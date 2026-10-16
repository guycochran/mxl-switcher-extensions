#!/usr/bin/env python3
"""Supervised paired Program Master to NDI output adapter."""

from __future__ import annotations

import argparse
import os
import pathlib
import signal
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Callable

from common.health import handler_for_snapshot
from common.runtime import ExtensionRuntime, RestartBudget
from outputs.ndi.ndi_av_probe import probe as verify_ndi_av


QUEUE_NS = 500_000_000
VIDEO_QUEUE_BUFFERS = 4
AUDIO_RATE = 48_000
AUDIO_CHANNELS = 2
REQUIRED_PLUGINS = ("mxlsrc", "audiobuffersplit", "ndisinkcombiner", "ndisink")


def _flow_name(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("flow identity must be one path component")
    return value


def gst_command(domain: str, video_flow: str, audio_flow: str, ndi_name: str) -> list[str]:
    """Build an argv-only paired A/V pipeline.

    `ndisinkcombiner` holds one video pad and one audio request pad and emits a
    paired raw stream to `ndisink`. No NDI SDK/runtime is redistributed here.
    """

    _flow_name(video_flow)
    _flow_name(audio_flow)
    if not ndi_name.strip():
        raise ValueError("NDI source name is required")
    return [
        "gst-launch-1.0", "-e", "-q",
        "ndisinkcombiner", "name=combiner", "!",
        "ndisink", f"ndi-name={ndi_name}",
        "mxlsrc", f"domain={domain}", f"video-flow-id={video_flow}",
        "automatic-eos=false", "!",
        "queue", f"max-size-time={QUEUE_NS}", "max-size-buffers=0",
        "max-size-bytes=0", "!",
        "videoconvert", "n-threads=4", "!",
        "video/x-raw,format=UYVY", "!",
        "queue", f"max-size-buffers={VIDEO_QUEUE_BUFFERS}",
        "max-size-time=0", "max-size-bytes=0", "leaky=downstream", "!",
        "combiner.video",
        "mxlsrc", f"domain={domain}", f"audio-flow-id={audio_flow}",
        "automatic-eos=false", "!",
        "queue", f"max-size-time={QUEUE_NS}", "max-size-buffers=0",
        "max-size-bytes=0", "!",
        "audioconvert", "!", "audioresample", "!",
        f"audio/x-raw,format=F32LE,rate={AUDIO_RATE},channels={AUDIO_CHANNELS},layout=interleaved", "!",
        "audiobuffersplit", "output-buffer-duration=1/30", "!",
        "queue", f"max-size-time={QUEUE_NS}", "max-size-buffers=0",
        "max-size-bytes=0", "!",
        "combiner.audio",
    ]


def plugin_preflight(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    for plugin in REQUIRED_PLUGINS:
        try:
            done = run(
                ["gst-inspect-1.0", plugin],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"required GStreamer plugin unavailable: {plugin}") from exc
        if done.returncode != 0:
            raise RuntimeError(f"required GStreamer plugin unavailable: {plugin}")


class NDIOutput:
    def __init__(
        self,
        args: argparse.Namespace,
        *,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        verifier: Callable[..., dict] = verify_ndi_av,
    ) -> None:
        self.args = args
        self.popen = popen
        self.clock = clock
        self.verifier = verifier
        self.runtime = ExtensionRuntime("program-ndi-output", "output")
        self.budget = RestartBudget(args.max_restarts, args.restart_window_seconds, clock=clock)
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.flow_inodes: tuple[int, int] | None = None
        self.stop_requested = False
        self.wire_verification: dict | None = None
        self.lock = threading.RLock()

    def _flow_dirs(self) -> tuple[pathlib.Path, pathlib.Path]:
        domain = self.args.domain
        return (
            domain / f"{_flow_name(self.args.video_flow)}.mxl-flow",
            domain / f"{_flow_name(self.args.audio_flow)}.mxl-flow",
        )

    def _inodes(self) -> tuple[int, int] | None:
        try:
            return tuple(path.stat().st_ino for path in self._flow_dirs())  # type: ignore[return-value]
        except OSError:
            return None

    def _metrics(self) -> dict:
        alive = self.process is not None and self.process.poll() is None
        return {
            "pipeline_alive": alive,
            "video_input_available": self._flow_dirs()[0].is_dir(),
            "audio_input_available": self._flow_dirs()[1].is_dir(),
            "paired_av": True,
            "wire_av_verified": bool(
                self.wire_verification and self.wire_verification.get("passed")
            ),
            "wire_video_buffers": 0 if not self.wire_verification else self.wire_verification.get("video_buffers", 0),
            "wire_audio_buffers": 0 if not self.wire_verification else self.wire_verification.get("audio_buffers", 0),
            "wire_video_fps": 0.0 if not self.wire_verification else self.wire_verification.get("video_fps_from_pts", 0.0),
            "wire_audio_sample_rate_hz": 0.0 if not self.wire_verification else self.wire_verification.get("audio_sample_rate_from_pts", 0.0),
            "audio_rate_hz": AUDIO_RATE,
            "audio_channels": AUDIO_CHANNELS,
            "queue_bound_ns": QUEUE_NS,
            "video_queue_buffers": VIDEO_QUEUE_BUFFERS,
            "uptime_seconds": 0.0 if self.started_at is None else round(self.clock() - self.started_at, 3),
            "restart_budget": self.budget.snapshot(),
        }

    def snapshot(self) -> dict:
        self.runtime.publish_metrics(self._metrics())
        return self.runtime.snapshot()

    def start(self, *, restart: bool = False) -> None:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("NDI output is already running")
            if restart:
                if not self.budget.claim():
                    self.runtime.transition("failed", error="restart budget exhausted")
                    raise RuntimeError("restart budget exhausted")
                self.runtime.record_restart()
                if self.runtime.snapshot()["state"] in {"ready", "running"}:
                    self.runtime.transition(
                        "degraded", ready=False, error="pipeline restart requested"
                    )
            self.runtime.transition("starting")
            inodes = self._inodes()
            if inodes is None:
                self.runtime.transition("failed", error="paired Program video/audio input is unavailable")
                raise RuntimeError("paired Program video/audio input is unavailable")
            command = gst_command(
                str(self.args.domain), self.args.video_flow, self.args.audio_flow, self.args.ndi_name
            )
            try:
                self.process = self.popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                self.runtime.transition("failed", error=f"pipeline launch failed: {exc}")
                raise
            self.flow_inodes = inodes
            self.started_at = self.clock()
            try:
                self.wire_verification = self.verifier(
                    self.args.verification_source_name,
                    self.args.verification_seconds,
                    self.args.verification_connect_timeout_ms,
                )
            except Exception as exc:
                self.wire_verification = {"passed": False, "error": str(exc)}
            if not self.wire_verification.get("passed"):
                process = self.process
                if process is not None and process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                self.runtime.transition("failed", error="NDI wire A/V verification failed")
                raise RuntimeError("NDI wire A/V verification failed")

    def stop(self) -> None:
        with self.lock:
            process = self.process
            self.stop_requested = True
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            self.runtime.transition("disabled")

    def check(self) -> str:
        """Return `healthy`, `wait`, `restart`, or `failed` without sleeping."""

        with self.lock:
            if self.stop_requested:
                return "failed"
            inodes = self._inodes()
            if inodes is None:
                self.runtime.transition("degraded", ready=False, error="paired Program input disappeared")
                return "wait"
            alive = self.process is not None and self.process.poll() is None
            if not alive:
                self.runtime.transition("degraded", ready=False, error="NDI pipeline exited")
                return "restart"
            if self.flow_inodes != inodes:
                self.runtime.transition("degraded", ready=False, error="Program flow was replaced")
                return "restart"
            if self.runtime.snapshot()["state"] == "starting":
                self.runtime.transition("ready")
            self.runtime.transition("running", ready=True)
            return "healthy"

    def supervise(self) -> None:
        self.start()
        while not self.stop_requested:
            time.sleep(self.args.poll_seconds)
            result = self.check()
            if result == "healthy":
                continue
            if result == "wait":
                continue
            if result == "failed":
                return
            process = self.process
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=self.args.stop_timeout_seconds)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            try:
                self.start(restart=True)
            except (OSError, RuntimeError):
                return


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=pathlib.Path, required=True)
    parser.add_argument("--video-flow", required=True)
    parser.add_argument("--audio-flow", required=True)
    parser.add_argument("--ndi-name", default="MXL Switcher Program")
    parser.add_argument(
        "--verification-source-name",
        required=True,
        help="full advertised NDI source name, for example HOST (MXL Switcher Program)",
    )
    parser.add_argument("--verification-seconds", type=float, default=3.0)
    parser.add_argument("--verification-connect-timeout-ms", type=int, default=10_000)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18120)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--stop-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--restart-window-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    _flow_name(args.video_flow)
    _flow_name(args.audio_flow)
    if args.bind not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("health bind must remain loopback-only")
    if (
        args.poll_seconds <= 0
        or args.stop_timeout_seconds <= 0
        or args.verification_seconds <= 0
        or args.verification_connect_timeout_ms <= 0
    ):
        parser.error("poll and stop timeouts must be positive")
    return args


def main() -> int:
    args = parse_args()
    plugin_preflight()
    adapter = NDIOutput(args)
    worker = threading.Thread(target=adapter.supervise, daemon=True)
    worker.start()
    server = ThreadingHTTPServer((args.bind, args.port), handler_for_snapshot(adapter.snapshot))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        adapter.stop()
        server.server_close()
        worker.join(timeout=args.stop_timeout_seconds + 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
