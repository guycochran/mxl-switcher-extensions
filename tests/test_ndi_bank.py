import argparse
import json
import pathlib
import tempfile
import unittest

from inputs.ndi.ndi_bank import (
    HISTORY_OPTION,
    NATIVE_BANK_PLUGINS,
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
            writer_mode="gstreamer",
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

    def test_native_writer_keeps_private_selectors_out_of_argv(self):
        self.args.writer_mode = "native"
        bank = NDIBank(self.args, self.sources, popen=FakeProcess, clock=lambda: 10.0)
        command = bank._command()
        environment = bank._environment()
        self.assertEqual(command[0], "/usr/local/bin/ndi-mxl-bank")
        self.assertNotIn("192.0.2.1:5961", command)
        self.assertNotIn("CAMERA B", command)
        self.assertNotIn("video-a", command)
        self.assertEqual(environment["NDI_BANK_SOURCE_0_TYPE"], "address")
        self.assertEqual(environment["NDI_BANK_SOURCE_0_VALUE"], "192.0.2.1:5961")
        self.assertEqual(environment["NDI_BANK_SOURCE_1_TYPE"], "name")
        self.assertEqual(environment["NDI_BANK_SOURCE_1_VALUE"], "CAMERA B")
        self.assertEqual(environment["NDI_BANK_SOURCE_0_FLOW"], "video-a")

    def test_native_timing_report_is_opt_in_and_secret_free(self):
        self.args.writer_mode = "native"
        bank = NDIBank(self.args, self.sources, popen=FakeProcess, clock=lambda: 10.0)
        self.assertNotIn("--timing-report-seconds", bank._command())
        self.args.timing_report_seconds = 10
        command = bank._command()
        position = command.index("--timing-report-seconds")
        self.assertEqual(command[position + 1], "10")
        for private in ("192.0.2.1:5961", "CAMERA B", "video-a", "video-b"):
            self.assertNotIn(private, command)
        self.assertEqual(bank.snapshot()["metrics"]["timing_report_seconds"], 10)

    def test_timing_report_argument_is_bounded_and_native_only(self):
        from inputs.ndi.ndi_bank import parse_args
        base = ["--domain", self.tmp.name, "--config", "sources.json"]
        self.assertEqual(parse_args(base).timing_report_seconds, 0)
        self.assertEqual(parse_args([*base, "--timing-report-seconds", "5"]).timing_report_seconds, 5)
        for bad in (["--timing-report-seconds", "-1"], ["--timing-report-seconds", "3601"],
                    ["--timing-report-seconds", "5", "--writer-mode", "gstreamer"]):
            with self.assertRaises(SystemExit):
                parse_args([*base, *bad])

    def test_copy_worker_is_opt_in_bounded_and_native_only(self):
        from inputs.ndi.ndi_bank import parse_args
        base = ["--domain", self.tmp.name, "--config", "sources.json"]
        self.assertEqual(parse_args(base).copy_workers, 0)
        self.assertEqual(parse_args([*base, "--copy-workers", "1"]).copy_workers, 1)
        for bad in (["--copy-workers", "2"],
                    ["--copy-workers", "1", "--writer-mode", "gstreamer"]):
            with self.assertRaises(SystemExit):
                parse_args([*base, *bad])

    def test_native_copy_worker_is_explicit_and_keeps_mxl_on_coordinator(self):
        self.args.writer_mode = "native"
        bank = NDIBank(self.args, self.sources, popen=FakeProcess, clock=lambda: 10.0)
        self.assertNotIn("--copy-workers", bank._command())
        self.args.copy_workers = 1
        command = bank._command()
        position = command.index("--copy-workers")
        self.assertEqual(command[position + 1], "1")
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "inputs" / "ndi" / "native" / "ndi_mxl_bank.cpp").read_text()
        copier = source[source.index("class PayloadCopier"):source.index("class Writer")]
        self.assertNotIn("mxlFlowWriter", copier)
        self.assertNotIn("gst_", copier)
        self.assertIn("copier.copy(payload, map.data, info.grainSize)", source)

    def test_native_source_keeps_timing_telemetry_free_of_identities(self):
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "inputs" / "ndi" / "native" / "ndi_mxl_bank.cpp").read_text()
        report = source[source.index("void maybe_report"):source.index(" private:\n  static constexpr")]
        for forbidden in ("selector", "required_env", "FLOW", "NDI_BANK_SOURCE", "VALUE"):
            self.assertNotIn(forbidden, report)
        self.assertIn('"timing-report-seconds"', source)
        self.assertIn("RUSAGE_THREAD", source)

    def test_native_writer_fails_closed_for_audio_until_implemented(self):
        self.args.writer_mode = "native"
        sources = [dict(source) for source in self.sources]
        sources[0].update(audio_mode="required", audio_flow="audio-a")
        bank = NDIBank(self.args, sources, popen=FakeProcess, clock=lambda: 10.0)
        with self.assertRaisesRegex(RuntimeError, "video-only"):
            bank._command()

    def test_native_plugin_set_does_not_require_superseded_writers(self):
        self.assertIn("appsink", NATIVE_BANK_PLUGINS)
        self.assertNotIn("mxlsink", NATIVE_BANK_PLUGINS)
        self.assertNotIn("intervideosink", NATIVE_BANK_PLUGINS)

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
