#!/usr/bin/env python3
"""Run one NDI pipeline on GstSystemClock instead of the NDI receiver clock."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time


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
    startup_probes = []
    frame_inputs = {}
    frame_outputs = {}
    timestamp_syncs = []
    iterator = pipeline.iterate_elements()
    while True:
        result, element = iterator.next()
        if result == Gst.IteratorResult.OK:
            if element.get_name().startswith("startup_gate_"):
                gates.append(element)
            elif element.get_name().startswith("startup_probe_"):
                startup_probes.append(element)
            elif element.get_name().startswith("frame_sync_in_"):
                frame_inputs[element.get_name().removeprefix("frame_sync_in_")] = element
            elif element.get_name().startswith("frame_sync_out_"):
                frame_outputs[element.get_name().removeprefix("frame_sync_out_")] = element
            elif element.get_name().startswith("timestamp_sync_"):
                timestamp_syncs.append(element)
        elif result == Gst.IteratorResult.RESYNC:
            iterator.resync()
            gates.clear()
            startup_probes.clear()
            frame_inputs.clear()
            frame_outputs.clear()
            timestamp_syncs.clear()
        elif result == Gst.IteratorResult.DONE:
            break
        else:
            raise RuntimeError("could not enumerate pipeline elements")
    if gates:
        if set(frame_inputs) != set(frame_outputs):
            raise RuntimeError("frame synchronizer inputs and outputs do not match")
        ready: set[str] = set()
        ready_lock = threading.Lock()
        opened = threading.Event()
        pump_stop = threading.Event()
        latest_buffers = {}
        buffer_lock = threading.Lock()
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

            def pump() -> None:
                frame_number = 0
                interval = args.rate_denominator / args.rate_numerator
                deadline = time.monotonic()
                while not pump_stop.is_set():
                    delay = deadline - time.monotonic()
                    if delay > 0 and pump_stop.wait(delay):
                        return
                    with buffer_lock:
                        current = {
                            branch: latest_buffers[branch].copy()
                            for branch in frame_outputs
                        }
                    pts = timeline_epoch[0] + frame_number * frame_duration
                    for branch, appsrc in frame_outputs.items():
                        buffer = current[branch]
                        buffer.pts = pts
                        buffer.dts = Gst.CLOCK_TIME_NONE
                        buffer.duration = frame_duration
                        if appsrc.emit("push-buffer", buffer) != Gst.FlowReturn.OK:
                            pump_stop.set()
                            return
                    frame_number += 1
                    deadline += interval
                    if deadline < time.monotonic() - interval:
                        deadline = time.monotonic()

            threading.Thread(target=pump, daemon=True).start()

        def observe(_pad, info, gate):
            if info.type & Gst.PadProbeType.BUFFER:
                with ready_lock:
                    ready.add(gate.get_name())
                    if len(ready) == len(startup_probes) and not opened.is_set():
                        opened.set()
                        threading.Thread(target=open_all, daemon=True).start()
            return Gst.PadProbeReturn.OK

        if frame_inputs:
            def sample_ready(appsink, branch):
                sample = appsink.emit("pull-sample")
                if sample is None or sample.get_buffer() is None:
                    return Gst.FlowReturn.ERROR
                with buffer_lock:
                    first = branch not in latest_buffers
                    latest_buffers[branch] = sample.get_buffer().copy()
                if first:
                    print(
                        f"NDI frame synchronizer ready: {branch}",
                        file=sys.stderr,
                        flush=True,
                    )
                with ready_lock:
                    ready.add(branch)
                    if len(ready) == len(frame_inputs) and not opened.is_set():
                        opened.set()
                        threading.Thread(target=open_all, daemon=True).start()
                return Gst.FlowReturn.OK

            for branch, appsink in frame_inputs.items():
                appsink.connect("new-sample", sample_ready, branch)
        else:
            for startup_probe in startup_probes:
                startup_probe.get_static_pad("sink").add_probe(
                    Gst.PadProbeType.BUFFER, observe, startup_probe
                )

    def stop(*_unused) -> None:
        if gates:
            pump_stop.set()
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
        # Element names and GStreamer error domains are safe operational
        # diagnostics.  Do not print the debug string: source elements may
        # include credentialed or private addresses in it.
        source = message.src.get_name() if message.src is not None else "unknown"
        print(
            f"NDI pipeline error from {source}: {error}",
            file=sys.stderr,
            flush=True,
        )
    if gates:
        pump_stop.set()
        for appsrc in frame_outputs.values():
            appsrc.emit("end-of-stream")
    pipeline.set_state(Gst.State.NULL)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
