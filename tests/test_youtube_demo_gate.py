#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "check_youtube_demo.py"
SPEC = importlib.util.spec_from_file_location("check_youtube_demo", MODULE_PATH)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(gate)


def sample(ready=True, failed=None, source_fps=29.97, mux_kbps=30000, reconnects=0):
    return {
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
                "process_cpu_cores": 12.25,
                "process_rss_mib": 768.0,
                "source_fps": {f"source_{index}": source_fps for index in range(8)},
            },
        },
    }


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
