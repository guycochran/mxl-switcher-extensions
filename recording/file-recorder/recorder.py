#!/usr/bin/env python3
"""Supervised Program Master file recorder.

The recorder is deliberately outside Core. It launches one bounded GStreamer
consumer, exposes loopback-only lifecycle/health HTTP, and atomically promotes
the partial Matroska file only after GStreamer has finalized it.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import signal
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from typing import Any

from common.health import handler_for_snapshot


QUEUE_NS = 2_000_000_000
MEDIA_START_BYTES = 4096


def media_file_started(path: pathlib.Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.stat().st_size >= MEDIA_START_BYTES
    except OSError:
        return False


def gst_command(
    domain: str,
    video_flow: str,
    audio_flow: str,
    output: pathlib.Path,
    video_bitrate_kbps: int,
    audio_bitrate_bps: int,
) -> list[str]:
    """Build an argv-only pipeline so flow IDs and paths never reach a shell."""

    return [
        "gst-launch-1.0", "-e", "-q",
        "matroskamux", "name=mux", "writing-app=MXL-Program-File-Recorder",
        "!", "filesink", f"location={output}",
        "mxlsrc", f"domain={domain}", f"video-flow-id={video_flow}",
        "automatic-eos=false", "!",
        "queue", f"max-size-time={QUEUE_NS}", "max-size-buffers=0",
        "max-size-bytes=0", "!",
        "videoconvert", "n-threads=4", "!", "video/x-raw,format=I420", "!",
        "queue", "max-size-buffers=4", "max-size-bytes=0", "max-size-time=0", "!",
        "x264enc", "tune=zerolatency", "speed-preset=ultrafast",
        "threads=8", "sliced-threads=true", f"bitrate={video_bitrate_kbps}",
        "key-int-max=60", "bframes=0", "!",
        "h264parse", "!", "queue", "!", "mux.video_0",
        "mxlsrc", f"domain={domain}", f"audio-flow-id={audio_flow}",
        "automatic-eos=false", "!",
        "queue", f"max-size-time={QUEUE_NS}", "max-size-buffers=0",
        "max-size-bytes=0", "!",
        "audioconvert", "!", "audioresample", "!",
        "audio/x-raw,rate=48000,channels=2", "!",
        "avenc_aac", f"bitrate={audio_bitrate_bps}", "!",
        "aacparse", "!", "queue", "!", "mux.audio_0",
    ]


class Recorder:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.RLock()
        self.process: subprocess.Popen[bytes] | None = None
        self.started_monotonic: float | None = None
        self.partial_path: pathlib.Path | None = None
        self.final_path: pathlib.Path | None = None
        self.state = "ready"
        self.revision = 0
        self.restarts = 0
        self.last_error: str | None = None
        self.finalization_state = "idle"
        self.stop_requested = False
        self.shutdown_requested = False
        self.last_wall_duration_seconds = 0.0
        self.media_started_monotonic: float | None = None
        self.last_capture_wall_seconds = 0.0
        self.failure_requested = False

    def _recording_paths(self) -> tuple[pathlib.Path, pathlib.Path]:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        base = self.args.recordings_dir / f"program-{stamp}-{time.time_ns() % 1_000_000_000:09d}"
        return base.with_suffix(".partial.mkv"), base.with_suffix(".mkv")

    def start(self) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("recording is already running")
            self.args.recordings_dir.mkdir(parents=True, exist_ok=True)
            partial, final = self._recording_paths()
            command = gst_command(
                self.args.domain,
                self.args.video_flow,
                self.args.audio_flow,
                partial,
                self.args.video_bitrate_kbps,
                self.args.audio_bitrate_bps,
            )
            self.state = "starting"
            self.finalization_state = "idle"
            self.last_error = None
            self.stop_requested = False
            self.last_wall_duration_seconds = 0.0
            self.media_started_monotonic = None
            self.last_capture_wall_seconds = 0.0
            self.failure_requested = False
            try:
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                self.state = "failed"
                self.last_error = str(exc)
                self.revision += 1
                raise
            self.partial_path = partial
            self.final_path = final
            self.started_monotonic = time.monotonic()
            self.state = "running"
            self.revision += 1
            threading.Thread(target=self._watch, daemon=True).start()

    def stop(self) -> None:
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                raise RuntimeError("recording is not running")
            self.stop_requested = True
            self.finalization_state = "finalizing"
            # gst-launch -e handles SIGINT by sending EOS before exiting.
            os.killpg(process.pid, signal.SIGINT)
            self.revision += 1

    def _watch(self) -> None:
        assert self.process is not None
        process = self.process
        stderr = process.communicate()[1].decode("utf-8", errors="replace")[-4000:]
        with self.lock:
            if self.started_monotonic is not None:
                self.last_wall_duration_seconds = max(
                    0.0, time.monotonic() - self.started_monotonic
                )
            if self.media_started_monotonic is not None:
                self.last_capture_wall_seconds = max(
                    0.0, time.monotonic() - self.media_started_monotonic
                )
            # gst-launch handles SIGINT by sending EOS when invoked with -e,
            # then retains the signal return code (-SIGINT) even though the
            # muxer closed the file cleanly.
            clean = (
                self.stop_requested
                and not self.failure_requested
                and process.returncode in {0, -signal.SIGINT}
                and self.partial_path is not None
                and self.partial_path.is_file()
                and self.partial_path.stat().st_size > 0
            )
            if clean and self.partial_path and self.final_path:
                try:
                    self.partial_path.replace(self.final_path)
                    self.finalization_state = "complete"
                    self.state = "ready"
                except OSError as exc:
                    self.finalization_state = "failed"
                    self.state = "failed"
                    self.last_error = f"finalize failed: {exc}"
            else:
                self.finalization_state = "failed"
                self.state = "failed"
                if not self.last_error:
                    self.last_error = (
                        stderr.strip() or f"recorder exited {process.returncode}"
                    )
            self.revision += 1

    def enforce_duration(self) -> None:
        while not self.shutdown_requested:
            time.sleep(0.25)
            with self.lock:
                running = self.process is not None and self.process.poll() is None
                now = time.monotonic()
                launch_elapsed = (
                    0.0
                    if self.started_monotonic is None
                    else now - self.started_monotonic
                )
                if (
                    running
                    and self.media_started_monotonic is None
                    and media_file_started(self.partial_path)
                ):
                    self.media_started_monotonic = now
                    self.revision += 1
                capture_elapsed = (
                    0.0
                    if self.media_started_monotonic is None
                    else now - self.media_started_monotonic
                )
                startup_timed_out = (
                    running
                    and self.media_started_monotonic is None
                    and launch_elapsed >= self.args.startup_timeout_seconds
                )
                if startup_timed_out:
                    self.failure_requested = True
                    self.state = "failed"
                    self.finalization_state = "failed"
                    self.last_error = (
                        "no media reached the recorder within "
                        f"{self.args.startup_timeout_seconds}s"
                    )
                    assert self.process is not None
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.revision += 1
            if (
                running
                and self.media_started_monotonic is not None
                and self.args.max_duration_seconds > 0
                and capture_elapsed >= self.args.max_duration_seconds
            ):
                try:
                    self.stop()
                except RuntimeError:
                    pass

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            elapsed = (
                0.0
                if self.started_monotonic is None
                else max(0.0, time.monotonic() - self.started_monotonic)
            )
            capture_elapsed = (
                0.0
                if self.media_started_monotonic is None
                else max(0.0, time.monotonic() - self.media_started_monotonic)
            )
            path = self.partial_path if running else self.final_path
            size = 0
            if path:
                try:
                    size = path.stat().st_size
                except OSError:
                    pass
            disk = shutil.disk_usage(self.args.recordings_dir if self.args.recordings_dir.exists() else self.args.recordings_dir.parent)
            return {
                "name": "program-file-recorder",
                "kind": "recorder",
                "state": self.state,
                "ready": self.state in {"ready", "running"},
                "revision": self.revision,
                "restarts": self.restarts,
                "last_error": self.last_error,
                "metrics": {
                    "recording": running,
                    "wall_duration_seconds": round(
                        elapsed if running else self.last_wall_duration_seconds, 3
                    ),
                    "capture_wall_seconds": round(
                        capture_elapsed if running else self.last_capture_wall_seconds,
                        3,
                    ),
                    "media_duration_seconds": None,
                    "bytes": size,
                    "disk_free_bytes": disk.free,
                    "finalization": self.finalization_state,
                    "file": None if path is None else path.name,
                    "queue_bound_ns": QUEUE_NS,
                    "video_convert_threads": 4,
                    "video_encode_threads": 8,
                    "audio_rate_hz": 48000,
                    "av_skew_ns": None,
                    "drops": None,
                },
            }


def handler_for(recorder: Recorder):
    class Handler(handler_for_snapshot(recorder.snapshot)):

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path == "/api/v1/start":
                    recorder.start()
                elif self.path == "/api/v1/stop":
                    recorder.stop()
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
            except RuntimeError as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
                return
            except OSError as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            self._json(HTTPStatus.ACCEPTED, recorder.snapshot())

    return Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="/mxl-domain")
    parser.add_argument("--video-flow", required=True)
    parser.add_argument("--audio-flow", required=True)
    parser.add_argument("--recordings-dir", type=pathlib.Path, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18110)
    parser.add_argument("--video-bitrate-kbps", type=int, default=12000)
    parser.add_argument("--audio-bitrate-bps", type=int, default=192000)
    parser.add_argument("--max-duration-seconds", type=int, default=3600)
    parser.add_argument("--startup-timeout-seconds", type=int, default=30)
    parser.add_argument("--autostart", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.bind not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("refusing non-loopback bind; put authenticated control in front")
    recorder = Recorder(args)
    threading.Thread(target=recorder.enforce_duration, daemon=True).start()
    if args.autostart:
        recorder.start()
    server = ThreadingHTTPServer((args.bind, args.port), handler_for(recorder))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        with recorder.lock:
            running = recorder.process is not None and recorder.process.poll() is None
        if running:
            recorder.stop()
            recorder.process.wait(timeout=15)
    finally:
        recorder.shutdown_requested = True
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
