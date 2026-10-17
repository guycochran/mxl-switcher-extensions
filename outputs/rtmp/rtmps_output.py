#!/usr/bin/env python3
"""Supervised, secret-safe MXL Program A/V output to RTMPS."""

from __future__ import annotations

import argparse
import collections
import ctypes
import json
import os
import pathlib
import resource
import signal
import stat
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from typing import Any

from common.health import handler_for_snapshot
from youtube_8up_canvas import Input, parse_input, tile_layout, write_label_png


QUEUE_NS = 2_000_000_000
COMPOSITOR_LATENCY_NS = 100_000_000
MUX_LATENCY_NS = 300_000_000
SEND_QUEUE_READY_NS = 500_000_000
# Crossing 90 ms is an early warning. The compositor can still accept a frame
# until its configured latency plus one frame interval; fail only at that real
# deadline instead of turning a recoverable warning into a false outage.
SOURCE_LATENESS_WARN_NS = 90_000_000
BITRATE_WINDOW_SECONDS = 4.0
RTMP_CHUNK_SIZE = 4096
# AAC-LC emits one encoded buffer per 1024 PCM samples per channel.
AAC_FRAME_SAMPLES = 1024


def audio_flow_ids(args: argparse.Namespace) -> list[str]:
    """Normalize the legacy single audio-flow value and repeated CLI values."""
    value = args.audio_flow
    if isinstance(value, str):
        return [value]
    return list(value or [])


def current_rss_mib() -> float:
    """Return current process RSS on Linux, with a portable peak-RSS fallback."""
    try:
        lines = pathlib.Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
        for line in lines:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS and the BSDs report bytes.
    return peak / (1024 * 1024) if peak > 10_000_000 else peak / 1024


def rate_as_float(rate: str) -> float:
    numerator, denominator = (int(part) for part in rate.split("/", 1))
    if numerator <= 0 or denominator <= 0:
        raise ValueError("frame rate must be a positive fraction")
    return numerator / denominator


