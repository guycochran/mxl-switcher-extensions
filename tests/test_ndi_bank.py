import argparse
import json
import pathlib
import tempfile
import unittest

from inputs.ndi.ndi_bank import (
    HISTORY_OPTION,
    NDIBank,
    load_sources,
    validate_domain_history,
)


class FakeProcess:
    pid = 43212
    returncode = None
    def __init__(self, argv, **_kwargs): self.argv = argv
    def poll(self): return self.returncode


class BankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.domain = pathlib.Path(self.tmp.name)
        self.sources = [
            {"id":"a", "source_address":"192.0.2.1:5961", "video_flow":"video-a", "audio_mode":"disabled"},
            {"id":"b", "source_name":"CAMERA B", "video_flow":"video-b", "audio_mode":"disabled"},
        ]
        self.args = argparse.Namespace(
            domain=self.domain, width=1920, height=1080, rate_numerator=30, rate_denominator=1,
            rate_mode="normalize", timestamp_mode="receive-time",
            pacing_mode="retimestamp-clock-sync", convert_threads=4,
            max_restarts=1, restart_window_seconds=60.0, poll_seconds=0.01,
            stop_timeout_seconds=0.1,
        )

    def test_one_pipeline_contains_unique_branches_and_no_shell(self):
        bank = NDIBank(self.args, self.sources, popen=FakeProcess, clock=lambda: 10.0)
        command = bank._command()
        self.assertTrue(command[1].endswith("ndi_pipeline.py"))
        self.assertEqual(command.count("ndisrc"), 2)
        self.assertIn("name=demux0", command); self.assertIn("name=demux1", command)
        self.assertIn("demux0.video", command); self.assertIn("demux1.video", command)
        self.assertIn("name=startup_gate_demux0", command)
        self.assertIn("name=startup_gate_demux1", command)
        self.assertIn("name=startup_probe_demux0", command)
        self.assertIn("name=startup_probe_demux1", command)
        self.assertIn("name=clock_sync_demux0", command)
        self.assertIn("name=clock_sync_demux1", command)
        self.assertIn("name=timestamp_sync_demux0", command)
        self.assertIn("name=timestamp_sync_demux1", command)
        self.assertNotIn("name=pacing_queue_demux0", command)
        self.assertNotIn("name=pacing_queue_demux1", command)
        self.assertIn("name=watchdog_demux0", command)
        self.assertIn("name=watchdog_demux1", command)
        self.assertIn("name=frame_bridge_in_demux0", command)
        self.assertIn("name=frame_bridge_in_demux1", command)
        self.assertIn("name=frame_bridge_out_demux0", command)
        self.assertIn("name=frame_bridge_out_demux1", command)
        self.assertIn("name=writer_queue_demux0", command)
        self.assertIn("name=writer_queue_demux1", command)
        self.assertEqual(command.count("intervideosink"), 2)
        self.assertEqual(command.count("intervideosrc"), 2)
        self.assertEqual(command.count("async=false"), 2)
        self.assertEqual(command.count("do-timestamp=true"), 2)
        self.assertEqual(command.count("timeout=30000000000"), 2)
        self.assertEqual(
            sum(
                token.startswith("video/x-raw,format=v210")
                and "interlace-mode=progressive,colorimetry=bt709" in token
                for token in command
            ),
            2,
        )
        self.assertEqual(command.count("timeout=30000"), 2)
        self.assertEqual(command.count("max-size-buffers=12"), 2)
        self.assertEqual(command.count("min-threshold-buffers=6"), 2)
        self.assertEqual(command.count("max-size-buffers=30"), 0)
        self.assertEqual(command.count("min-threshold-buffers=20"), 0)
        self.assertIn("--rate-numerator", command)
        self.assertIn("--rate-denominator", command)
        self.assertIn("--startup-lead-frames", command)
        self.assertIn("2", command)
        self.assertEqual(command.count("drop=true"), 2)
        self.assertNotIn("sh", command)

    def test_config_rejects_duplicate_flows_and_accepts_two_sources(self):
        path = self.domain / "bank.json"
        path.write_text(json.dumps({"sources": self.sources}))
        self.assertEqual(len(load_sources(path)), 2)
        duplicate = [dict(self.sources[0]), dict(self.sources[1], video_flow="video-a")]
        path.write_text(json.dumps({"sources": duplicate}))
        with self.assertRaisesRegex(ValueError, "unique"):
            load_sources(path)

    def test_state_omits_source_identity_and_addresses(self):
        bank = NDIBank(self.args, self.sources, popen=FakeProcess, clock=lambda: 10.0)
        state = json.dumps(bank.snapshot())
        self.assertNotIn("192.0.2.1", state)
        self.assertNotIn("CAMERA B", state)
        self.assertNotIn("video-a", state)
        self.assertIn('"configured_inputs": 2', state)

    def test_bank_requires_one_second_domain_history(self):
        options = self.domain / "options.json"
        with self.assertRaisesRegex(RuntimeError, "missing or invalid"):
            validate_domain_history(self.domain, 1_000_000_000)
        options.write_text(json.dumps({HISTORY_OPTION: 200_000_000}))
        with self.assertRaisesRegex(RuntimeError, "at least"):
            validate_domain_history(self.domain, 1_000_000_000)
        options.write_text(json.dumps({HISTORY_OPTION: 1_000_000_000}))
        validate_domain_history(self.domain, 1_000_000_000)


if __name__ == "__main__": unittest.main()
