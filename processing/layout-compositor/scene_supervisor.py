#!/usr/bin/env python3
"""Keep one MXL layout bank warm and replace only its off-air scene child."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from layout_compositor import SUPPORTED_LAYOUTS


class SceneEngine:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.RLock()
        self.configure_lock = threading.Lock()
        self.child: subprocess.Popen[str] | None = None
        self.generation = 0
        self.ready = False
        self.scene = {"count": 0, "sources": []}
        self.last_ready_ms = 0
        self.last_error = ""
        self.output_ticks = 0

    def _validate(self, count: int, sources: list[int]) -> None:
        if count not in SUPPORTED_LAYOUTS or len(sources) != count:
            raise ValueError("layout must contain 2, 3, 4, 6, or 8 sources")
        if len(set(sources)) != count or any(source < 1 or source > len(self.args.input) for source in sources):
            raise ValueError(f"sources must be unique numbers from 1 through {len(self.args.input)}")

    def _command(self, sources: list[int]) -> list[str]:
        command = [sys.executable, self.args.compositor, "--domain", self.args.domain]
        for source in sources:
            command.extend(("--input", self.args.input[source - 1]))
        command.extend((
            "--output-flow-id", self.args.output_flow_id,
            "--output-label", f"MXL LAYOUT {self.args.bank.upper()}",
            "--width", str(self.args.width), "--height", str(self.args.height),
            "--rate", self.args.rate, "--health-interval", "0.25",
        ))
        return command

    def _copy_output(self, child: subprocess.Popen[str], generation: int) -> None:
        assert child.stdout is not None
        for line in child.stdout:
            print(line, end="", flush=True)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self.lock:
                if generation != self.generation:
                    continue
                if event.get("event") == "health":
                    self.output_ticks = int(event.get("output_ticks", 0))
                    self.ready = self.output_ticks > 0
                elif event.get("event") in {"fatal", "output_eos"}:
                    self.last_error = line.strip()
                    self.ready = False

    def configure(self, count: int, sources: list[int]) -> dict[str, object]:
        self._validate(count, sources)
        with self.configure_lock:
            started = time.monotonic()
            with self.lock:
                if self.ready and self.scene == {"count": count, "sources": sources}:
                    result = self.state()
                    result["ready_ms"] = 0
                    return result
                old = self.child
                self.generation += 1
                generation = self.generation
                self.ready = False
                self.output_ticks = 0
                self.last_error = ""
            if old is not None and old.poll() is None:
                old.terminate()
                try:
                    old.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    old.kill()
                    old.wait(timeout=3)
                time.sleep(0.1)
            error = "compositor did not start"
            for _attempt in range(3):
                child = subprocess.Popen(
                    self._command(sources), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
                with self.lock:
                    self.child = child
                threading.Thread(
                    target=self._copy_output, args=(child, generation), daemon=True
                ).start()
                deadline = time.monotonic() + self.args.ready_timeout
                while time.monotonic() < deadline:
                    with self.lock:
                        if generation == self.generation and self.ready:
                            self.scene = {"count": count, "sources": list(sources)}
                            self.last_ready_ms = round((time.monotonic() - started) * 1000)
                            return self.state()
                    if child.poll() is not None:
                        break
                    time.sleep(0.025)
                with self.lock:
                    error = self.last_error or f"compositor exited with {child.poll()}"
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=3)
                time.sleep(0.2)
            raise RuntimeError(f"scene did not become ready: {error}")

    def state(self) -> dict[str, object]:
        with self.lock:
            child = self.child
            live = bool(self.ready and child and child.poll() is None)
            return {
                "bank": self.args.bank, "layout": self.scene["count"],
                "sources": self.scene["sources"], "state": "ready" if live else "starting",
                "ready": live, "revision": self.generation, "ready_ms": self.last_ready_ms,
                "output_ticks": self.output_ticks,
                "child_pid": child.pid if child and child.poll() is None else None,
                "last_error": self.last_error,
            }

    def stop(self) -> None:
        with self.lock:
            child = self.child
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)


ENGINE: SceneEngine


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/v1/state":
            return self._send(HTTPStatus.OK, ENGINE.state())
        if self.path == "/api/v1/health/live":
            return self._send(HTTPStatus.OK, {"live": True})
        if self.path in {"/api/v1/health", "/api/v1/health/ready", "/state"}:
            state = ENGINE.state()
            status = HTTPStatus.OK if state["ready"] else HTTPStatus.SERVICE_UNAVAILABLE
            return self._send(status, state)
        self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/api/v1/scene", "/scene"}:
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            result = ENGINE.configure(
                int(payload["count"]), [int(item) for item in payload["sources"]]
            )
            self._send(HTTPStatus.OK, result)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--bank", choices=("a", "b"), required=True)
    result.add_argument("--domain", required=True)
    result.add_argument("--input", action="append", required=True)
    result.add_argument("--output-flow-id", required=True)
    result.add_argument("--compositor", required=True)
    result.add_argument("--width", type=int, default=1920)
    result.add_argument("--height", type=int, default=1080)
    result.add_argument("--rate", default="30000/1001")
    result.add_argument("--control-host", default="127.0.0.1")
    result.add_argument("--control-port", type=int, default=9720)
    result.add_argument("--ready-timeout", type=float, default=12)
    result.add_argument("--initial-count", type=int, required=True)
    result.add_argument("--initial-sources", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    global ENGINE
    args = parser().parse_args(argv)
    if not 2 <= len(args.input) <= 8:
        raise SystemExit("two through eight --input values are required")
    ENGINE = SceneEngine(args)
    ENGINE.configure(args.initial_count, [int(item) for item in args.initial_sources.split(",")])
    server = ThreadingHTTPServer((args.control_host, args.control_port), Handler)

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
        return 0
    finally:
        ENGINE.stop()


if __name__ == "__main__":
    raise SystemExit(main())
