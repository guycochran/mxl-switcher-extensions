from __future__ import annotations

import importlib.util
import pathlib
import signal
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "recording" / "file-recorder" / "recorder.py"
SPEC = importlib.util.spec_from_file_location("file_recorder", MODULE_PATH)
assert SPEC and SPEC.loader
file_recorder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(file_recorder)


class PipelineTests(unittest.TestCase):
    def test_pipeline_is_shell_free_bounded_and_pairs_program_essences(self):
        output = pathlib.Path("/recordings/program.partial.mkv")
        command = file_recorder.gst_command(
            "/mxl-domain", "video-id", "audio-id", output, 12000, 192000
        )
        joined = " ".join(map(str, command))
        self.assertEqual(command[0], "gst-launch-1.0")
        self.assertIn("video-flow-id=video-id", command)
        self.assertIn("audio-flow-id=audio-id", command)
        self.assertEqual(joined.count("max-size-time=2000000000"), 2)
        self.assertIn("audio/x-raw,rate=48000,channels=2", command)
        self.assertIn("matroskamux", command)
        self.assertIn("location=/recordings/program.partial.mkv", command)
        self.assertNotIn("sh", command)

    def test_video_and_audio_encoder_settings_are_explicit(self):
        command = file_recorder.gst_command(
            "/domain", "v", "a", pathlib.Path("x.partial.mkv"), 8000, 128000
        )
        self.assertIn("bitrate=8000", command)
        self.assertIn("bitrate=128000", command)
        self.assertIn("key-int-max=60", command)
        self.assertIn("bframes=0", command)


class FinalizationTests(unittest.TestCase):
    def test_sigint_exit_promotes_nonempty_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            partial = root / "show.partial.mkv"
            final = root / "show.mkv"
            partial.write_bytes(b"valid media")
            args = SimpleNamespace(recordings_dir=root)
            recorder = file_recorder.Recorder(args)
            recorder.partial_path = partial
            recorder.final_path = final
            recorder.stop_requested = True
            process = mock.Mock()
            process.returncode = -signal.SIGINT
            process.communicate.return_value = (b"", b"")
            recorder.process = process
            recorder._watch()
            self.assertEqual(recorder.state, "ready")
            self.assertEqual(recorder.finalization_state, "complete")
            self.assertTrue(final.is_file())
            self.assertFalse(partial.exists())


if __name__ == "__main__":
    unittest.main()
