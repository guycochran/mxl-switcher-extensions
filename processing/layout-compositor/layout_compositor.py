#!/usr/bin/env python3
"""Compose selected MXL video flows into one paced MXL scene flow."""

from __future__ import annotations

import argparse
import json
import signal
import threading
import time
from dataclasses import dataclass


SUPPORTED_LAYOUTS = (2, 3, 4, 5, 6, 7, 8)


@dataclass(frozen=True)
class Tile:
    x: int
    y: int
    width: int
    height: int


def layout_tiles(count: int, width: int = 1920, height: int = 1080) -> list[Tile]:
    """Return the fixed broadcast-safe geometry for a supported scene."""

    if count not in SUPPORTED_LAYOUTS:
        raise ValueError("layout must contain between 2 and 8 inputs")
    if count == 2:
        return [Tile(0, 0, width // 2, height), Tile(width // 2, 0, width // 2, height)]
    if count == 3:
        return [
            Tile(0, 0, width * 2 // 3, height),
            Tile(width * 2 // 3, 0, width // 3, height // 2),
            Tile(width * 2 // 3, height // 2, width // 3, height // 2),
        ]
    row_counts = {
        4: (2, 2),
        5: (3, 2),
        6: (3, 3),
        7: (4, 3),
        8: (4, 4),
    }[count]
    tiles: list[Tile] = []
    row_height = height // 2
    for row, columns in enumerate(row_counts):
        x = 0
        for column in range(columns):
            next_x = width if column == columns - 1 else width * (column + 1) // columns
            tiles.append(Tile(x, row * row_height, next_x - x,
                              height - row_height if row else row_height))
            x = next_x
    return tiles


def _rate(value: str) -> tuple[int, int]:
    numerator, denominator = (int(part) for part in value.split("/", 1))
    if numerator <= 0 or denominator <= 0:
        raise ValueError("rate must be a positive fraction")
    return numerator, denominator


def _v210_datarate(width: int, height: int, rate: str) -> int:
    numerator, denominator = _rate(rate)
    stride = ((width + 47) // 48) * 128
    return round(stride * height * numerator / denominator)


def pipeline_description(args: argparse.Namespace) -> str:
    tiles = layout_tiles(len(args.input), args.width, args.height)
    parts = [
        "compositor name=mix background=black force-live=true ignore-inactive-pads=true",
        "latency=100000000",
    ]
    for index, tile in enumerate(tiles):
        parts.extend([
            f"sink_{index}::xpos={tile.x}", f"sink_{index}::ypos={tile.y}",
            f"sink_{index}::width={tile.width}", f"sink_{index}::height={tile.height}",
            f"sink_{index}::zorder={index + 1}",
            f"sink_{index}::sizing-policy=keep-aspect-ratio",
        ])
    parts.extend([
        f"! video/x-raw,format=v210,width={args.width},height={args.height},"
        f"pixel-aspect-ratio=1/1,framerate={args.rate},interlace-mode=progressive,colorimetry=bt709",
        "! queue max-size-buffers=4 max-size-time=0 max-size-bytes=0",
        f"! identity datarate={_v210_datarate(args.width, args.height, args.rate)} single-segment=true",
        "! clocksync sync-to-first=false",
        "! identity name=output_tick silent=true",
        f"! mxlsink flow-id={args.output_flow_id} domain={args.domain}",
    ])
    for index, (flow_id, tile) in enumerate(zip(args.input, tiles, strict=True)):
        parts.extend([
            f"mxlsrc domain={args.domain} video-flow-id={flow_id} automatic-eos=false",
            "! queue max-size-buffers=3 max-size-time=0 max-size-bytes=0 leaky=downstream",
            f"! identity name=input_{index} silent=true",
            "! queue max-size-buffers=2 max-size-time=0 max-size-bytes=0 leaky=downstream",
            f"! mix.sink_{index}",
        ])
    return " ".join(parts)


class Compositor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.stop_event = threading.Event()
        self.output_ticks = 0
        self.input_ticks = [0 for _ in args.input]
        self.started = time.monotonic()
        self.last_report = self.started
        self.last_output_ticks = 0
        self.first_output_at: float | None = None

    @staticmethod
    def _load_gst():
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        return Gst

    def _probe(self, index: int | None):
        def count(_pad, _info):
            if index is None:
                if self.first_output_at is None:
                    self.first_output_at = time.monotonic()
                self.output_ticks += 1
            else:
                self.input_ticks[index] += 1
            return self.Gst.PadProbeReturn.OK
        return count

    def report(self) -> None:
        now = time.monotonic()
        elapsed = max(0.001, now - self.last_report)
        fps = (self.output_ticks - self.last_output_ticks) / elapsed
        output_window = 0.0 if self.first_output_at is None else now - self.first_output_at
        average_output_fps = (
            0.0 if output_window <= 0 else max(0, self.output_ticks - 1) / output_window
        )
        print(json.dumps({
            "event": "health", "state": "ready" if self.output_ticks else "starting",
            "output_ticks": self.output_ticks, "output_fps": round(fps, 3),
            "average_output_fps": round(average_output_fps, 3),
            "output_window_s": round(output_window, 3),
            "input_ticks": list(self.input_ticks), "uptime_s": round(now - self.started, 3),
        }, separators=(",", ":")), flush=True)
        self.last_report, self.last_output_ticks = now, self.output_ticks

    def run(self) -> int:
        self.Gst = self._load_gst()
        pipeline = self.Gst.parse_launch(pipeline_description(self.args))
        pipeline.get_by_name("output_tick").get_static_pad("src").add_probe(
            self.Gst.PadProbeType.BUFFER, self._probe(None)
        )
        for index in range(len(self.args.input)):
            pipeline.get_by_name(f"input_{index}").get_static_pad("src").add_probe(
                self.Gst.PadProbeType.BUFFER, self._probe(index)
            )
        bus = pipeline.get_bus()
        pipeline.set_state(self.Gst.State.PLAYING)
        try:
            while not self.stop_event.wait(self.args.health_interval):
                message = bus.pop_filtered(
                    self.Gst.MessageType.ERROR | self.Gst.MessageType.EOS
                )
                if message:
                    if message.type == self.Gst.MessageType.ERROR:
                        error, _debug = message.parse_error()
                        print(json.dumps({"event": "fatal", "reason": str(error)}), flush=True)
                    else:
                        print(json.dumps({"event": "output_eos"}), flush=True)
                    return 2
                self.report()
            return 0
        finally:
            pipeline.set_state(self.Gst.State.NULL)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--domain", required=True)
    result.add_argument("--input", action="append", required=True)
    result.add_argument("--output-flow-id", required=True)
    result.add_argument("--output-label", default="MXL composed scene")
    result.add_argument("--width", type=int, default=1920)
    result.add_argument("--height", type=int, default=1080)
    result.add_argument("--rate", default="30000/1001")
    result.add_argument("--health-interval", type=float, default=0.5)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if len(args.input) not in SUPPORTED_LAYOUTS:
        raise SystemExit("layout must contain between 2 and 8 inputs")
    _rate(args.rate)
    compositor = Compositor(args)
    signal.signal(signal.SIGTERM, lambda *_: compositor.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: compositor.stop_event.set())
    return compositor.run()


if __name__ == "__main__":
    raise SystemExit(main())
