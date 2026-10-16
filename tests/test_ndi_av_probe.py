import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "outputs" / "ndi" / "ndi_av_probe.py"
SPEC = importlib.util.spec_from_file_location("ndi_av_probe", MODULE_PATH)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe)


class EvaluationTests(unittest.TestCase):
    def result(self, *, video_buffers=301, video_span=10_000_000_000,
               audio_frames=480_000, audio_span=10_000_000_000):
        return {
            "video_buffers": video_buffers,
            "video_first_pts": 0,
            "video_last_pts": video_span,
            "audio_buffers": 300,
            "audio_sample_frames": audio_frames,
            "audio_first_pts": 0,
            "audio_last_pts": audio_span,
            "error": None,
        }

    def test_full_rate_audio_and_video_pass(self):
        result = probe.evaluate(self.result())
        self.assertTrue(result["passed"])
        self.assertEqual(result["video_fps_from_pts"], 30.0)
        self.assertEqual(result["audio_sample_rate_from_pts"], 48000.0)

    def test_presence_only_audio_fails(self):
        result = probe.evaluate(self.result(audio_frames=5_952))
        self.assertFalse(result["passed"])
        self.assertFalse(result["audio_rate_ok"])

    def test_out_of_range_video_fails(self):
        result = probe.evaluate(self.result(video_buffers=310))
        self.assertFalse(result["passed"])
        self.assertFalse(result["video_rate_ok"])


if __name__ == "__main__":
    unittest.main()
