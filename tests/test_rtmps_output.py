#!/usr/bin/env python3
import argparse
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "outputs" / "rtmp" / "rtmps_output.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("rtmps_output", MODULE_PATH)
rtmps = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(rtmps)


class RTMPSOutputTests(unittest.TestCase):
    def args(self):
        return argparse.Namespace(
            domain="/mxl-domain", video_flow="video-id", audio_flow="audio-id",
            input=None,
            rate="30/1", video_encoder="nvh264enc",
            video_bitrate_kbps=30000, audio_bitrate_bps=128000,
            audio_rate_hz=44100,
            server_url="rtmps://a.rtmps.youtube.com/live2",
            destination_label="YouTube Live",
        )

    def test_pipeline_is_4k_youtube_profile_and_contains_no_destination(self):
        description = rtmps.pipeline_description(self.args())
        self.assertIn("width=3840,height=2160,framerate=30/1", description)
        self.assertIn("nvh264enc", description)
        self.assertIn("bitrate=30000", description)
        self.assertIn("rc-mode=cbr", description)
        self.assertIn("gop-size=60", description)
        self.assertIn("bframes=2", description)
        self.assertIn("strict-gop=true", description)
        self.assertIn("h264parse config-interval=-1", description)
        self.assertIn("avenc_aac bitrate=128000", description)
        self.assertIn("identity name=composed_video", description)
        self.assertIn("queue name=encode_queue max-size-buffers=2", description)
        self.assertIn("leaky=downstream", description)
        self.assertIn("flvmux name=mux latency=300000000", description)
        self.assertIn("queue name=send_queue max-size-time=2000000000", description)
        self.assertIn("rtmp2sink name=destination sync=false async=false chunk-size=4096", description)
        self.assertIn("audio/x-raw,rate=44100,channels=2", description)
        self.assertIn("rtmp2sink name=destination", description)
        self.assertNotIn("rtmpsink name=destination", description)
        self.assertNotIn("youtube.com", description)
        self.assertNotIn("secret", description)

    def test_cpu_encoder_uses_x264_properties_not_nvenc_properties(self):
        args = self.args()
        args.video_encoder = "x264enc"
        description = rtmps.pipeline_description(args)
        self.assertIn("x264enc name=video_encoder bitrate=30000", description)
        self.assertIn("speed-preset=ultrafast", description)
        self.assertIn("pass=cbr", description)
        self.assertIn("key-int-max=60", description)
        self.assertIn("vbv-buf-capacity=2000", description)
        self.assertIn("threads=16", description)
        self.assertIn("sliced-threads=true", description)
        self.assertIn('option-string="nal-hrd=cbr:force-cfr=1"', description)
        self.assertNotIn("rc-mode=", description)
        self.assertNotIn("gop-size=", description)
        self.assertNotIn("strict-gop=", description)

    def test_rtmp_pacing_has_two_times_stream_rate_headroom(self):
        self.assertEqual(rtmps.rtmp_peak_kbps(self.args()), 60256)

    def test_direct_canvas_has_eight_sources_and_no_intermediate_mxl_sink(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args()
            args.video_flow = None
            args.input = [rtmps.Input(f"SOURCE {i + 1}", f"flow-{i + 1}") for i in range(8)]
            args.assets_dir = directory
            args.brand_text = "MXL LIVE | 4K"
            description = rtmps.pipeline_description(args)
            self.assertEqual(description.count("video-flow-id="), 8)
            self.assertEqual(description.count("identity name=source_"), 8)
            self.assertIn("compositor name=mix", description)
            self.assertIn("ignore-inactive-pads=true latency=100000000", description)
            self.assertIn("mix.sink_8", description)
            self.assertIn("sink_8::xpos=2560", description)
            self.assertIn("sink_8::ypos=1440", description)
            self.assertIn("sink_8::width=1280", description)
            self.assertIn("sink_8::height=720", description)
            self.assertIn("sink_8::zorder=9", description)
            self.assertIn(
                "videotestsrc is-live=true pattern=ball animation-mode=frames motion=sweep",
                description,
            )
            self.assertIn("cairooverlay name=brand_clock", description)
            self.assertLess(
                description.index(
                    "videotestsrc is-live=true pattern=ball animation-mode=frames motion=sweep"
                ),
                description.index("cairooverlay name=brand_clock"),
            )
            self.assertLess(
                description.index("cairooverlay name=brand_clock"),
                description.rindex("mix.sink_8"),
            )
            self.assertNotIn("mxlsink", description)
            self.assertNotIn("gdkpixbufoverlay", description)
            self.assertIn("videoscale method=0", description)

    def test_live_tile_timecode_uses_requested_cadence(self):
        self.assertEqual(rtmps.running_timecode(0.0, "30000/1001"), "00:00:00:00")
        self.assertEqual(rtmps.running_timecode(61.5, "30/1"), "00:01:01:15")
        svg = rtmps.timecode_svg(61.5, "30/1")
        self.assertIn("LIVE TIMECODE", svg)
        self.assertIn("00:01:01:15", svg)

    def test_direct_four_up_canvas_uses_native_1080p_quadrants(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args()
            args.video_flow = None
            args.input = [rtmps.Input(f"SOURCE {i + 1}", f"flow-{i + 1}") for i in range(4)]
            args.assets_dir = directory
            args.brand_text = "MXL LIVE | 4K"
            description = rtmps.pipeline_description(args)
            self.assertEqual(description.count("video-flow-id="), 4)
            self.assertIn("sink_3::xpos=1920 sink_3::ypos=1080", description)
            self.assertIn("sink_3::width=1920 sink_3::height=1080", description)
            self.assertIn("width=1920,height=1080,framerate=30/1", description)
            self.assertIn("videotestsrc is-live=true pattern=black", description)
            self.assertIn("sink_4::width=3840 sink_4::height=2160", description)
            self.assertIn("sink_4::zorder=0", description)

    def test_readiness_requires_every_source_clock_and_mux_to_advance(self):
        args = self.args()
        args.video_flow = None
        args.input = [rtmps.Input(f"SOURCE {i + 1}", f"flow-{i + 1}") for i in range(8)]
        with mock.patch.object(rtmps.time, "monotonic", return_value=100.0):
            output = rtmps.Output(args, "abc-def")
        output.state = "streaming"
        output.last_mux_at = 101.9
        output.counts.update(composed=60, video=60, audio=100, mux_buffers=60, mux_bytes=7_500_000)
        output.source_counts = {name: 60 for name in output.source_counts}
        output.source_lateness_window_ns = {
            name: 42_000_000 for name in output.source_counts
        }
        output.timecode_updates = 60
        with mock.patch.object(output, "_sink_bytes", return_value=7_500_000), \
             mock.patch.object(output, "_send_queue_levels", return_value=(0, 0)), \
             mock.patch.object(rtmps.time, "monotonic", return_value=102.0):
            snapshot = output.snapshot()
        self.assertTrue(snapshot["ready"])
        self.assertEqual(snapshot["failed_checks"], [])
        self.assertAlmostEqual(snapshot["metrics"]["video_fps"], 30.0)
        self.assertAlmostEqual(snapshot["metrics"]["mux_kbps"], 30000.0)
        self.assertAlmostEqual(snapshot["metrics"]["sent_kbps"], 30000.0)
        self.assertEqual(snapshot["metrics"]["source_lateness_max_ms"], 42.0)
        self.assertTrue(all(rate == 30.0 for rate in snapshot["metrics"]["source_fps"].values()))

        output.source_counts["source_4"] = 60
        output.counts.update(
            composed=120, video=120, audio=200, mux_buffers=120, mux_bytes=15_000_000
        )
        output.timecode_updates = 120
        output.last_mux_at = 103.9
        for name in output.source_counts:
            if name != "source_4":
                output.source_counts[name] = 120
        with mock.patch.object(output, "_sink_bytes", return_value=15_000_000), \
             mock.patch.object(output, "_send_queue_levels", return_value=(0, 0)), \
             mock.patch.object(rtmps.time, "monotonic", return_value=104.0):
            snapshot = output.snapshot()
        self.assertFalse(snapshot["ready"])
        self.assertIn("all_sources_advancing", snapshot["failed_checks"])

    def test_readiness_rejects_low_bitrate_and_frozen_clock(self):
        args = self.args()
        args.video_flow = None
        args.input = [rtmps.Input(f"SOURCE {i + 1}", f"flow-{i + 1}") for i in range(8)]
        with mock.patch.object(rtmps.time, "monotonic", return_value=200.0):
            output = rtmps.Output(args, "abc-def")
        output.state = "streaming"
        output.last_mux_at = 201.9
        output.counts.update(composed=60, video=60, audio=100, mux_buffers=60, mux_bytes=250_000)
        output.source_counts = {name: 60 for name in output.source_counts}
        with mock.patch.object(output, "_sink_bytes", return_value=250_000), \
             mock.patch.object(rtmps.time, "monotonic", return_value=202.0):
            snapshot = output.snapshot()
        self.assertFalse(snapshot["ready"])
        self.assertIn("mux_bitrate", snapshot["failed_checks"])
        self.assertIn("destination_bitrate", snapshot["failed_checks"])
        self.assertIn("diagnostic_clock_advancing", snapshot["failed_checks"])
        self.assertIn("send_queue_not_backlogged", snapshot["failed_checks"])

    def test_readiness_rejects_send_queue_backlog(self):
        args = self.args()
        with mock.patch.object(rtmps.time, "monotonic", return_value=400.0):
            output = rtmps.Output(args, "abc-def")
        output.state = "streaming"
        output.last_mux_at = 401.9
        output.counts.update(composed=60, video=60, audio=94, mux_bytes=7_500_000)
        with mock.patch.object(output, "_sink_bytes", return_value=7_500_000), \
             mock.patch.object(
                 output, "_send_queue_levels", return_value=(600_000_000, 2_000_000)
             ), \
             mock.patch.object(rtmps.time, "monotonic", return_value=402.0):
            snapshot = output.snapshot()
        self.assertFalse(snapshot["ready"])
        self.assertIn("send_queue_not_backlogged", snapshot["failed_checks"])
        self.assertEqual(snapshot["metrics"]["send_queue_level_ms"], 600.0)

    def test_readiness_rejects_source_outside_compositor_budget(self):
        args = self.args()
        args.video_flow = None
        args.input = [rtmps.Input("SOURCE 1", "flow-1")]
        with mock.patch.object(rtmps.time, "monotonic", return_value=500.0):
            output = rtmps.Output(args, "abc-def")
        output.state = "streaming"
        output.last_mux_at = 501.9
        output.counts.update(composed=60, video=60, audio=94, mux_bytes=7_500_000)
        output.source_counts["source_0"] = 60
        output.source_lateness_window_ns["source_0"] = 95_000_000
        with mock.patch.object(output, "_sink_bytes", return_value=7_500_000), \
             mock.patch.object(output, "_send_queue_levels", return_value=(0, 0)), \
             mock.patch.object(rtmps.time, "monotonic", return_value=502.0):
            snapshot = output.snapshot()
        self.assertFalse(snapshot["ready"])
        self.assertIn("sources_within_compositor_budget", snapshot["failed_checks"])
        self.assertEqual(snapshot["metrics"]["source_lateness_max_ms"], 95.0)

    def test_readiness_requires_audio_at_aac_frame_cadence(self):
        args = self.args()
        args.audio_rate_hz = 48000
        with mock.patch.object(rtmps.time, "monotonic", return_value=300.0):
            output = rtmps.Output(args, "abc-def")
        output.state = "streaming"

        def window(end, audio):
            output.last_mux_at = end - 0.1
            output.counts.update(
                composed=output.counts["composed"] + 60,
                video=output.counts["video"] + 60,
                audio=output.counts["audio"] + audio,
                mux_buffers=output.counts["mux_buffers"] + 60,
                mux_bytes=output.counts["mux_bytes"] + 7_500_000,
            )
            with mock.patch.object(output, "_sink_bytes", return_value=output.counts["mux_bytes"]), \
                 mock.patch.object(rtmps.time, "monotonic", return_value=end):
                return output.snapshot()

        # 48 kHz AAC is 46.875 frames/s; two seconds is about 94 frames.
        snapshot = window(302.0, 94)
        self.assertNotIn("audio_advancing", snapshot["failed_checks"])

        # Half-rate Program audio advances, but it is not a healthy 48 kHz stream.
        snapshot = window(304.0, 47)
        self.assertFalse(snapshot["ready"])
        self.assertIn("audio_advancing", snapshot["failed_checks"])
        self.assertAlmostEqual(snapshot["metrics"]["minimum_audio_buffers_per_second"], 42.188)

    def test_stream_key_requires_private_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "key"
            path.write_text("abcd-efgh-ijkl\n", encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(rtmps.read_stream_key(path), "abcd-efgh-ijkl")
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "group or other"):
                rtmps.read_stream_key(path)

    def test_destination_requires_clean_rtmps_base(self):
        self.assertEqual(
            rtmps.destination_url("rtmps://a.rtmps.youtube.com/live2", "abc-def"),
            "rtmps://a.rtmps.youtube.com/live2/abc-def",
        )
        for value in (
            "rtmp://example/live", "rtmps://user@example/live", "rtmps://example/live?q=1"
        ):
            with self.assertRaises(ValueError):
                rtmps.destination_url(value, "abc-def")
        for key in ("", "key/with/slashes", "key with spaces", "x" * 201):
            with self.assertRaises(ValueError):
                rtmps.destination_url("rtmps://a.rtmps.youtube.com/live2", key)


if __name__ == "__main__":
    unittest.main()
