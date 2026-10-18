#!/usr/bin/env python3
"""Run one NDI pipeline on GstSystemClock instead of the NDI receiver clock."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading


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
    parser.add_argument("--rate-numerator", type=int, default=30)
    parser.add_argument("--rate-denominator", type=int, default=1)
    parser.add_argument("--startup-lead-frames", type=int, default=2)
    parser.add_argument("pipeline", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.pipeline[:1] == ["--"]:
        args.pipeline = args.pipeline[1:]
    if not args.pipeline:
        parser.error("pipeline tokens are required after --")
    if args.rate_numerator <= 0 or args.rate_denominator <= 0:
        parser.error("rate numerator and denominator must be positive")
    if args.startup_lead_frames < 0:
        parser.error("startup lead frames must not be negative")

    Gst.init(None)
    pipeline = Gst.parse_launch(description(args.pipeline))
    pipeline.use_clock(Gst.SystemClock.obtain())

    gates = []
    timestamp_syncs = []
    iterator = pipeline.iterate_elements()
    while True:
        result, element = iterator.next()
        if result == Gst.IteratorResult.OK:
            if element.get_name().startswith("startup_gate_"):
                gates.append(element)
            elif element.get_name().startswith("timestamp_sync_"):
                timestamp_syncs.append(element)
        elif result == Gst.IteratorResult.RESYNC:
            iterator.resync()
            gates.clear()
            timestamp_syncs.clear()
        elif result == Gst.IteratorResult.DONE:
            break
        else:
            raise RuntimeError("could not enumerate pipeline elements")
    if gates:
        ready: set[str] = set()
        ready_lock = threading.Lock()
        opened = threading.Event()
        frame_duration = Gst.util_uint64_scale(
            args.rate_denominator, Gst.SECOND, args.rate_numerator
        )
        timeline_epoch = [0]
        frame_numbers = {element.get_name(): 0 for element in timestamp_syncs}

        def retimestamp(element, buffer) -> None:
            name = element.get_name()
            frame_number = frame_numbers[name]
            buffer.pts = timeline_epoch[0] + frame_number * frame_duration
            buffer.dts = Gst.CLOCK_TIME_NONE
            buffer.duration = frame_duration
            frame_numbers[name] = frame_number + 1

        for timestamp_sync in timestamp_syncs:
            timestamp_sync.connect("handoff", retimestamp)

        def open_all() -> None:
            # All bank branches start their fixed-cadence timeline on one
            # pipeline-clock boundary.  Per-branch sync-to-first offsets would
            # preserve each receiver's arbitrary arrival phase.
            clock = pipeline.get_clock()
            running_time = clock.get_time() - pipeline.get_base_time()
            timeline_epoch[0] = (
                running_time + args.startup_lead_frames * frame_duration
            )
            for gate in gates:
                gate.set_property("drop", False)
            opened.set()

        def observe(_pad, info, gate):
            if info.type & Gst.PadProbeType.BUFFER:
                with ready_lock:
                    ready.add(gate.get_name())
                    if len(ready) == len(gates) and not opened.is_set():
                        threading.Thread(target=open_all, daemon=True).start()
            return Gst.PadProbeReturn.OK

        for gate in gates:
            gate.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, observe, gate)

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
        error, _debug = message.parse_error()
        print(f"NDI pipeline error: {error}", file=sys.stderr, flush=True)
    pipeline.set_state(Gst.State.NULL)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
