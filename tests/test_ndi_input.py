import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "inputs" / "ndi" / "ndi_input.py"
SPEC = importlib.util.spec_from_file_location("ndi_input", MODULE_PATH)
ndi = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ndi)
PIPELINE_PATH = ROOT / "inputs" / "ndi" / "ndi_pipeline.py"
PIPELINE_SPEC = importlib.util.spec_from_file_location("ndi_pipeline", PIPELINE_PATH)
pipeline_runner = importlib.util.module_from_spec(PIPELINE_SPEC)
assert PIPELINE_SPEC.loader is not None
PIPELINE_SPEC.loader.exec_module(pipeline_runner)


class FakeProcess:
    def __init__(self, argv, **_kwargs):
        self.argv = argv
        self.pid = 43211
        self.returncode = None

    def poll(self):
        return self.returncode


def command(**overrides):
    values = {
        "domain": "/mxl-domain",
        "source_name": "CAMERA (Program)",
        "source_address": None,
        "video_flow": "video-id",
        "audio_flow": "audio-id",
        "audio_mode": "optional",
        "width": 1920,
        "height": 1080,
        "rate_numerator": 30,
        "rate_denominator": 1,
        "rate_mode": "normalize",
        "timestamp_mode": "timestamp",
        "pacing_mode": "none",
        "convert_threads": 4,
        "audio_channels": 2,
    }
    values.update(overrides)
    return ndi.gst_command(**values)


