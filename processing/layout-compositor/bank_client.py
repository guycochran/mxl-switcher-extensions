#!/usr/bin/env python3
"""Small loopback-only client used by the operator Skin to prepare a bank."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from layout_compositor import SUPPORTED_LAYOUTS


def bank_url(bank: str) -> str:
    if bank not in {"a", "b"}:
        raise ValueError("bank must be a or b")
    value = os.environ.get(f"MXL_LAYOUT_BANK_{bank.upper()}_URL", f"http://127.0.0.1:{9721 if bank == 'a' else 9722}")
    if not value.startswith(("http://127.0.0.1:", "http://localhost:", "http://[::1]:")):
        raise ValueError("layout bank URL must be loopback HTTP")
    return value.rstrip("/")


def prepare(bank: str, count: int, sources: list[int]) -> dict[str, object]:
    if count not in SUPPORTED_LAYOUTS or len(sources) != count:
        raise ValueError("layout must contain 2, 3, 4, 6, or 8 sources")
    if len(set(sources)) != count or any(source < 1 or source > 8 for source in sources):
        raise ValueError("sources must be unique numbers from 1 through 8")
    request = urllib.request.Request(
        bank_url(bank) + "/api/v1/scene",
        data=json.dumps({"count": count, "sources": sources}).encode(),
        method="POST", headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        value = json.load(response)
    if not isinstance(value, dict) or value.get("ready") is not True:
        raise RuntimeError("layout bank did not report ready")
    return value


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    if len(values) < 3:
        print("usage: bank_client.py BANK COUNT SOURCE...", file=sys.stderr)
        return 2
    try:
        bank, count = values[0], int(values[1])
        result = prepare(bank, count, [int(item) for item in values[2:]])
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
