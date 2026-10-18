#!/usr/bin/env python3
"""Supervise 2-8 NDI inputs in one clock-aligned MXL writer pipeline."""

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

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from common.health import handler_for_snapshot
from common.runtime import ExtensionRuntime, RestartBudget
from inputs.ndi.ndi_input import _flow_name, gst_command, plugin_preflight

HISTORY_OPTION = "urn:x-mxl:option:history_duration/v1.0"
MINIMUM_HISTORY_NS = 1_000_000_000
NATIVE_BANK_PLUGINS = (
    "ndisrc", "ndisrcdemux", "queue", "watchdog", "videorate",
    "videoscale", "videoconvert", "appsink",
)


def validate_domain_history(domain: pathlib.Path, minimum_ns: int) -> None:
    try:
        options = json.loads((domain / "options.json").read_text(encoding="utf-8"))
        configured = options.get(HISTORY_OPTION)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("MXL domain options.json is missing or invalid") from exc
    if not isinstance(configured, (int, float)) or isinstance(configured, bool) or configured < minimum_ns:
        raise RuntimeError(
            f"MXL domain history duration must be at least {minimum_ns} ns"
        )


class NDIBank:
    def __init__(self, args: argparse.Namespace, sources: list[dict], *, popen=subprocess.Popen, clock=time.monotonic):
        self.args, self.sources, self.popen, self.clock = args, sources, popen, clock
        self.runtime = ExtensionRuntime("ndi-mxl-input-bank", "input-bank")
        self.budget = RestartBudget(args.max_restarts, args.restart_window_seconds, clock=clock)
        self.process = None
        self.started_at = None
        self.stop_requested = False
        self.lock = threading.RLock()

    def _available(self, source: dict) -> bool:
        checks = [(source["video_flow"], "video/v210")]
        if source["audio_mode"] == "required":
            checks.append((source["audio_flow"], "audio/float32"))
        for flow, media_type in checks:
            path = self.args.domain / f"{flow}.mxl-flow"
            try:
                definition = json.loads((path / "flow_def.json").read_text())
                if definition.get("id") != flow or definition.get("media_type") != media_type or not (path / "data").is_file():
                    return False
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return False
        return True

    def snapshot(self) -> dict:
        alive = self.process is not None and self.process.poll() is None
        ready_count = sum(self._available(source) for source in self.sources)
        self.runtime.publish_metrics({
            "pipeline_alive": alive, "pipeline_clock": "system",
            "configured_inputs": len(self.sources), "ready_inputs": ready_count,
            "writer_mode": self.args.writer_mode,
            "timestamp_mode": self.args.timestamp_mode, "pacing_mode": self.args.pacing_mode,
            "rate_mode": self.args.rate_mode, "target_width": self.args.width,
            "timing_report_seconds": getattr(self.args, "timing_report_seconds", 0),
            "target_height": self.args.height, "target_rate_numerator": self.args.rate_numerator,
            "target_rate_denominator": self.args.rate_denominator,
            "uptime_seconds": 0.0 if self.started_at is None else round(self.clock() - self.started_at, 3),
            "restart_budget": self.budget.snapshot(),
        })
        return self.runtime.snapshot()

    def _command(self) -> list[str]:
        if self.args.writer_mode == "native":
            if any(source["audio_mode"] != "disabled" for source in self.sources):
                raise RuntimeError("native writer currently supports video-only banks")
            command = [
                "/usr/local/bin/ndi-mxl-bank",
                "--domain", str(self.args.domain),
                "--source-count", str(len(self.sources)),
                "--width", str(self.args.width), "--height", str(self.args.height),
                "--rate-numerator", str(self.args.rate_numerator),
                "--rate-denominator", str(self.args.rate_denominator),
                "--convert-threads", str(self.args.convert_threads),
                "--timestamp-mode", self.args.timestamp_mode,
            ]
            # Opt-in, secret-free per-stage scheduler timing on the child's stdout.
            report = getattr(self.args, "timing_report_seconds", 0)
            if report:
                command.extend(["--timing-report-seconds", str(report)])
            return command
        tokens: list[str] = []
        for index, source in enumerate(self.sources):
            command = gst_command(
                domain=str(self.args.domain), source_name=source.get("source_name"),
                source_address=source.get("source_address"), video_flow=source["video_flow"],
                audio_flow=source.get("audio_flow"), audio_mode=source["audio_mode"],
                width=self.args.width, height=self.args.height,
                rate_numerator=self.args.rate_numerator, rate_denominator=self.args.rate_denominator,
                rate_mode=self.args.rate_mode, timestamp_mode=self.args.timestamp_mode,
                pacing_mode=self.args.pacing_mode, convert_threads=self.args.convert_threads,
                audio_channels=source.get("audio_channels", 2), branch_name=f"demux{index}",
                startup_gate=True,
            )
            tokens.extend(command[3:])
        return [
            sys.executable, str(pathlib.Path(__file__).with_name("ndi_pipeline.py")),
            "--rate-numerator", str(self.args.rate_numerator),
            "--rate-denominator", str(self.args.rate_denominator),
            "--startup-lead-frames", "2",
            "--", *tokens,
        ]

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        if self.args.writer_mode != "native":
            return environment
        for index, source in enumerate(self.sources):
            prefix = f"NDI_BANK_SOURCE_{index}"
            if source.get("source_name"):
                environment[f"{prefix}_TYPE"] = "name"
                environment[f"{prefix}_VALUE"] = source["source_name"]
            else:
                environment[f"{prefix}_TYPE"] = "address"
                environment[f"{prefix}_VALUE"] = source["source_address"]
            environment[f"{prefix}_FLOW"] = source["video_flow"]
        return environment

    def start(self, *, restart=False) -> None:
        with self.lock:
            self.runtime.transition("starting")
            if not self.args.domain.is_dir():
                self.runtime.transition("failed", error="MXL domain is unavailable")
                raise RuntimeError("MXL domain is unavailable")
            validate_domain_history(
                self.args.domain,
                getattr(self.args, "minimum_history_ns", MINIMUM_HISTORY_NS),
            )
            if restart:
                if not self.budget.claim():
                    self.runtime.transition("failed", error="restart budget exhausted")
                    raise RuntimeError("restart budget exhausted")
                self.runtime.record_restart()
            self.process = self.popen(
                self._command(), stdin=subprocess.DEVNULL,
                start_new_session=True, env=self._environment(),
            )
            self.started_at = self.clock()

    def check(self) -> str:
        with self.lock:
            if self.stop_requested:
                return "failed"
            if self.process is None or self.process.poll() is not None:
                self.runtime.transition("degraded", ready=False, error="NDI bank pipeline exited")
                return "restart"
            if not all(self._available(source) for source in self.sources):
                self.runtime.transition("degraded", ready=False, error="configured MXL outputs are unavailable")
                return "wait"
            if self.runtime.snapshot()["state"] in {"starting", "degraded"}:
                self.runtime.transition("ready")
            self.runtime.transition("running", ready=True)
            return "healthy"

    def _terminate(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=self.args.stop_timeout_seconds)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)

    def stop(self) -> None:
        with self.lock:
            self.stop_requested = True
            self._terminate()
            self.runtime.transition("disabled")

    def supervise(self) -> None:
        self.start()
        while not self.stop_requested:
            time.sleep(self.args.poll_seconds)
            result = self.check()
            if result in {"healthy", "wait"}:
                continue
            if result == "failed":
                return
            self._terminate()
            try:
                self.start(restart=True)
            except (OSError, RuntimeError):
                return