class CommandTests(unittest.TestCase):
    def test_pipeline_description_quotes_untrusted_property_values(self):
        value = 'CAMERA ! fakesrc name="bad"'
        rendered = pipeline_runner.description(["ndisrc", f"ndi-name={value}", "!", "fakesink"])
        self.assertEqual(rendered, 'ndisrc ndi-name="CAMERA ! fakesrc name=\\"bad\\"" ! fakesink')

    def test_normalizes_rate_before_scale_and_threaded_conversion(self):
        argv = command()
        rate = argv.index("videorate")
        scale = argv.index("videoscale")
        convert = argv.index("videoconvert")
        sink = argv.index("mxlsink")
        self.assertLess(rate, scale)
        self.assertLess(scale, convert)
        self.assertLess(convert, sink)
        self.assertIn("video/x-raw,framerate=30/1", argv)
        self.assertIn("n-threads=4", argv)
        self.assertIn("video/x-raw,format=v210", argv)
        self.assertIn("timestamp-mode=timestamp", argv)
        self.assertNotIn("sh", argv)

    def test_direct_address_and_fractional_rate_are_argv_only(self):
        argv = command(
            source_name=None,
            source_address="192.0.2.10:5961",
            rate_numerator=30000,
            rate_denominator=1001,
        )
        self.assertIn("url-address=192.0.2.10:5961", argv)
        self.assertFalse(any(value.startswith("ndi-name=") for value in argv))
        self.assertIn("video/x-raw,framerate=30000/1001", argv)

    def test_clocked_timestamp_mode_is_explicitly_supported(self):
        self.assertIn("timestamp-mode=clocked", command(timestamp_mode="clocked"))

    def test_passthrough_rate_omits_videorate_but_requires_target_caps(self):
        argv = command(rate_mode="passthrough")
        self.assertNotIn("videorate", argv)
        self.assertIn("video/x-raw,framerate=30/1", argv)
        self.assertLess(argv.index("video/x-raw,framerate=30/1"), argv.index("videoscale"))

    def test_clock_sync_paces_after_conversion_before_mxl(self):
        argv = command(pacing_mode="clock-sync")
        self.assertLess(argv.index("videoconvert"), argv.index("clocksync"))
        self.assertLess(argv.index("clocksync"), argv.index("mxlsink"))
        self.assertIn("sync-to-first=true", argv)

    def test_deterministic_v210_retimestamp_precedes_clock_sync(self):
        argv = command(pacing_mode="retimestamp-clock-sync")
        self.assertLess(argv.index("videoconvert"), argv.index("identity"))
        self.assertLess(argv.index("identity"), argv.index("clocksync"))
        self.assertLess(argv.index("clocksync"), argv.index("mxlsink"))
        self.assertIn("datarate=165888000", argv)
        self.assertIn("single-segment=true", argv)

    def test_optional_audio_is_normalized_and_disabled_audio_is_absent(self):
        argv = command(audio_channels=8)
        self.assertIn("audioresample", argv)
        self.assertIn("audiorate", argv)
        self.assertIn(
            "audio/x-raw,format=F32LE,layout=interleaved,rate=48000,channels=8,channel-mask=(bitmask)0xff",
            argv,
        )
        video_only = command(audio_mode="disabled", audio_flow=None)
        self.assertNotIn("demux.audio", video_only)
        self.assertEqual(video_only.count("mxlsink"), 1)

    def test_invalid_identifiers_and_settings_fail_closed(self):
        for flow in ("", "../escape", "a/b", "a\\b"):
            with self.subTest(flow=flow), self.assertRaises(ValueError):
                command(video_flow=flow)
        with self.assertRaises(ValueError):
            command(audio_mode="required", audio_flow=None)
        with self.assertRaises(ValueError):
            command(timestamp_mode="guess")
        with self.assertRaises(ValueError):
            command(pacing_mode="guess")
        with self.assertRaises(ValueError):
            command(rate_mode="guess")
        with self.assertRaises(ValueError):
            command(source_name="CAMERA", source_address="192.0.2.10:5961")
        with self.assertRaises(ValueError):
            command(source_name=None, source_address=None)

    def test_plugin_preflight_fails_closed(self):
        seen = []

        def run(argv, **_kwargs):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 1 if argv[-1] == "videorate" else 0)

        with self.assertRaisesRegex(RuntimeError, "videorate"):
            ndi.plugin_preflight(run)
        self.assertEqual(seen[0], ["gst-inspect-1.0", "ndisrc"])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.domain = pathlib.Path(self.tmp.name)
        self.processes = []

        def popen(argv, **kwargs):
            process = FakeProcess(argv, **kwargs)
            self.processes.append(process)
            return process

        self.args = argparse.Namespace(
            domain=self.domain,
            source_name="CAMERA (Program)",
            source_address=None,
            video_flow="video",
            audio_flow="audio",
            audio_mode="optional",
            audio_channels=2,
            width=1920,
            height=1080,
            rate_numerator=30,
            rate_denominator=1,
            rate_mode="normalize",
            timestamp_mode="timestamp",
            pacing_mode="none",
            convert_threads=4,
            max_restarts=1,
            restart_window_seconds=60.0,
            poll_seconds=0.01,
            stop_timeout_seconds=0.1,
        )
        self.module = ndi.NDIInput(
            self.args,
            popen=popen,
            clock=lambda: 10.0,
            wall_clock=lambda: 1_000.0,
        )

    def flow(self, flow_id, media_type, age=0):
        directory = self.domain / f"{flow_id}.mxl-flow"
        directory.mkdir()
        (directory / "flow_def.json").write_text(json.dumps({
            "id": flow_id,
            "media_type": media_type,
        }))
        data = directory / "data"
        data.write_bytes(b"grain")
        now = 1_000.0
        os.utime(data, (now - age, now - age))
        return directory

    def test_optional_audio_does_not_block_video_readiness(self):
        self.module.start()
        self.assertTrue(self.processes[-1].argv[1].endswith("ndi_pipeline.py"))
        self.assertEqual(self.processes[-1].argv[2], "--")
        self.assertNotIn("gst-launch-1.0", self.processes[-1].argv)
        self.assertEqual(self.module.check(), "wait")
        self.flow("video", "video/v210")
        self.assertEqual(self.module.check(), "healthy")
        state = self.module.snapshot()
        self.assertEqual((state["state"], state["ready"]), ("running", True))
        self.assertFalse(state["metrics"]["audio_output_available"])
        self.assertNotIn("CAMERA", str(state))
        self.assertNotIn("video-id", str(state))

    def test_required_audio_blocks_until_both_flows_exist(self):
        self.args.audio_mode = "required"
        module = ndi.NDIInput(
            self.args,
            popen=self.module.popen,
            clock=lambda: 10.0,
            wall_clock=lambda: 1_000.0,
        )
        module.start()
        self.flow("video", "video/v210")
        self.assertEqual(module.check(), "wait")
        self.flow("audio", "audio/float32")
        self.assertEqual(module.check(), "healthy")

    def test_presence_only_wrong_type_and_stale_output_are_not_ready(self):
        module = ndi.NDIInput(
            self.args,
            popen=self.module.popen,
            clock=lambda: 10.0,
            wall_clock=lambda: 1_000.0,
        )
        module.start()
        (self.domain / "video.mxl-flow").mkdir()
        self.assertEqual(module.check(), "wait")
        (self.domain / "video.mxl-flow").rmdir()
        stale = self.flow("video", "video/v210", age=61)
        self.assertEqual(module.check(), "wait")
        os.utime(stale / "data", (1_000.0, 1_000.0))
        (stale / "flow_def.json").write_text(json.dumps({
            "id": "video",
            "media_type": "audio/float32",
        }))
        self.assertEqual(module.check(), "wait")
        (stale / "flow_def.json").write_text(json.dumps({
            "id": "video",
            "media_type": "video/v210",
        }))
        self.assertEqual(module.check(), "healthy")

    def test_exit_requests_one_bounded_restart_then_fails_closed(self):
        self.module.start()
        self.processes[-1].returncode = 1
        self.assertEqual(self.module.check(), "restart")
        self.module.start(restart=True)
        self.assertEqual(self.module.snapshot()["restarts"], 1)
        self.processes[-1].returncode = 1
        with self.assertRaisesRegex(RuntimeError, "restart budget exhausted"):
            self.module.start(restart=True)
        self.assertEqual(self.module.snapshot()["state"], "failed")

    def test_missing_domain_fails_before_process_launch(self):
        self.args.domain = self.domain / "missing"
        module = ndi.NDIInput(self.args, popen=self.module.popen)
        with self.assertRaisesRegex(RuntimeError, "domain"):
            module.start()
        self.assertEqual(self.processes, [])


if __name__ == "__main__":
    unittest.main()
