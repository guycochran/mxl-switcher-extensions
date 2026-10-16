"""Thread-safe lifecycle state and bounded restart policy for extensions."""

from __future__ import annotations

import copy
import json
import threading
import time
from collections import deque
from typing import Any, Callable


STATES = {"disabled", "starting", "ready", "running", "degraded", "failed"}
ALLOWED_TRANSITIONS = {
    "disabled": {"starting"},
    "starting": {"ready", "degraded", "failed", "disabled"},
    "ready": {"starting", "running", "degraded", "failed", "disabled"},
    "running": {"ready", "degraded", "failed", "disabled"},
    "degraded": {"starting", "ready", "running", "failed", "disabled"},
    "failed": {"starting", "disabled"},
}
SENSITIVE_KEY_PARTS = ("credential", "password", "secret", "token")


def _reject_sensitive_keys(value: Any, path: str = "metrics") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                raise ValueError(f"{path}.{key}: sensitive keys are not public metrics")
            _reject_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, f"{path}[{index}]")


class ExtensionRuntime:
    """Own the common observable state for one extension process.

    Media adapters keep transport-specific behavior outside this class. State
    changes and metric publications are atomic, revisioned, and JSON-safe.
    """

    def __init__(self, name: str, kind: str) -> None:
        if not name or not kind:
            raise ValueError("name and kind are required")
        self._lock = threading.RLock()
        self._name = name
        self._kind = kind
        self._state = "disabled"
        self._ready = False
        self._revision = 0
        self._restarts = 0
        self._last_error: str | None = None
        self._metrics: dict[str, Any] = {}

    def transition(
        self,
        state: str,
        *,
        ready: bool | None = None,
        error: str | None = None,
    ) -> None:
        if state not in STATES:
            raise ValueError(f"unknown extension state: {state}")
        with self._lock:
            if state != self._state and state not in ALLOWED_TRANSITIONS[self._state]:
                raise ValueError(f"invalid extension transition: {self._state} -> {state}")
            next_ready = state in {"ready", "running"} if ready is None else bool(ready)
            next_error = error
            if state == "failed" and not error:
                raise ValueError("failed state requires an error")
            if state not in {"degraded", "failed"} and error is not None:
                raise ValueError("only degraded or failed state may expose an error")
            changed = (state, next_ready, next_error) != (
                self._state,
                self._ready,
                self._last_error,
            )
            self._state = state
            self._ready = next_ready
            self._last_error = next_error
            if changed:
                self._revision += 1

    def publish_metrics(self, metrics: dict[str, Any]) -> None:
        if not isinstance(metrics, dict):
            raise TypeError("metrics must be an object")
        _reject_sensitive_keys(metrics)
        # This also rejects adapters accidentally publishing non-JSON values.
        json.dumps(metrics, allow_nan=False)
        with self._lock:
            next_metrics = copy.deepcopy(metrics)
            if next_metrics != self._metrics:
                self._metrics = next_metrics
                self._revision += 1

    def record_restart(self) -> None:
        with self._lock:
            self._restarts += 1
            self._revision += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "kind": self._kind,
                "state": self._state,
                "ready": self._ready,
                "revision": self._revision,
                "restarts": self._restarts,
                "last_error": self._last_error,
                "metrics": copy.deepcopy(self._metrics),
            }


class RestartBudget:
    """Sliding-window restart limiter with no implicit sleep or process action."""

    def __init__(
        self,
        max_restarts: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_restarts < 0 or window_seconds <= 0:
            raise ValueError("restart budget requires max_restarts >= 0 and window > 0")
        self.max_restarts = max_restarts
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def claim(self, now: float | None = None) -> bool:
        """Consume one restart slot, or fail closed when none remains."""

        moment = self._clock() if now is None else float(now)
        with self._lock:
            self._prune(moment)
            if len(self._events) >= self.max_restarts:
                return False
            self._events.append(moment)
            return True

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        moment = self._clock() if now is None else float(now)
        with self._lock:
            self._prune(moment)
            retry_after = 0.0
            if len(self._events) >= self.max_restarts and self._events:
                retry_after = max(0.0, self._events[0] + self.window_seconds - moment)
            return {
                "max_restarts": self.max_restarts,
                "window_seconds": self.window_seconds,
                "used": len(self._events),
                "remaining": max(0, self.max_restarts - len(self._events)),
                "retry_after_seconds": retry_after,
            }
