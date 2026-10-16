import argparse
import importlib.util
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "outputs" / "ndi" / "ndi_output.py"
SPEC = importlib.util.spec_from_file_location("ndi_output", MODULE_PATH)
ndi = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(ndi)


class FakeProcess:
    def __init__(self, argv, **_kwargs):
        self.argv = argv
        self.pid = 43210
        self.returncode = None

    def poll(self):
        return self.returncode


class CommandTests(unittest.TestCase):
    def test_pipeline_pairs_program_video_and_audio_without_a_shell(self):
        command = ndi.gst_command("/mxl-domain", "video-id", "audio-id", "Program")
        self.assertEqual(command[:3], ["gst-launch-1.0", "-e", "-q"])
        self.assertEqual(command.count("mxlsrc"), 2)
        self.assertIn("ndisinkcombiner", command)
        self.assertIn("combiner.video", command)
        self.assertIn("combiner.audio", command)
        self.assertIn("video/x-raw,format=UYVY", command)
        self.assertIn("audio/x-raw,format=F32LE,rate=48000,channels=2,layout=interleaved", command)
        self.assertIn("audiobuffersplit", command)
        self.assertIn("output-buffer-duration=1/30", command)
        self.assertNotIn("sh", command)

    def test_unsafe_or_blank_identifiers_are_refused(self):
        for flow in ("", "../escape", "a/b", "a\\b"):
            with self.subTest(flow=flow), self.assertRaises(ValueError):
                ndi.gst_command("/mxl-domain", flow, "audio", "Program")
        with self.assertRaises(ValueError):
            ndi.gst_command("/mxl-domain", "video", "audio", " ")

    def test_plugin_preflight_fails_closed(self):
        seen = []

        def run(argv, **_kwargs):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 1 if argv[-1] == "ndisinkcombiner" else 0)

        with self.assertRaisesRegex(RuntimeError, "ndisinkcombiner"):
            ndi.plugin_preflight(run)
        self.assertEqual(seen[0], ["gst-inspect-1.0", "mxlsrc"])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.domain = pathlib.Path(self.tmp.name)
        self.video = self.domain / "video.mxl-flow"
        self.audio = self.domain / "audio.mxl-flow"
        self.video.mkdir()
        self.audio.mkdir()
        self.now = 0.0
        self.processes = []

        def popen(argv, **kwargs):
            process = FakeProcess(argv, **kwargs)
            self.processes.append(process)
            return process

        self.args = argparse.Namespace(
            domain=self.domain,
            video_flow="video",
            audio_flow="audio",
            ndi_name="Program",
            verification_source_name="HOST (Program)",
            verification_seconds=0.01,
            verification_connect_timeout_ms=10,
            max_restarts=1,
            restart_window_seconds=60.0,
            poll_seconds=0.01,
            stop_timeout_seconds=0.1,
        )
        self.adapter = ndi.NDIOutput(
            self.args,
            popen=popen,
            clock=lambda: self.now,
            verifier=lambda *_args: {
                "passed": True,
                "video_buffers": 3,
                "audio_buffers": 7,
                "video_fps_from_pts": 30.0,
                "audio_sample_rate_from_pts": 48000.0,
            },
        )

    def test_both_essences_are_required_and_health_is_paired(self):
        self.adapter.start()
        self.assertEqual(self.adapter.snapshot()["state"], "starting")
        self.assertEqual(self.adapter.check(), "healthy")
        state = self.adapter.snapshot()
        self.assertEqual((state["state"], state["ready"]), ("running", True))
        self.assertTrue(state["metrics"]["paired_av"])
        self.assertTrue(state["metrics"]["wire_av_verified"])
        self.assertEqual(state["metrics"]["wire_video_buffers"], 3)
        self.assertEqual(state["metrics"]["wire_audio_buffers"], 7)
        self.assertEqual(state["metrics"]["wire_video_fps"], 30.0)
        self.assertEqual(state["metrics"]["wire_audio_sample_rate_hz"], 48000.0)
        self.assertEqual(state["metrics"]["audio_rate_hz"], 48000)
        self.audio.rmdir()
        self.assertEqual(self.adapter.check(), "wait")
        state = self.adapter.snapshot()
        self.assertEqual((state["state"], state["ready"]), ("degraded", False))

    def test_flow_replacement_requests_one_bounded_restart(self):
        self.adapter.start()
        self.assertEqual(self.adapter.check(), "healthy")
        old = self.domain / "video.old"
        self.video.rename(old)
        self.video.mkdir()
        self.assertEqual(self.adapter.check(), "restart")
        self.processes[0].returncode = 0
        self.adapter.start(restart=True)
        self.assertEqual(self.adapter.check(), "healthy")
        self.assertEqual(self.adapter.snapshot()["restarts"], 1)
        self.processes[-1].returncode = 1
        with self.assertRaisesRegex(RuntimeError, "restart budget exhausted"):
            self.adapter.start(restart=True)
        state = self.adapter.snapshot()
        self.assertEqual((state["state"], state["ready"]), ("failed", False))

    def test_missing_input_fails_closed_before_launch(self):
        self.audio.rmdir()
        with self.assertRaisesRegex(RuntimeError, "paired Program"):
            self.adapter.start()
        self.assertEqual(self.processes, [])
        self.assertFalse(self.adapter.snapshot()["ready"])

    def test_wire_verification_failure_stops_before_ready(self):
        adapter = ndi.NDIOutput(
            self.args,
            popen=self.adapter.popen,
            clock=lambda: self.now,
            verifier=lambda *_args: {
                "passed": False,
                "video_buffers": 3,
                "audio_buffers": 0,
            },
        )
        with self.assertRaisesRegex(RuntimeError, "wire A/V verification"):
            adapter.start()
        state = adapter.snapshot()
        self.assertEqual((state["state"], state["ready"]), ("failed", False))
        self.assertFalse(state["metrics"]["wire_av_verified"])


if __name__ == "__main__":
    unittest.main()
