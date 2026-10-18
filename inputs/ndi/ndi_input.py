#!/usr/bin/env python3
"""Supervised NDI source to paired MXL video/audio input module."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Callable

# Support both the container layout (/app/ndi_input.py plus /app/common) and
# direct execution from a repository checkout.
if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from common.health import handler_for_snapshot
from common.runtime import ExtensionRuntime, RestartBudget


VIDEO_QUEUE_BUFFERS = 4
BANK_VIDEO_QUEUE_BUFFERS = 12
BANK_VIDEO_QUEUE_PREROLL = 6
BANK_PACING_QUEUE_BUFFERS = 30
BANK_PACING_QUEUE_PREROLL = 20
AUDIO_QUEUE_NS = 200_000_000
AUDIO_RATE = 48_000
FLOW_ACTIVE_SECONDS = 60.0
ALLOWED_CHANNELS = (1, 2, 4, 8)
TIMESTAMP_MODES = ("receive-time", "timestamp", "timecode", "clocked")
REQUIRED_PLUGINS = (
    "ndisrc", "ndisrcdemux", "queue", "valve", "watchdog", "compositor", "videorate", "videoscale",
    "videoconvert", "identity", "clocksync", "audioconvert", "audioresample", "audiorate", "mxlsink",
)


def _flow_name(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("flow identity must be one path component")
    return value


def _fraction(numerator: int, denominator: int) -> str:
    if numerator <= 0 or denominator <= 0:
        raise ValueError("rate numerator and denominator must be positive")
    return f"{numerator}/{denominator}"


def _v210_datarate(width: int, height: int, numerator: int, denominator: int) -> int:
    """Return bytes/s for GStreamer's 128-byte-aligned v210 frame layout."""

    stride = ((width + 47) // 48) * 128
    value = round(stride * height * numerator / denominator)
    if not 1 <= value <= 2_147_483_647:
        raise ValueError("v210 data rate is outside GStreamer identity's supported range")
    return value


def gst_command(
    *,
    domain: str,
    source_name: str | None,
    source_address: str | None,
    video_flow: str,
    audio_flow: str | None,
    audio_mode: str,
    width: int,
    height: int,
    rate_numerator: int,
    rate_denominator: int,
    rate_mode: str,
    timestamp_mode: str,
    pacing_mode: str,
    convert_threads: int,
    audio_channels: int,
    branch_name: str = "demux",
    startup_gate: bool = False,
) -> list[str]:
    """Build the optimized argv-only NDI-to-MXL pipeline."""

    _flow_name(video_flow)
    if audio_flow is not None:
        _flow_name(audio_flow)
    source_name = None if source_name is None else source_name.strip()
    source_address = None if source_address is None else source_address.strip()
    if bool(source_name) == bool(source_address):
        raise ValueError("configure exactly one NDI source name or direct address")
    if audio_mode not in {"optional", "required", "disabled"}:
        raise ValueError("unknown audio mode")
    if audio_mode != "disabled" and audio_flow is None:
        raise ValueError("audio flow is required unless audio is disabled")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if timestamp_mode not in TIMESTAMP_MODES:
        raise ValueError("unknown timestamp mode")
    if rate_mode not in {"normalize", "passthrough"}:
        raise ValueError("unknown rate mode")
    if pacing_mode not in {"none", "clock-sync", "retimestamp-clock-sync"}:
        raise ValueError("unknown pacing mode")
    if not 1 <= convert_threads <= 32:
        raise ValueError("convert threads must be between 1 and 32")
    if audio_channels not in ALLOWED_CHANNELS:
        raise ValueError("audio channels must be 1, 2, 4, or 8")

    rate = _fraction(rate_numerator, rate_denominator)
    source = ["ndisrc"]
    if source_name is not None:
        source.append(f"ndi-name={source_name}")
    else:
        source.append(f"url-address={source_address}")
    if not branch_name.replace("_", "").isalnum():
        raise ValueError("invalid branch name")
    source.extend([f"timestamp-mode={timestamp_mode}", "!", "ndisrcdemux", f"name={branch_name}"])

    queue_buffers = BANK_VIDEO_QUEUE_BUFFERS if startup_gate else VIDEO_QUEUE_BUFFERS
    command = [
        "gst-launch-1.0", "-e", "-q", *source,
        f"{branch_name}.video", "!",
        "queue", f"max-size-buffers={queue_buffers}",
        "max-size-time=0", "max-size-bytes=0", "leaky=downstream",
    ]
    if startup_gate:
        command.append(f"min-threshold-buffers={BANK_VIDEO_QUEUE_PREROLL}")
    command.append("!")
    if startup_gate:
        command.extend([
            "watchdog", f"name=watchdog_{branch_name}", "timeout=30000", "!",
            "identity", f"name=startup_probe_{branch_name}", "!",
        ])
    if rate_mode == "normalize":
        if startup_gate:
            # A live aggregator is the bank's frame synchronizer: after the
            # first camera frame it emits fixed-cadence output and repeats the
            # last picture through bounded NDI arrival stalls.
            command.extend([
                "compositor", f"name=frame_sync_{branch_name}",
                "background=black", "force-live=true", "ignore-inactive-pads=true",
                "latency=200000000", "max-threads=2", "!",
                f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1,framerate={rate}", "!",
            ])
        else:
            # Normalize cadence before the expensive scale and v210 conversion.
            command.extend([
                "videorate", "!", f"video/x-raw,framerate={rate}", "!",
                "videoscale", "add-borders=true", "!",
                f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1", "!",
            ])
    else:
        # A sender already matching the target geometry and cadence should not
        # pay for videorate or a no-op videoscale on constrained CPU hosts.
        command.extend([
            f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1,framerate={rate}", "!",
        ])
    if startup_gate:
        command.extend(["valve", f"name=startup_gate_{branch_name}", "drop=true", "!"])
    command.extend([
        "videoconvert", f"n-threads={convert_threads}", "!",
        "video/x-raw,format=v210", "!",
    ])
    if pacing_mode == "retimestamp-clock-sync":
        command.extend([
            "identity",
            *([f"name=timestamp_sync_{branch_name}"] if startup_gate else []),
            f"datarate={_v210_datarate(width, height, rate_numerator, rate_denominator)}",
            "single-segment=true",
            "!",
        ])
        if startup_gate:
            command.extend([
                "queue", f"name=pacing_queue_{branch_name}",
                f"max-size-buffers={BANK_PACING_QUEUE_BUFFERS}",
                f"min-threshold-buffers={BANK_PACING_QUEUE_PREROLL}",
                "max-size-time=0", "max-size-bytes=0", "!",
            ])
    if pacing_mode in {"clock-sync", "retimestamp-clock-sync"}:
        if startup_gate:
            command.extend(["clocksync", f"name=clock_sync_{branch_name}", "sync-to-first=false", "!"])
        else:
            command.extend(["clocksync", "sync-to-first=true", "!"])
    command.extend(["mxlsink", f"flow-id={video_flow}", f"domain={domain}"])
    if audio_mode != "disabled":
        mask = hex((1 << audio_channels) - 1)
        command.extend([
            f"{branch_name}.audio", "!",
            "queue", f"max-size-time={AUDIO_QUEUE_NS}",
            "max-size-buffers=0", "max-size-bytes=0", "!",
            "audioconvert", "mix-matrix=<>", "!",
            "audioresample", "!",
            f"audio/x-raw,format=F32LE,layout=interleaved,rate={AUDIO_RATE},channels={audio_channels},channel-mask=(bitmask){mask}", "!",
            "audiorate", "skip-to-first=true", "!",
            "mxlsink", f"flow-id={audio_flow}", f"domain={domain}",
        ])
    return command


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


class NDIInput:
    def __init__(
        self,
        args: argparse.Namespace,
        *,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.args = args
        self.popen = popen
        self.clock = clock
        self.wall_clock = wall_clock
        self.runtime = ExtensionRuntime("ndi-mxl-input", "input")
        self.budget = RestartBudget(args.max_restarts, args.restart_window_seconds, clock=clock)
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.stop_requested = False
        self.lock = threading.RLock()

    def _flow_dir(self, flow: str | None) -> pathlib.Path | None:
        return None if flow is None else self.args.domain / f"{_flow_name(flow)}.mxl-flow"

    def _available(self, flow: str | None, media_type: str) -> bool:
        path = self._flow_dir(flow)
        if path is None:
            return False
        try:
            definition = json.loads((path / "flow_def.json").read_text(encoding="utf-8"))
            data = path / "data"
            return (
                str(definition.get("id")) == flow
                and definition.get("media_type") == media_type
                and data.is_file()
                and self.wall_clock() - data.stat().st_mtime <= FLOW_ACTIVE_SECONDS
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _metrics(self) -> dict:
        alive = self.process is not None and self.process.poll() is None
        return {
            "pipeline_alive": alive,
            "video_output_available": self._available(self.args.video_flow, "video/v210"),
            "audio_output_available": self._available(self.args.audio_flow, "audio/float32"),
            "flow_active_window_seconds": FLOW_ACTIVE_SECONDS,
            "audio_mode": self.args.audio_mode,
            "target_width": self.args.width,
            "target_height": self.args.height,
            "target_rate_numerator": self.args.rate_numerator,
            "target_rate_denominator": self.args.rate_denominator,
            "rate_mode": self.args.rate_mode,
            "timestamp_mode": self.args.timestamp_mode,
            "pacing_mode": self.args.pacing_mode,
            "pipeline_clock": "system",
            "convert_threads": self.args.convert_threads,
            "audio_rate_hz": AUDIO_RATE,
            "audio_channels": self.args.audio_channels,
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
                raise RuntimeError("NDI input is already running")
            self.runtime.transition("starting")
            if not self.args.domain.is_dir():
                self.runtime.transition("failed", error="MXL domain is unavailable")
                raise RuntimeError("MXL domain is unavailable")
            if restart:
                if not self.budget.claim():
                    self.runtime.transition("failed", error="restart budget exhausted")
                    raise RuntimeError("restart budget exhausted")
                self.runtime.record_restart()
            command = gst_command(
                domain=str(self.args.domain),
                source_name=self.args.source_name,
                source_address=self.args.source_address,
                video_flow=self.args.video_flow,
                audio_flow=self.args.audio_flow,
                audio_mode=self.args.audio_mode,
                width=self.args.width,
                height=self.args.height,
                rate_numerator=self.args.rate_numerator,
                rate_denominator=self.args.rate_denominator,
                rate_mode=self.args.rate_mode,
                timestamp_mode=self.args.timestamp_mode,
                pacing_mode=self.args.pacing_mode,
                convert_threads=self.args.convert_threads,
                audio_channels=self.args.audio_channels,
            )
            command = [
                sys.executable,
                str(pathlib.Path(__file__).with_name("ndi_pipeline.py")),
                "--",
                *command[3:],
            ]
            try:
                self.process = self.popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as exc:
                self.runtime.transition("failed", error=f"pipeline launch failed: {exc}")
                raise
            self.started_at = self.clock()

    def stop(self) -> None:
        with self.lock:
            self.stop_requested = True
            process = self.process
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self.runtime.transition("disabled")

    def check(self) -> str:
        """Return healthy, wait, restart, or failed without sleeping."""

        with self.lock:
            if self.stop_requested:
                return "failed"
            if self.process is None or self.process.poll() is not None:
                self.runtime.transition("degraded", ready=False, error="NDI pipeline exited")
                return "restart"
            video_available = self._available(self.args.video_flow, "video/v210")
            audio_required = self.args.audio_mode == "required"
            audio_available = self._available(self.args.audio_flow, "audio/float32")
            if not video_available or (audio_required and not audio_available):
                self.runtime.transition("degraded", ready=False, error="configured MXL output is unavailable")
                return "wait"
            if self.runtime.snapshot()["state"] in {"starting", "degraded"}:
                self.runtime.transition("ready")
            self.runtime.transition("running", ready=True)
            return "healthy"

    def _terminate_for_restart(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=self.args.stop_timeout_seconds)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    def supervise(self) -> None:
        self.start()
        while not self.stop_requested:
            time.sleep(self.args.poll_seconds)
            result = self.check()
            if result in {"healthy", "wait"}:
                continue
            if result == "failed":
                return
            self._terminate_for_restart()
            try:
                self.start(restart=True)
            except (OSError, RuntimeError):
                return


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=pathlib.Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-name")
    source.add_argument("--source-address")
    parser.add_argument("--video-flow", required=True)
    parser.add_argument("--audio-flow")
    parser.add_argument("--audio-mode", choices=("optional", "required", "disabled"), default="optional")
    parser.add_argument("--audio-channels", type=int, choices=ALLOWED_CHANNELS, default=2)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rate-numerator", type=int, default=30)
    parser.add_argument("--rate-denominator", type=int, default=1)
    parser.add_argument(
        "--rate-mode",
        choices=("normalize", "passthrough"),
        default="normalize",
        help="normalize variable-rate sources or pass through a sender already at the target cadence",
    )
    parser.add_argument(
        "--timestamp-mode",
        choices=TIMESTAMP_MODES,
        required=True,
        help="choose from measured sender behavior; no universal default is assumed",
    )
    parser.add_argument(
        "--pacing-mode",
        choices=("none", "clock-sync", "retimestamp-clock-sync"),
        default="none",
    )
    parser.add_argument("--convert-threads", type=int, default=4)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18130)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--stop-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--restart-window-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    _flow_name(args.video_flow)
    if args.audio_flow is not None:
        _flow_name(args.audio_flow)
    if args.audio_mode != "disabled" and args.audio_flow is None:
        parser.error("--audio-flow is required unless --audio-mode disabled")
    if args.bind not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("health bind must remain loopback-only")
    if args.width <= 0 or args.height <= 0:
        parser.error("width and height must be positive")
    if args.rate_numerator <= 0 or args.rate_denominator <= 0:
        parser.error("rate numerator and denominator must be positive")
    if not 1 <= args.convert_threads <= 32:
        parser.error("convert threads must be between 1 and 32")
    if args.poll_seconds <= 0 or args.stop_timeout_seconds <= 0:
        parser.error("poll and stop timeouts must be positive")
    return args


def main() -> int:
    args = parse_args()
    plugin_preflight()
    module = NDIInput(args)
    worker = threading.Thread(target=module.supervise, daemon=True)
    worker.start()
    server = ThreadingHTTPServer((args.bind, args.port), handler_for_snapshot(module.snapshot))

    def request_shutdown(_signum: int, _frame: object) -> None:
        # BaseServer.shutdown must be called from a different thread than
        # serve_forever. Docker's SIGTERM therefore schedules a bounded stop.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    try:
        server.serve_forever()
    finally:
        module.stop()
        server.server_close()
        worker.join(timeout=args.stop_timeout_seconds + 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
