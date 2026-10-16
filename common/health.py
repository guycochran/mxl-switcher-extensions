"""Loopback-friendly HTTP health contract shared by MXL extensions."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable


SnapshotProvider = Callable[[], dict[str, Any]]


def readiness(snapshot: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Return a stable readiness result without confusing it with liveness."""

    ready = snapshot.get("ready") is True
    return (
        HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
        {
            "ready": ready,
            "state": snapshot.get("state", "unknown"),
            "revision": snapshot.get("revision"),
        },
    )


def handler_for_snapshot(snapshot_provider: SnapshotProvider):
    """Build a GET-only handler for state, liveness, and readiness.

    Adapters may subclass the returned handler to add their own authenticated
    or loopback-only control endpoints. Health responses deliberately contain
    no adapter configuration or credentials.
    """

    class HealthHandler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/v1/state":
                self._json(HTTPStatus.OK, snapshot_provider())
                return
            if self.path == "/api/v1/health/live":
                self._json(HTTPStatus.OK, {"live": True})
                return
            if self.path in {"/api/v1/health", "/api/v1/health/ready"}:
                status, payload = readiness(snapshot_provider())
                self._json(status, payload)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return HealthHandler