def load_sources(path: pathlib.Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    sources = value.get("sources") if isinstance(value, dict) else None
    if not isinstance(sources, list) or not 2 <= len(sources) <= 8:
        raise ValueError("config must contain 2-8 sources")
    ids, flows = set(), set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not source["id"]:
            raise ValueError("each source requires an id")
        if source["id"] in ids:
            raise ValueError("source ids must be unique")
        ids.add(source["id"])
        if bool(source.get("source_name")) == bool(source.get("source_address")):
            raise ValueError("each source requires exactly one name or address")
        source.setdefault("audio_mode", "disabled")
        if source["audio_mode"] not in {"disabled", "optional", "required"}:
            raise ValueError("invalid audio mode")
        for key in ("video_flow", "audio_flow"):
            flow = source.get(key)
            if key == "audio_flow" and source["audio_mode"] == "disabled" and flow is None:
                continue
            _flow_name(flow)
            if flow in flows:
                raise ValueError("flow ids must be unique")
            flows.add(flow)
    return sources


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", type=pathlib.Path, required=True)
    parser.add_argument("--config", type=pathlib.Path, required=True)
    parser.add_argument("--width", type=int, default=1920); parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--rate-numerator", type=int, default=30); parser.add_argument("--rate-denominator", type=int, default=1)
    parser.add_argument("--rate-mode", choices=("normalize", "passthrough"), default="passthrough")
    parser.add_argument("--timestamp-mode", choices=("receive-time", "timestamp", "timecode", "clocked"), default="receive-time")
    parser.add_argument("--pacing-mode", choices=("none", "clock-sync", "retimestamp-clock-sync"), default="retimestamp-clock-sync")
    parser.add_argument("--writer-mode", choices=("native", "gstreamer"), default="native")
    parser.add_argument("--convert-threads", type=int, default=4)
    parser.add_argument("--bind", default="127.0.0.1"); parser.add_argument("--port", type=int, default=18130)
    parser.add_argument("--poll-seconds", type=float, default=1.0); parser.add_argument("--stop-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--max-restarts", type=int, default=3); parser.add_argument("--restart-window-seconds", type=float, default=60.0)
    parser.add_argument("--minimum-history-ns", type=int, default=MINIMUM_HISTORY_NS)
    parser.add_argument(
        "--timing-report-seconds", type=int, default=0,
        help="native writer only: print per-stage scheduler timing every N seconds (0 = off)",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.timing_report_seconds <= 3600:
        parser.error("timing report interval must be 0-3600 seconds")
    if args.timing_report_seconds and args.writer_mode != "native":
        parser.error("timing reports require the native writer")
    if args.bind not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("health bind must remain loopback-only")
    if args.minimum_history_ns < MINIMUM_HISTORY_NS:
        parser.error(f"minimum history must be at least {MINIMUM_HISTORY_NS} ns")
    return args


def main() -> int:
    args = parse_args(); sources = load_sources(args.config)
    if args.writer_mode == "native":
        plugin_preflight(plugins=NATIVE_BANK_PLUGINS)
    else:
        plugin_preflight()
    bank = NDIBank(args, sources)
    threading.Thread(target=bank.supervise, daemon=True).start()
    server = ThreadingHTTPServer((args.bind, args.port), handler_for_snapshot(bank.snapshot))
    def shutdown(*_unused): threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown); signal.signal(signal.SIGINT, shutdown)
    try: server.serve_forever()
    finally: bank.stop(); server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
