#!/usr/bin/env python3
import argparse
import importlib.util
import pathlib
import sys
import tempfile
import unittest


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

    def test_direct_canvas_has_eight_sources_and_no_intermediate_mxl_sink(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args()
            args.video_flow = None
            args.input = [rtmps.Input(f"SOURCE {i + 1}", f"flow-{i + 1}") for i in range(8)]
            args.assets_dir = directory
            args.brand_text = "MXL LIVE | 4K"
            description = rtmps.pipeline_description(args)
            self.assertEqual(description.count("video-flow-id="), 8)
            self.assertIn("compositor name=mix", description)
            self.assertIn("mix.sink_8", description)
            self.assertIn(
                "videotestsrc is-live=true pattern=ball animation-mode=frames motion=sweep",
                description,
            )
            self.assertIn("cairooverlay name=brand_clock", description)
            self.assertNotIn("mxlsink", description)
            self.assertTrue((pathlib.Path(directory) / "brand.png").is_file())

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
