#!/usr/bin/env python3
"""Fail-closed one-shot readiness check for an extension manifest and service."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


Fetch = Callable[[str], tuple[int, dict[str, Any]]]


def validate_loopback_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise ValueError("extension health base URL must be loopback HTTP(S)")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("extension health base URL must not contain path, query, or user info")
    return value.rstrip("/")


def fetch_json(url: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        finally:
            error.close()


def check(manifest: dict[str, Any], base_url: str, fetch: Fetch = fetch_json) -> dict[str, Any]:
    base = validate_loopback_url(base_url)
    health = manifest["health"]
    live_status, live = fetch(base + health["liveness_path"])
    ready_status, ready = fetch(base + health["readiness_path"])
    state_status, state = fetch(base + health["state_path"])
    expected_name = manifest["name"]
    checks = {
        "live": live_status == 200 and live.get("live") is True,
        "ready": ready_status == 200 and ready.get("ready") is True,
        "state": state_status == 200 and state.get("ready") is True,
        "identity": state.get("name") == expected_name,
    }
    return {
        "name": expected_name,
        "passed": all(checks.values()),
        "checks": checks,
        "state": state.get("state", "unknown"),
        "revision": state.get("revision"),
        "restarts": state.get("restarts"),
        "last_error": state.get("last_error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text())
        result = check(manifest, args.url)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, separators=(",", ":")))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
