#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_youtube_demo.py"
SPEC = importlib.util.spec_from_file_location("check_youtube_demo", MODULE_PATH)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(gate)


def sample(
    ready=True, failed=None, source_fps=29.97, mux_kbps=30000,
    reconnects=0, elapsed=None, rss_mib=768.0,
):
    result = {
        "source_count": 8,
        "output": {
            "ready": ready,
            "failed_checks": failed or [],
            "reconnects": reconnects,
            "metrics": {
                "composed_fps": 29.97,
                "video_fps": 29.97,
                "mux_kbps": mux_kbps,
                "sent_kbps": mux_kbps,
                "timecode_fps": 29.97,
                "audio_buffers_per_second": 46.875,
                "source_lateness_max_ms": 42.0,
                "send_queue_level_ms": 120.0,
                "process_cpu_cores": 12.25,
                "process_rss_mib": rss_mib,
                "source_fps": {f"source_{index}": source_fps for index in range(8)},
            },
        },
    }
    if elapsed is not None:
        result["_sample_elapsed_seconds"] = elapsed
    return result


class YouTubeDemoGateTests(unittest.TestCase):
    def test_accepts_sustained_healthy_samples(self):
        result = gate.summarize([sample(), sample(), sample()], 8, 30.125)
        self.assertTrue(result["passed"])
        self.assertEqual(result["ready_samples"], 3)
        self.assertEqual(result["elapsed_seconds"], 30.125)
        self.assertEqual(result["source_fps_min"], 29.97)
        self.assertEqual(result["composed_fps_min"], 29.97)
        self.assertEqual(result["mux_mbps_min"], 30.0)
        self.assertEqual(result["sent_mbps_min"], 30.0)
        self.assertEqual(result["process_cpu_cores_max"], 12.25)
        self.assertEqual(result["process_rss_mib_max"], 768.0)
        self.assertEqual(result["audio_buffers_per_second_min"], 46.875)
        self.assertEqual(result["source_lateness_ms_max"], 42.0)
        self.assertEqual(result["send_queue_level_ms_max"], 120.0)

    def test_rejects_single_frozen_source_or_low_mux(self):
        result = gate.summarize([
            sample(),
            sample(False, ["all_sources_advancing", "mux_bitrate"], 0.0, 8.8),
        ], 8)
        self.assertFalse(result["passed"])
        self.assertEqual(result["source_fps_min"], 0.0)
        self.assertEqual(result["mux_mbps_min"], 0.009)
        self.assertEqual(result["sent_mbps_min"], 0.009)
        self.assertEqual(result["failed_checks"], ["all_sources_advancing", "mux_bitrate"])

    def test_rejects_reconnect_even_after_recovery(self):
        result = gate.summarize([sample(reconnects=1)], 8)
        self.assertFalse(result["passed"])
        self.assertEqual(result["reconnects_max"], 1)

    def test_long_gate_rejects_unbounded_post_warmup_rss_growth(self):
        result = gate.summarize([
            sample(elapsed=0, rss_mib=700),
            sample(elapsed=300, rss_mib=760),
            sample(elapsed=900, rss_mib=860),
            sample(elapsed=1500, rss_mib=960),
        ], 8, 1500)
        self.assertFalse(result["passed"])
        self.assertTrue(result["rss_slope_check_applied"])
        self.assertEqual(result["rss_growth_mib_per_hour"], 600.0)
        self.assertIn("process_rss_growth", result["failed_checks"])

    def test_short_gate_reports_but_does_not_apply_rss_slope(self):
        result = gate.summarize([
            sample(elapsed=0, rss_mib=700),
            sample(elapsed=30, rss_mib=710),
        ], 8, 30)
        self.assertTrue(result["passed"])
        self.assertFalse(result["rss_slope_check_applied"])
        self.assertIsNone(result["rss_growth_mib_per_hour"])

    def test_controller_url_is_loopback_only(self):
        self.assertEqual(
            gate.state_url("http://127.0.0.1:18123/"),
            "http://127.0.0.1:18123/api/state",
        )
        for value in ("https://127.0.0.1:18123", "http://192.0.2.1:18123", "http://user@localhost"):
            with self.assertRaises(ValueError):
                gate.state_url(value)


if __name__ == "__main__":
    unittest.main()
