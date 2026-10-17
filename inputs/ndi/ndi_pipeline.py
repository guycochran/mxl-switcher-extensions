#!/usr/bin/env python3
"""Run one NDI pipeline on GstSystemClock instead of the NDI receiver clock."""

from __future__ import annotations

import argparse
import json
import signal


def description(tokens: list[str]) -> str:
    """Serialize pipeline tokens while quoting untrusted property values."""

    rendered: list[str] = []
    for token in tokens:
        if token.startswith(("video/", "audio/")) or token == "mix-matrix=<>":
            rendered.append(token)
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            if any(character.isspace() or character in {'"', "\\"} for character in value):
                token = f"{key}={json.dumps(value)}"
        rendered.append(token)
    return " ".join(rendered)


def main(argv: list[str] | None = None) -> int:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pipeline", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.pipeline[:1] == ["--"]:
        args.pipeline = args.pipeline[1:]
    if not args.pipeline:
        parser.error("pipeline tokens are required after --")

    Gst.init(None)
    pipeline = Gst.parse_launch(description(args.pipeline))
    pipeline.use_clock(Gst.SystemClock.obtain())

    def stop(*_unused) -> None:
        pipeline.send_event(Gst.Event.new_eos())

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        pipeline.set_state(Gst.State.NULL)
        return 1
    message = pipeline.get_bus().timed_pop_filtered(
        Gst.CLOCK_TIME_NONE,
        Gst.MessageType.ERROR | Gst.MessageType.EOS,
    )
    failed = message.type == Gst.MessageType.ERROR
    if failed:
        error, debug = message.parse_error()
        print(f"NDI pipeline error: {error}: {debug}", flush=True)
    pipeline.set_state(Gst.State.NULL)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
