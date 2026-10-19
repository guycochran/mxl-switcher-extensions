#!/usr/bin/env python3
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "qualify_online_program.py"
SPEC = importlib.util.spec_from_file_location("qualify_online_program", MODULE_PATH)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(gate)


class OnlineProgramQualificationTests(unittest.TestCase):
    def test_proc_parsers_and_host_summary(self):
        self.assertEqual(gate.parse_proc_stat("cpu  10 2 3 40 5 0 0 0\n"), (60, 45))
        self.assertEqual(gate.parse_mem_available_mib("MemTotal: 4096 kB\nMemAvailable: 2048 kB\n"), 2)
        result = gate.host_summary([
            {"cpu": (100, 40), "mem_available_mib": 1024},
            {"cpu": (200, 70), "mem_available_mib": 900},
            {"cpu": (300, 95), "mem_available_mib": 800},
        ], 20)
        self.assertTrue(result["passed"])
        self.assertEqual(result["idle_percent_min"], 25)
        self.assertEqual(result["available_memory_mib_min"], 800)

    def test_host_summary_rejects_insufficient_headroom(self):
        result = gate.host_summary([
            {"cpu": (100, 40), "mem_available_mib": 1024},
            {"cpu": (200, 50), "mem_available_mib": 900},
        ], 20)
        self.assertFalse(result["passed"])
        self.assertEqual(result["idle_percent_min"], 10)

    def test_private_report_is_mode_0600(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "report.json"
            gate.write_private_json(path, {"passed": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn('"passed": true', path.read_text(encoding="utf-8"))

    def test_combined_gate_requires_all_machine_boundaries(self):
        now = [0.0]

        def clock():
            return now[0]

        def sleeper(seconds):
            now[0] += seconds

        youtube_sample = {
            "source_count": 8,
            "output": {
                "ready": True,
                "failed_checks": [],
                "reconnects": 0,
                "metrics": {
                    "composed_fps": 29.97,
                    "video_fps": 29.97,
                    "mux_kbps": 30000,
                    "sent_kbps": 30000,
                    "timecode_fps": 29.97,
                    "audio_buffers_per_second": 46.875,
                    "source_lateness_max_ms": 20,
                    "send_queue_level_ms": 0,
                    "process_cpu_cores": 9,
                    "process_rss_mib": 1024,
                    "source_fps": {str(index): 29.97 for index in range(8)},
                },
            },
        }
        host_samples = iter([
            {"cpu": (100, 40), "mem_available_mib": 4096},
            {"cpu": (200, 70), "mem_available_mib": 4000},
        ])
        with mock.patch.object(gate.demo, "acceptance_snapshot", side_effect=[{"a": 1}, {"a": 2}]), \
                mock.patch.object(gate.demo, "evaluate_acceptance", return_value={"passed": True}):
            result = gate.run_gate(
                {"source_count": 8, "deployment_id": "test-demo"},
                controller_url="http://127.0.0.1:18123",
                seconds=30,
                interval=30,
                minimum_idle_percent=20,
                fetch=lambda _url: youtube_sample,
                sample_host=lambda: next(host_samples),
                clock=clock,
                sleeper=sleeper,
            )
        self.assertTrue(result["passed"])
        self.assertTrue(result["core"]["passed"])
        self.assertTrue(result["youtube"]["passed"])
        self.assertTrue(result["host_resources"]["passed"])
        self.assertEqual(len(result["human_observations_required"]), 4)


if __name__ == "__main__":
    unittest.main()
