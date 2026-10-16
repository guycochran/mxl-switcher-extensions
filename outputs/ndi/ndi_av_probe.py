#!/usr/bin/env python3
"""Verify that an advertised NDI source carries both video and audio."""

from __future__ import annotations

import argparse
import json
import threading
import time


SAMPLE_WIDTHS = {
    "F32LE": 4,
    "F32BE": 4,
    "S16LE": 2,
    "S16BE": 2,
}


def evaluate(result: dict, expected_video_fps: float = 30.0, expected_audio_rate: int = 48_000) -> dict:
    video_span = result["video_last_pts"] - result["video_first_pts"] if result["video_buffers"] > 1 else 0
    audio_span = result["audio_last_pts"] - result["audio_first_pts"] if result["audio_buffers"] > 1 else 0
    result["video_fps_from_pts"] = (
        (result["video_buffers"] - 1) * 1_000_000_000 / video_span if video_span > 0 else 0.0
    )
    result["audio_sample_rate_from_pts"] = (
        result["audio_sample_frames"] * 1_000_000_000 / audio_span if audio_span > 0 else 0.0
    )
    result["video_rate_ok"] = expected_video_fps * 0.98 <= result["video_fps_from_pts"] <= expected_video_fps * 1.02
    result["audio_rate_ok"] = expected_audio_rate * 0.98 <= result["audio_sample_rate_from_pts"] <= expected_audio_rate * 1.02
    result["passed"] = (
        result["error"] is None
        and result["video_buffers"] > 1
        and result["audio_buffers"] > 1
        and result["video_rate_ok"]
        and result["audio_rate_ok"]
    )
    return result


def probe(source_name: str, seconds: float = 3.0, connect_timeout_ms: int = 10_000) -> dict:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    safe_name = source_name.replace("\\", "\\\\").replace('"', '\\"')
    pipeline = Gst.parse_launch(
        f'ndisrc ndi-name="{safe_name}" connect-timeout={connect_timeout_ms} '
        f'timeout={connect_timeout_ms} ! ndisrcdemux name=demux '
        "demux.video ! queue ! appsink name=video emit-signals=true sync=false "
        "max-buffers=8 drop=true "
        "demux.audio ! queue ! appsink name=audio emit-signals=true sync=false "
        "max-buffers=32 drop=true"
    )
    lock = threading.Lock()
    result = {
        "video_buffers": 0,
        "video_bytes": 0,
        "video_first_pts": None,
        "video_last_pts": None,
        "audio_buffers": 0,
        "audio_bytes": 0,
        "audio_sample_frames": 0,
        "audio_rate_hz": None,
        "audio_channels": None,
        "audio_format": None,
        "audio_first_pts": None,
        "audio_last_pts": None,
        "error": None,
    }

    def callback(kind: str):
        def receive(sink):
            sample = sink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.ERROR
            buffer = sample.get_buffer()
            with lock:
                result[f"{kind}_buffers"] += 1
                result[f"{kind}_bytes"] += buffer.get_size()
                if result[f"{kind}_first_pts"] is None:
                    result[f"{kind}_first_pts"] = int(buffer.pts)
                result[f"{kind}_last_pts"] = int(buffer.pts)
                if kind == "audio":
                    structure = sample.get_caps().get_structure(0)
                    audio_format = structure.get_string("format")
                    channels = structure.get_value("channels")
                    rate = structure.get_value("rate")
                    width = SAMPLE_WIDTHS.get(audio_format)
                    result["audio_format"] = audio_format
                    result["audio_channels"] = channels
                    result["audio_rate_hz"] = rate
                    if width and channels:
                        result["audio_sample_frames"] += buffer.get_size() // (width * channels)
            return Gst.FlowReturn.OK

        return receive

    pipeline.get_by_name("video").connect("new-sample", callback("video"))
    pipeline.get_by_name("audio").connect("new-sample", callback("audio"))
    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        raise RuntimeError("NDI verification pipeline failed to start")
    bus = pipeline.get_bus()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        message = bus.timed_pop_filtered(
            100 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS
        )
        if message:
            result["error"] = (
                message.parse_error()[1]
                if message.type == Gst.MessageType.ERROR
                else "unexpected EOS"
            )
            break
    pipeline.set_state(Gst.State.NULL)
    with lock:
        return evaluate(dict(result))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--connect-timeout-ms", type=int, default=10_000)
    args = parser.parse_args()
    if args.seconds <= 0 or args.connect_timeout_ms <= 0:
        parser.error("probe duration and connect timeout must be positive")
    result = probe(args.source_name, args.seconds, args.connect_timeout_ms)
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