def rtmp_peak_kbps(args: argparse.Namespace) -> int:
    """Allow a stalled live connection to drain without creating standing latency."""
    total_kbps = args.video_bitrate_kbps + (args.audio_bitrate_bps // 1000)
    return total_kbps * 2


def running_timecode(elapsed: float, rate: str) -> str:
    """Return a display timecode driven by monotonic pipeline run time."""
    fps = rate_as_float(rate)
    nominal_fps = max(1, round(fps))
    elapsed = max(0.0, elapsed)
    total_seconds = int(elapsed)
    frames = min(nominal_fps - 1, int((elapsed - total_seconds) * fps))
    seconds = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = (total_seconds // 3600) % 100
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def timecode_svg(elapsed: float, rate: str) -> str:
    """Build the dependency-free SVG used by the live ninth canvas tile."""
    value = running_timecode(elapsed, rate)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
        'viewBox="0 0 1280 720">'
        '<rect x="110" y="190" width="1060" height="330" rx="28" '
        'fill="#071018" fill-opacity="0.92" stroke="#26d9e9" stroke-width="4"/>'
        '<text x="640" y="300" text-anchor="middle" fill="#26d9e9" '
        'font-family="monospace" font-size="58" font-weight="700">LIVE TIMECODE</text>'
        f'<text x="640" y="440" text-anchor="middle" fill="#f4f7ff" '
        f'font-family="monospace" font-size="132" font-weight="700">{value}</text>'
        '</svg>'
    )


def video_encoder_description(args: argparse.Namespace) -> list[str]:
    """Return explicit low-latency settings for the selected H.264 encoder."""
    if args.video_encoder == "x264enc":
        return [
            f"! x264enc name=video_encoder bitrate={args.video_bitrate_kbps}",
            "tune=zerolatency speed-preset=ultrafast pass=cbr",
            "key-int-max=60 bframes=0 byte-stream=true aud=true",
            "vbv-buf-capacity=2000 threads=16 sliced-threads=true",
            'option-string="nal-hrd=cbr:force-cfr=1"',
        ]
    return [
        f"! {args.video_encoder} name=video_encoder bitrate={args.video_bitrate_kbps}",
        "rc-mode=cbr gop-size=60 bframes=2 aud=true",
        "strict-gop=true vbv-buffer-size=60000",
    ]


def validate_stream_key(key: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not key or len(key) > 200 or any(char not in allowed for char in key):
        raise ValueError("stream key is not valid")
    return key


def read_stream_key(path: pathlib.Path) -> str:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("stream-key path is not a regular file")
    if info.st_mode & 0o077:
        raise ValueError("stream-key file must not be accessible by group or other users")
    if info.st_uid != os.geteuid():
        raise ValueError("stream-key file must be owned by the output process user")
    key = path.read_text(encoding="utf-8").strip()
    return validate_stream_key(key)


def destination_url(server_url: str, stream_key: str) -> str:
    validate_stream_key(stream_key)
    parsed = urllib.parse.urlsplit(server_url)
    if parsed.scheme != "rtmps" or not parsed.hostname:
        raise ValueError("production destination must be an rtmps:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("destination URL cannot contain credentials, query, or fragment")
    if any(char in server_url for char in "\r\n\t"):
        raise ValueError("destination URL contains control characters")
    return server_url.rstrip("/") + "/" + stream_key


def pipeline_description(args: argparse.Namespace) -> str:
    """Return a pipeline with a named sink but no destination secret."""
    parts = [
        f"flvmux name=mux latency={MUX_LATENCY_NS} streamable=true skip-backwards-streams=true",
        "! identity name=mux_bytes silent=true",
        "! queue name=send_queue max-size-time=2000000000 max-size-buffers=0 max-size-bytes=0",
        f"! rtmp2sink name=destination sync=false async=false chunk-size={RTMP_CHUNK_SIZE}",
    ]
    inputs = getattr(args, "input", None) or []
    if inputs:
        tile_width, tile_height, positions = tile_layout(len(inputs))
        parts.extend([
            "compositor name=mix background=black force-live=true",
            f"ignore-inactive-pads=true latency={COMPOSITOR_LATENCY_NS}",
        ])
        for index, (x, y) in enumerate(positions[:len(inputs)]):
            parts.extend([
                f"sink_{index}::xpos={x}",
                f"sink_{index}::ypos={y}",
                f"sink_{index}::width={tile_width}",
                f"sink_{index}::height={tile_height}",
                f"sink_{index}::zorder={index + 1}",
            ])
        if len(inputs) == 4:
            parts.extend([
                "sink_4::xpos=0", "sink_4::ypos=0",
                "sink_4::width=3840", "sink_4::height=2160", "sink_4::zorder=0",
            ])
        elif len(inputs) == 8:
            # The diagnostic source is the ninth pad in the 3x3 canvas.
            # Explicit placement prevents its default (0, 0) position from
            # covering source 1 and leaving the intended status cell black.
            parts.extend([
                "sink_8::xpos=2560", "sink_8::ypos=1440",
                "sink_8::width=1280", "sink_8::height=720", "sink_8::zorder=9",
            ])
        parts.append(
            f"! video/x-raw,format=BGRA,width=3840,height=2160,framerate={args.rate}"
        )
        parts.extend([
            "! videoconvert n-threads=8",
            f"! video/x-raw,format=NV12,width=3840,height=2160,framerate={args.rate},colorimetry=bt709",
        ])
    else:
        if not args.video_flow:
            raise ValueError("either --video-flow or exactly four/eight --input values are required")
        parts.extend([
            f"mxlsrc domain={args.domain} video-flow-id={args.video_flow} automatic-eos=false",
            "! queue max-size-time=2000000000 max-size-buffers=0 max-size-bytes=0 leaky=downstream",
            "! videoconvert n-threads=8",
            f"! video/x-raw,format=NV12,width=3840,height=2160,framerate={args.rate},colorimetry=bt709",
        ])
    parts.extend([
        "! identity name=composed_video silent=true",
        "! queue name=encode_queue max-size-buffers=2 max-size-time=0 max-size-bytes=0 leaky=downstream silent=true",
    ] + video_encoder_description(args) + [
        "! video/x-h264,profile=high,stream-format=byte-stream,alignment=au",
        "! identity name=encoded_video silent=true ! h264parse config-interval=-1",
        "! queue max-size-time=2000000000 max-size-buffers=0 max-size-bytes=0 ! mux.video",
    ])
    audio_flows = audio_flow_ids(args)
    if not audio_flows:
        raise ValueError("at least one audio flow is required")
    if len(audio_flows) == 1:
        parts.extend([
            f"mxlsrc domain={args.domain} audio-flow-id={audio_flows[0]} automatic-eos=false",
            "! queue max-size-time=2000000000 max-size-buffers=0 max-size-bytes=0 leaky=downstream",
            "! audioconvert ! audioresample quality=10",
            f"! audio/x-raw,rate={args.audio_rate_hz},channels=2",
            f"! avenc_aac bitrate={args.audio_bitrate_bps}",
            "! identity name=encoded_audio silent=true ! aacparse",
            "! queue max-size-time=2000000000 max-size-buffers=0 max-size-bytes=0 ! mux.audio",
        ])
    else:
        parts.extend([
            "audiomixer name=program_audio_mix force-live=true ignore-inactive-pads=true",
            "latency=100000000 output-buffer-duration=10000000",
            "! audioconvert",
            f"! audio/x-raw,format=F32LE,layout=interleaved,rate={args.audio_rate_hz},channels=2",
            # Sum in float, then apply a non-clipping safety limiter before AAC.
            # This preserves a single talker at unity while protecting the mix
            # when multiple isolated participant feeds speak together.
            "! rglimiter",
            f"! avenc_aac bitrate={args.audio_bitrate_bps}",
            "! identity name=encoded_audio silent=true ! aacparse",
            "! queue max-size-time=2000000000 max-size-buffers=0 max-size-bytes=0 ! mux.audio",
        ])
        for index, flow_id in enumerate(audio_flows):
            parts.extend([
                f"mxlsrc domain={args.domain} audio-flow-id={flow_id} automatic-eos=false",
                "! queue max-size-time=200000000 max-size-buffers=0 max-size-bytes=0 leaky=downstream",
                "! audioconvert ! audioresample quality=10",
                f"! audio/x-raw,format=F32LE,layout=interleaved,rate={args.audio_rate_hz},channels=2",
                f"! queue max-size-time=200000000 max-size-buffers=0 max-size-bytes=0 leaky=downstream ! program_audio_mix.sink_{index}",
            ])
    if inputs:
        asset_dir = pathlib.Path(args.assets_dir)
        for index, source in enumerate(inputs):
            parts.extend([
                f"mxlsrc domain={args.domain} video-flow-id={source.flow_id} automatic-eos=false",
                "! queue max-size-buffers=3 max-size-bytes=0 max-size-time=0 leaky=downstream",
                "! videoscale method=0 add-borders=false",
                f"! video/x-raw,width={tile_width},height={tile_height},framerate={args.rate}",
                "! videoconvert n-threads=2",
                f"! video/x-raw,format=BGRA,width={tile_width},height={tile_height},framerate={args.rate}",
                f"! identity name=source_{index} silent=true",
                "! queue max-size-buffers=2 leaky=downstream",
                f"! mix.sink_{index}",
            ])
        if len(inputs) == 8:
            brand = asset_dir / "brand.png"
            write_label_png(brand, tile_width, tile_height, args.brand_text)
            parts.extend([
                "videotestsrc is-live=true pattern=ball animation-mode=frames motion=sweep",
                "background-color=0xff071018 foreground-color=0xff26d9e9",
                f"! video/x-raw,format=BGRA,width={tile_width},height={tile_height},framerate={args.rate}",
                # Keep Cairo on the 1280x720 diagnostic tile. Running it after
                # the compositor forces a full 4K BGRA map/copy on every frame
                # and cuts CPU-only throughput well below real time.
                "! cairooverlay name=brand_clock",
                "! queue max-size-buffers=2 leaky=downstream ! mix.sink_8",
            ])
        else:
            parts.extend([
                "videotestsrc is-live=true pattern=black",
                f"! video/x-raw,format=BGRA,width=16,height=16,framerate={args.rate}",
                "! queue max-size-buffers=2 leaky=downstream",
                "! mix.sink_4",
            ])
    return " ".join(parts)


class Output:
    def __init__(self, args: argparse.Namespace, stream_key: str) -> None:
        self.args = args
        self.stream_key = stream_key
        self.url = destination_url(args.server_url, stream_key)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.pipeline = None
        self.state = "starting"
        self.revision = 0
        self.reconnects = 0
        self.last_error: str | None = None
        self.started_at = time.monotonic()
        self.last_mux_at = 0.0
        self.counts = {
            "composed": 0, "video": 0, "audio": 0,
            "mux_buffers": 0, "mux_bytes": 0, "sent_bytes": 0,
        }
        self.source_counts = {
            f"source_{index}": 0 for index, _source in enumerate(getattr(args, "input", None) or [])
        }
        self.rate_sample_at = self.started_at
        self.rate_sample_counts = {
            "composed": 0, "video": 0, "audio": 0,
        }
        self.bitrate_samples = collections.deque([
            (self.started_at, 0, 0),
        ])
        self.source_rate_sample_counts = dict(self.source_counts)
        self.rates = {"video_fps": 0.0, "audio_buffers_per_second": 0.0, "mux_kbps": 0.0}
        self.source_rates = {name: 0.0 for name in self.source_counts}
        self.source_lateness_window_ns = {name: 0 for name in self.source_counts}
        self.source_lateness_ms = {name: 0.0 for name in self.source_counts}
        self.timecode_updates = 0
        self.timecode_rate_sample_count = 0
        self.last_sent_at = 0.0
        self.resource_sample_at = self.started_at
        self.resource_cpu_seconds = time.process_time()
        self.process_cpu_cores = 0.0

    def _draw_clock(
        self, _overlay: object, context: object, timestamp: int, _duration: int
    ) -> None:
        """Draw timecode directly into each video frame without reparsing SVG."""
        elapsed = timestamp / 1_000_000_000 if timestamp >= 0 else time.monotonic() - self.started_at
        value = running_timecode(elapsed, self.args.rate)
        x_offset, y_offset = (0, 0)
        cairo = self._cairo
        pointer = hash(context)
        cairo.cairo_set_source_rgba(pointer, 0.027, 0.063, 0.094, 0.92)
        cairo.cairo_rectangle(pointer, x_offset + 110, y_offset + 190, 1060, 330)
        cairo.cairo_fill_preserve(pointer)
        cairo.cairo_set_source_rgb(pointer, 0.149, 0.851, 0.914)
        cairo.cairo_set_line_width(pointer, 4)
        cairo.cairo_stroke(pointer)
        cairo.cairo_select_font_face(pointer, b"monospace", 0, 1)
        cairo.cairo_set_font_size(pointer, 58)
        label = "LIVE TIMECODE"
        cairo.cairo_move_to(pointer, x_offset + 390, y_offset + 300)
        cairo.cairo_show_text(pointer, label.encode("ascii"))
        cairo.cairo_set_source_rgb(pointer, 0.957, 0.969, 1.0)
        cairo.cairo_set_font_size(pointer, 132)
        cairo.cairo_move_to(pointer, x_offset + 245, y_offset + 440)
        cairo.cairo_show_text(pointer, value.encode("ascii"))
        with self.lock:
            self.timecode_updates += 1

    def _redact(self, value: object) -> str:
        return str(value).replace(self.stream_key, "<redacted>").replace(
            self.url, "<redacted-destination>"
        )

    def _probe(self, name: str):
        def count(_pad, info):
            buffer = info.get_buffer()
            with self.lock:
                if name == "mux":
                    self.counts["mux_buffers"] += 1
                    self.counts["mux_bytes"] += 0 if buffer is None else buffer.get_size()
                    self.last_mux_at = time.monotonic()
                else:
                    self.counts[name] += 1
            return self.Gst.PadProbeReturn.OK

        return count

    def _start(self) -> None:
        pipeline = self.Gst.parse_launch(pipeline_description(self.args))
        sink = pipeline.get_by_name("destination")
        sink.set_property("location", self.url)
        if sink.find_property("peak-kbps") is not None:
            sink.set_property("peak-kbps", rtmp_peak_kbps(self.args))
        clock_overlay = pipeline.get_by_name("brand_clock")
        if clock_overlay is not None:
            self._cairo = ctypes.CDLL("libcairo.so.2")
            pointer = ctypes.c_void_p
            number = ctypes.c_double
            self._cairo.cairo_set_source_rgba.argtypes = [pointer, number, number, number, number]
            self._cairo.cairo_rectangle.argtypes = [pointer, number, number, number, number]
            self._cairo.cairo_fill_preserve.argtypes = [pointer]
            self._cairo.cairo_set_source_rgb.argtypes = [pointer, number, number, number]
            self._cairo.cairo_set_line_width.argtypes = [pointer, number]
            self._cairo.cairo_stroke.argtypes = [pointer]
            self._cairo.cairo_select_font_face.argtypes = [pointer, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
            self._cairo.cairo_set_font_size.argtypes = [pointer, number]
            self._cairo.cairo_move_to.argtypes = [pointer, number, number]
            self._cairo.cairo_show_text.argtypes = [pointer, ctypes.c_char_p]
            clock_overlay.connect("draw", self._draw_clock)
        for element_name, metric in (
            ("composed_video", "composed"), ("encoded_video", "video"),
            ("encoded_audio", "audio"), ("mux_bytes", "mux")
        ):
            pipeline.get_by_name(element_name).get_static_pad("src").add_probe(
                self.Gst.PadProbeType.BUFFER, self._probe(metric)
            )
        for source_name in self.source_counts:
            source = pipeline.get_by_name(source_name)
            if source is None:
                raise RuntimeError(f"missing pipeline telemetry point {source_name}")
            source.get_static_pad("src").add_probe(
                self.Gst.PadProbeType.BUFFER, self._source_probe(source_name)
            )
        if pipeline.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(self.Gst.State.NULL)
            raise RuntimeError("pipeline failed to enter PLAYING")
        with self.lock:
            self.pipeline = pipeline
            self.state = "streaming"
            self.last_error = None
            self.revision += 1

    def _stop_pipeline(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
        self.pipeline = None

    def _source_probe(self, name: str):
        def count(_pad, info):
            buffer = info.get_buffer()
            lateness_ns = 0
            pipeline = self.pipeline
            if buffer is not None and pipeline is not None:
                clock = pipeline.get_clock()
                if clock is not None and buffer.pts != self.Gst.CLOCK_TIME_NONE:
                    running_time = clock.get_time() - pipeline.get_base_time()
                    lateness_ns = max(0, running_time - buffer.pts)
            with self.lock:
                self.source_counts[name] += 1
                self.source_lateness_window_ns[name] = max(
                    self.source_lateness_window_ns[name], lateness_ns
                )
            return self.Gst.PadProbeReturn.OK

        return count

    def _sink_bytes(self) -> int | None:
        """Return bytes written by the RTMP sink, not merely bytes muxed."""
        pipeline = self.pipeline
        if pipeline is None:
            return None
        sink = pipeline.get_by_name("destination")
        if sink is None or sink.find_property("stats") is None:
            return None
        stats = sink.get_property("stats")
        if stats is None or not stats.has_field("out-bytes-total"):
            return None
        return int(stats.get_value("out-bytes-total"))

    def _send_queue_levels(self) -> tuple[int | None, int | None]:
        pipeline = self.pipeline
        if pipeline is None:
            return None, None
        queue = pipeline.get_by_name("send_queue")
        if queue is None:
            return None, None
        return (
            int(queue.get_property("current-level-time")),
            int(queue.get_property("current-level-bytes")),
        )

    def run(self) -> int:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        self.Gst = Gst
        Gst.init(None)
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                self._start()
                bus = self.pipeline.get_bus()
                while not self.stop_event.wait(0.25):
                    message = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                    if message is None:
                        continue
                    if message.type == Gst.MessageType.ERROR:
                        error, debug = message.parse_error()
                        detail = error.message if not debug else f"{error.message} ({debug})"
                        raise RuntimeError(self._redact(detail))
                    raise RuntimeError("pipeline reached EOS")
                backoff = 1.0
            except Exception as error:
                with self.lock:
                    self.reconnects += 1
                    self.state = "retrying"
                    self.last_error = self._redact(error)
                    self.revision += 1
                print(json.dumps({
                    "event": "retry", "reason": self.last_error, "backoff_s": backoff
                }), flush=True)
                self.stop_event.wait(backoff)
                backoff = min(self.args.max_retry_seconds, backoff * 2)
            finally:
                self._stop_pipeline()
        with self.lock:
            self.state = "stopped"
            self.revision += 1
        return 0

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            now = time.monotonic()
            mux_age = None if not self.last_mux_at else now - self.last_mux_at
            try:
                sent_bytes = self._sink_bytes()
            except Exception:
                sent_bytes = None
            if sent_bytes is not None:
                if sent_bytes > self.counts["sent_bytes"]:
                    self.last_sent_at = now
                self.counts["sent_bytes"] = sent_bytes
            current_bitrate_counts = (
                now, self.counts["mux_bytes"], self.counts["sent_bytes"]
            )
            if (
                self.bitrate_samples
                and (
                    current_bitrate_counts[1] < self.bitrate_samples[-1][1]
                    or current_bitrate_counts[2] < self.bitrate_samples[-1][2]
                )
            ):
                # A restarted pipeline resets cumulative counters. Rebuild the
                # window rather than reporting a negative or misleading rate.
                self.bitrate_samples.clear()
            self.bitrate_samples.append(current_bitrate_counts)
            cutoff = now - BITRATE_WINDOW_SECONDS
            while (
                len(self.bitrate_samples) >= 3
                and self.bitrate_samples[1][0] <= cutoff
            ):
                self.bitrate_samples.popleft()
            bitrate_start = self.bitrate_samples[0]
            bitrate_elapsed = now - bitrate_start[0]
            bitrate_window_ready = bitrate_elapsed >= BITRATE_WINDOW_SECONDS
            if bitrate_window_ready:
                self.rates["mux_kbps"] = (
                    (self.counts["mux_bytes"] - bitrate_start[1])
                    * 8 / bitrate_elapsed / 1000
                )
                self.rates["sent_kbps"] = (
                    (self.counts["sent_bytes"] - bitrate_start[2])
                    * 8 / bitrate_elapsed / 1000
                )
            sent_age = None if not self.last_sent_at else now - self.last_sent_at
            send_queue_ns, send_queue_bytes = self._send_queue_levels()
            elapsed = now - self.rate_sample_at
            if elapsed >= 1.0:
                cpu_seconds = time.process_time()
                resource_elapsed = max(now - self.resource_sample_at, 0.001)
                self.process_cpu_cores = (
                    cpu_seconds - self.resource_cpu_seconds
                ) / resource_elapsed
                self.resource_sample_at = now
                self.resource_cpu_seconds = cpu_seconds
                self.rates = {
                    "composed_fps": (
                        self.counts["composed"] - self.rate_sample_counts["composed"]
                    ) / elapsed,
                    "video_fps": (self.counts["video"] - self.rate_sample_counts["video"]) / elapsed,
                    "audio_buffers_per_second": (
                        self.counts["audio"] - self.rate_sample_counts["audio"]
                    ) / elapsed,
                    "mux_kbps": self.rates.get("mux_kbps", 0.0),
                    "sent_kbps": self.rates.get("sent_kbps", 0.0),
                }
                self.rate_sample_at = now
                self.rate_sample_counts = {
                    "composed": self.counts["composed"],
                    "video": self.counts["video"],
                    "audio": self.counts["audio"],
                }
                self.source_rates = {
                    name: (count - self.source_rate_sample_counts[name]) / elapsed
                    for name, count in self.source_counts.items()
                }
                self.source_rate_sample_counts = dict(self.source_counts)
                self.source_lateness_ms = {
                    name: lateness / 1_000_000
                    for name, lateness in self.source_lateness_window_ns.items()
                }
                self.source_lateness_window_ns = {
                    name: 0 for name in self.source_lateness_window_ns
                }
                self.rates["timecode_fps"] = (
                    self.timecode_updates - self.timecode_rate_sample_count
                ) / elapsed
                self.timecode_rate_sample_count = self.timecode_updates
            expected_fps = rate_as_float(self.args.rate)
            source_lateness_fail_ns = (
                COMPOSITOR_LATENCY_NS + round(1_000_000_000 / expected_fps)
            )
            minimum_video_fps = expected_fps * 0.90
            minimum_source_fps = expected_fps * 0.80
            minimum_audio_buffers_per_second = (
                self.args.audio_rate_hz / AAC_FRAME_SAMPLES * 0.90
            )
            minimum_mux_kbps = self.args.video_bitrate_kbps * 0.70
            checks = {
                "pipeline_streaming": self.state == "streaming",
                "encoded_video_cadence": self.rates["video_fps"] >= minimum_video_fps,
                "audio_advancing": (
                    self.rates["audio_buffers_per_second"] >= minimum_audio_buffers_per_second
                ),
                "mux_fresh": mux_age is not None and mux_age < 5.0,
                "mux_bitrate": (
                    bitrate_window_ready and self.rates["mux_kbps"] >= minimum_mux_kbps
                ),
                "destination_fresh": sent_age is not None and sent_age < 5.0,
                "destination_bitrate": (
                    bitrate_window_ready
                    and self.rates.get("sent_kbps", 0.0) >= minimum_mux_kbps
                ),
                "send_queue_not_backlogged": (
                    send_queue_ns is not None and send_queue_ns < SEND_QUEUE_READY_NS
                ),
                "sources_within_compositor_budget": all(
                    lateness * 1_000_000 <= source_lateness_fail_ns
                    for lateness in self.source_lateness_ms.values()
                ),
                "all_sources_advancing": all(
                    rate >= minimum_source_fps for rate in self.source_rates.values()
                ),
                "diagnostic_clock_advancing": (
                    not self.source_counts
                    or len(self.source_counts) != 8
                    or self.rates.get("timecode_fps", 0.0) >= minimum_video_fps
                ),
                "no_active_error": self.last_error is None,
            }
            failed_checks = [name for name, passed in checks.items() if not passed]
            warnings = []
            if any(
                lateness * 1_000_000 > SOURCE_LATENESS_WARN_NS
                for lateness in self.source_lateness_ms.values()
            ):
                warnings.append("source_lateness_elevated")
            ready = not failed_checks
            return {
                "name": "program-rtmps-output", "kind": "output",
                "destination": self.args.destination_label, "state": self.state,
                "ready": ready, "revision": self.revision,
                "health_checks": checks,
                "failed_checks": failed_checks,
                "warnings": warnings,
                "reconnects": self.reconnects, "last_error": self.last_error,
                "uptime_seconds": round(now - self.started_at, 3),
                "metrics": {
                    **self.counts,
                    "last_mux_age_seconds": None if mux_age is None else round(mux_age, 3),
                    "last_sent_age_seconds": None if sent_age is None else round(sent_age, 3),
                    "video_bitrate_kbps": self.args.video_bitrate_kbps,
                    "audio_bitrate_bps": self.args.audio_bitrate_bps,
                    "audio_rate_hz": self.args.audio_rate_hz,
                    "resolution": "3840x2160", "frame_rate": self.args.rate,
                    "queue_bound_ns": QUEUE_NS,
                    "encode_queue_buffers": 2,
                    "timecode_updates": self.timecode_updates,
                    "source_frames": dict(self.source_counts),
                    "source_fps": {
                        name: round(value, 3) for name, value in self.source_rates.items()
                    },
                    "source_lateness_ms": {
                        name: round(value, 3)
                        for name, value in self.source_lateness_ms.items()
                    },
                    "source_lateness_max_ms": round(
                        max(self.source_lateness_ms.values(), default=0.0), 3
                    ),
                    "send_queue_level_ms": (
                        None if send_queue_ns is None else round(send_queue_ns / 1_000_000, 3)
                    ),
                    "send_queue_level_bytes": send_queue_bytes,
                    "send_queue_ready_ms": SEND_QUEUE_READY_NS / 1_000_000,
                    "source_lateness_warn_ms": SOURCE_LATENESS_WARN_NS / 1_000_000,
                    "source_lateness_fail_ms": round(
                        source_lateness_fail_ns / 1_000_000, 3
                    ),
                    "bitrate_window_seconds": round(bitrate_elapsed, 3),
                    "bitrate_window_required_seconds": BITRATE_WINDOW_SECONDS,
                    "minimum_video_fps": round(minimum_video_fps, 3),
                    "minimum_source_fps": round(minimum_source_fps, 3),
                    "minimum_audio_buffers_per_second": round(
                        minimum_audio_buffers_per_second, 3
                    ),
                    "minimum_mux_kbps": round(minimum_mux_kbps, 3),
                    "process_cpu_cores": round(self.process_cpu_cores, 3),
                    "process_rss_mib": round(current_rss_mib(), 3),
                    **{name: round(value, 3) for name, value in self.rates.items()},
                },
            }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--domain", default="/mxl-domain")
    result.add_argument("--video-flow")
    result.add_argument("--input", action="append", type=parse_input)
    result.add_argument("--audio-flow", action="append", required=True)
    result.add_argument("--stream-key-file", type=pathlib.Path, required=True)
    result.add_argument("--server-url", default="rtmps://a.rtmps.youtube.com/live2")
    result.add_argument("--destination-label", default="YouTube Live")
    result.add_argument("--brand-text", default="MXL LIVE | 4K")
    result.add_argument("--assets-dir", default="/tmp/mxl-youtube-4k-assets")
    result.add_argument("--video-encoder", default="x264enc")
    result.add_argument("--video-bitrate-kbps", type=int, default=30000)
    result.add_argument("--audio-bitrate-bps", type=int, default=128000)
    result.add_argument("--audio-rate-hz", type=int, default=48000)
    result.add_argument("--rate", default="30000/1001")
    result.add_argument("--bind", default="127.0.0.1")
    result.add_argument("--port", type=int, default=18120)
    result.add_argument("--max-retry-seconds", type=float, default=30.0)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.bind not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("refusing non-loopback health bind")
    if not 1000 <= args.video_bitrate_kbps <= 100000:
        raise SystemExit("video bitrate is outside the allowed range")
    if bool(args.video_flow) == bool(args.input):
        raise SystemExit("choose one --video-flow or exactly four/eight --input values")
    if args.input and len(args.input) not in {4, 8}:
        raise SystemExit("direct 4K canvas output requires exactly four or eight --input values")
    try:
        key = read_stream_key(args.stream_key_file)
        destination_url(args.server_url, key)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    output = Output(args, key)
    server = ThreadingHTTPServer((args.bind, args.port), handler_for_snapshot(output.snapshot))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    signal.signal(signal.SIGTERM, lambda *_: output.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: output.stop_event.set())
    print(json.dumps({
        "event": "launch", "destination": args.destination_label,
        "resolution": "3840x2160", "frame_rate": args.rate,
        "video_bitrate_kbps": args.video_bitrate_kbps,
    }), flush=True)
    try:
        return output.run()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
