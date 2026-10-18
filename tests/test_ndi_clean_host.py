import json
import contextlib
import dataclasses
import io
import pathlib
import tempfile
import unittest
from unittest import mock

from inputs.ndi.qualify_clean_host import (
    HARD_COUNTERS,
    OWNER_LABEL,
    ROLE_LABEL,
    Docker,
    Harness,
    QualificationError,
    Settings,
    evaluate_stage,
    main,
    read_host_cpu,
    require_apply_privilege,
    timing_events,
    validate_settings,
)


FLOW_A = "00000000-0000-4000-8000-000000000101"
FLOW_B = "00000000-0000-4000-8000-000000000201"


def health(index=1000, **changes):
    value = {key: 0 for key in HARD_COUNTERS}
    value.update(status="running", routed=index * 2, held=0,
                 sustained_unavailable_active=0,
                 sources=[{"flow_id": FLOW_A, "latest_head_index": index},
                          {"flow_id": FLOW_B, "latest_head_index": index}])
    value.update(changes)
    return value


def passing_samples():
    return [{"skew_frames": 1, "bank_cpu_percent": 180.0,
             "bank_rss_bytes": 300 * 1024 * 1024,
             "core_cpu_percent": 100.0, "core_rss_bytes": 200 * 1024 * 1024,
             "host_idle_percent": 40.0, "bank_restarts": 0, "core_restarts": 0},
            {"skew_frames": 0, "bank_cpu_percent": 170.0,
             "bank_rss_bytes": 305 * 1024 * 1024,
             "core_cpu_percent": 90.0, "core_rss_bytes": 205 * 1024 * 1024,
             "host_idle_percent": 35.0, "bank_restarts": 0, "core_restarts": 0}]


def passing_timing(count=5):
    return [{"event": "ndi_bank_timing", "catchup_ticks": 0, "max_frames_behind": 0}
            for _ in range(count)]


def verdict_kwargs():
    return dict(max_hold_ratio=.05, min_fps=28.5, max_fps=30.5,
                max_bank_cpu_percent=300, max_bank_rss_bytes=500 * 1024 * 1024,
                max_core_cpu_percent=200, max_core_rss_bytes=512 * 1024 * 1024,
                min_host_idle_percent=20, max_rss_growth_bytes=64 * 1024 * 1024,
                max_rss_growth_ratio=.10, flow_ids=[FLOW_A, FLOW_B])


class FakeDocker:
    def __init__(self):
        self.image_ids = {"bank:immutable": "sha256:" + "a" * 64,
                          "core:immutable": "sha256:" + "b" * 64}
        self.containers = {}
        self.names = {}
        self.mutations = []

    def read(self, *args): return "" if args[:2] == ("ps", "--quiet") else "test"
    def image_id(self, image): return self.image_ids[image]
    def inspect_optional(self, identifier):
        return self.containers.get(identifier) or self.containers.get(self.names.get(identifier))
    def running_ids(self): return {value["Id"] for value in self.containers.values()}
    def mutate(self, *args):
        self.mutations.append(list(args))
        if args[0] == "run":
            name = args[args.index("--name") + 1]
            owner = args[args.index("--label") + 1].split("=", 1)[1]
            second_label = args.index("--label", args.index("--label") + 1)
            role = args[second_label + 1].split("=", 1)[1]
            image_id = next(value for value in self.image_ids.values() if value in args)
            container_id = ("c" if role == "bank" else "d") * 64
            self.containers[name] = {"Id": container_id, "Image": image_id,
                                     "RestartCount": 0,
                                     "Config": {"Labels": {OWNER_LABEL: owner, ROLE_LABEL: role}}}
            self.names[container_id] = name
            return container_id
        if args[:2] == ("rm", "--force"):
            name = self.names.pop(args[2], args[2])
            self.containers.pop(name, None)
        return ""
    def logs(self, _name): return json.dumps(passing_timing()[0]) + "\n"
    def stats(self, names): return {name: (100.0, 200 * 1024 * 1024) for name in names}


class CleanHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.domain_root = root / "domains"; self.domain_root.mkdir()
        self.definitions = root / "definitions"; self.definitions.mkdir()
        self.source_config = root / "private-bank.json"
        self.source_config.write_text(json.dumps({"sources": [
            {"id": "a", "source_address": "192.0.2.10:5961", "video_flow": FLOW_A,
             "audio_mode": "disabled"},
            {"id": "b", "source_name": "PRIVATE CAMERA", "video_flow": FLOW_B,
             "audio_mode": "disabled"},
        ]}))
        self.core_config = root / "core.conf"; self.core_config.write_text(
            "domain=/mxl-domain\n"
            f"source=a|{FLOW_A}|Private A\nsource=b|{FLOW_B}|Private B\n"
            "control_host=127.0.0.1\ncontrol_port=18131\n")
        self.settings = Settings(
            "clean-test", "bank:immutable", "core:immutable", self.source_config,
            self.core_config, self.definitions, self.domain_root, root / "evidence",
            "http://127.0.0.1:18130", "http://127.0.0.1:18131/api/mxl/v1",
        )

    def test_preflight_is_two_source_native_video_only_and_redacted(self):
        sources = validate_settings(self.settings)
        self.assertEqual(sources, [FLOW_A, FLOW_B])
        plan = Harness(self.settings, docker=FakeDocker(), port_check=lambda _url: True).preflight()
        encoded = json.dumps(plan)
        for secret in ("clean-test", "192.0.2.10", "PRIVATE CAMERA", FLOW_A, str(self.source_config)):
            self.assertNotIn(secret, encoded)
        self.assertEqual(plan["stages_seconds"], [60, 180])
        self.assertEqual(plan["copy_workers"], 1)

    def test_rejects_non_two_source_or_audio_config(self):
        document = json.loads(self.source_config.read_text())
        document["sources"] = document["sources"][:1]
        self.source_config.write_text(json.dumps(document))
        with self.assertRaises((QualificationError, ValueError)):
            validate_settings(self.settings)
        document["sources"].append({"id": "b", "source_name": "PRIVATE CAMERA",
                                    "video_flow": FLOW_B, "audio_mode": "required",
                                    "audio_flow": "00000000-0000-4000-8000-000000000202"})
        self.source_config.write_text(json.dumps(document))
        with self.assertRaisesRegex(QualificationError, "video-only"):
            validate_settings(self.settings)

    def test_launch_uses_resolved_images_labels_and_private_readonly_mounts(self):
        docker = FakeDocker(); harness = Harness(self.settings, docker=docker, port_check=lambda _url: True)
        harness.preflight(); harness.start()
        commands = [command for command in docker.mutations if command[0] == "run"]
        self.assertEqual(len(commands), 2)
        joined = json.dumps(commands)
        self.assertIn("sha256:" + "a" * 64, joined)
        self.assertIn("sha256:" + "b" * 64, joined)
        self.assertIn(f"{OWNER_LABEL}=clean-test", joined)
        self.assertIn("readonly", joined)
        self.assertIn("--copy-workers", commands[0]); self.assertIn("1", commands[0])
        for private in ("192.0.2.10", "PRIVATE CAMERA", FLOW_A, FLOW_B):
            self.assertNotIn(private, joined)
        harness.cleanup()
        self.assertFalse(self.settings.domain.exists())
        removals = [command for command in docker.mutations if command[:2] == ["rm", "--force"]]
        self.assertEqual({command[2] for command in removals}, {"c" * 64, "d" * 64})
        self.assertNotIn(self.settings.bank_name, {command[2] for command in removals})

    def test_cleanup_refuses_changed_ownership(self):
        docker = FakeDocker(); harness = Harness(self.settings, docker=docker, port_check=lambda _url: True)
        harness.preflight(); harness.start()
        docker.containers[self.settings.bank_name]["Config"]["Labels"][OWNER_LABEL] = "someone-else"
        with self.assertRaisesRegex(QualificationError, "ownership changed"):
            harness.cleanup()
        self.assertIn(self.settings.bank_name, docker.containers)
        self.assertTrue(self.settings.domain.exists())

    def _argv(self, *extra):
        return ["--deployment-id", "clean-test", "--bank-image", "bank:immutable",
                "--core-image", "core:immutable", "--source-config", str(self.source_config),
                "--core-config", str(self.core_config), "--definitions", str(self.definitions),
                "--domain-root", str(self.domain_root), "--evidence-dir",
                str(self.settings.evidence_dir), *extra]

    def test_apply_requires_root_before_anything_is_launched(self):
        module = "inputs.ndi.qualify_clean_host"
        stderr = io.StringIO()
        with mock.patch(f"{module}.os.geteuid", return_value=1000), \
             mock.patch(f"{module}.Harness.preflight", return_value={"applied": False}), \
             mock.patch(f"{module}.Harness.start") as start, \
             mock.patch(f"{module}.Harness.cleanup") as cleanup, \
             contextlib.redirect_stderr(stderr):
            self.assertEqual(main(self._argv("--apply")), 2)
        start.assert_not_called(); cleanup.assert_not_called()
        self.assertIn("must run as root", stderr.getvalue())
        self.assertFalse(self.settings.domain.exists())
        with self.assertRaisesRegex(QualificationError, "root"):
            require_apply_privilege(lambda: 1000)
        require_apply_privilege(lambda: 0)

    def test_dry_preflight_stays_unprivileged_and_root_apply_proceeds(self):
        module = "inputs.ndi.qualify_clean_host"
        with mock.patch(f"{module}.os.geteuid", return_value=1000), \
             mock.patch(f"{module}.Harness.preflight", return_value={"applied": False}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(self._argv()), 0)
        result = {"passed": True}
        with mock.patch(f"{module}.os.geteuid", return_value=0), \
             mock.patch(f"{module}.Harness.preflight", return_value={"applied": False}), \
             mock.patch(f"{module}.Harness.start") as start, \
             mock.patch(f"{module}.Harness.wait_ready"), \
             mock.patch(f"{module}.Harness.qualify", return_value=result), \
             mock.patch(f"{module}.Harness.cleanup") as cleanup, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(self._argv("--apply")), 0)
        start.assert_called_once(); cleanup.assert_called_once()

    def test_cleanup_reports_root_owned_flow_contents_without_false_success(self):
        docker = FakeDocker(); harness = Harness(self.settings, docker=docker, port_check=lambda _url: True)
        harness.preflight(); harness.start()
        (self.settings.domain / "flow.mxl-flow").mkdir()   # created by a root container
        with mock.patch("inputs.ndi.qualify_clean_host.shutil.rmtree",
                        side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaisesRegex(QualificationError, "run --apply as root"):
                harness.cleanup()
        self.assertTrue(self.settings.domain.exists())
        self.assertEqual(docker.containers, {})          # both owned containers still removed
        harness.cleanup()                                  # as root: exact domain removed
        self.assertFalse(self.settings.domain.exists())

    def test_cleanup_refuses_a_symlinked_domain(self):
        docker = FakeDocker(); harness = Harness(self.settings, docker=docker, port_check=lambda _url: True)
        harness.preflight(); harness.start()
        elsewhere = self.domain_root.parent / "elsewhere"
        self.settings.domain.rename(elsewhere)
        self.settings.domain.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(QualificationError, "verify or remove"):
            harness.cleanup()
        self.assertTrue(elsewhere.is_dir())
        self.assertTrue((elsewhere / ".qualification-owner").exists())

    def test_preflight_refuses_busy_host_and_occupied_port(self):
        busy = FakeDocker()
        busy.read = lambda *args: "abc" if args[:2] == ("ps", "--quiet") else "test"
        with self.assertRaisesRegex(QualificationError, "any container"):
            Harness(self.settings, docker=busy, port_check=lambda _url: True).preflight()
        with self.assertRaisesRegex(QualificationError, "port is occupied"):
            Harness(self.settings, docker=FakeDocker(), port_check=lambda _url: False).preflight()

    def test_core_config_must_match_domain_control_and_bank_flows(self):
        cases = (
            ("domain=/wrong\n", "domain"),
            ("control_host=0.0.0.0\n", "host"),
            ("control_port=9999\n", "port"),
            (f"source=a|{FLOW_A}|A\nsource=x|00000000-0000-4000-8000-000000000999|X\n", "flows"),
        )
        original = self.core_config.read_text()
        for replacement, message in cases:
            with self.subTest(message=message):
                lines = original.splitlines()
                key = replacement.split("=", 1)[0]
                filtered = [line for line in lines if not line.startswith(key + "=")]
                self.core_config.write_text("\n".join(filtered) + "\n" + replacement)
                with self.assertRaisesRegex(QualificationError, message):
                    validate_settings(self.settings)
                self.core_config.write_text(original)

        ipv6 = dataclasses.replace(
            self.settings, core_url="http://[::1]:18131/api/mxl/v1")
        with self.assertRaisesRegex(QualificationError, "127.0.0.1"):
            validate_settings(ipv6)

    def test_evidence_must_be_outside_domain_root(self):
        unsafe = dataclasses.replace(self.settings, evidence_dir=self.domain_root / "evidence")
        with self.assertRaisesRegex(QualificationError, "disjoint"):
            validate_settings(unsafe)

    def test_stage_acceptance_and_strict_skip_failure(self):
        before = health(1000)
        after = health(2800, routed=5600)
        result = evaluate_stage(
            "smoke", 60, before, after, passing_samples(), passing_timing(),
            **verdict_kwargs())
        self.assertTrue(result["passed"])
        self.assertNotIn(FLOW_A, json.dumps(result))
        failed = health(2800, routed=5600, selected_input_skip_events=1,
                        selected_input_skipped_grains=2)
        result = evaluate_stage(
            "smoke", 60, before, failed, passing_samples(), passing_timing(),
            **verdict_kwargs())
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["hard_counters"])

    def test_heads_are_selected_by_flow_id_not_list_position(self):
        extra = "00000000-0000-4000-8000-000000000777"
        before = health(1000)
        after = health(2800, routed=5600)
        before["sources"] = [{"flow_id": extra, "latest_head_index": 999999},
                             before["sources"][1], before["sources"][0]]
        after["sources"] = [{"flow_id": extra, "latest_head_index": 1},
                            after["sources"][1], after["sources"][0]]
        result = evaluate_stage("smoke", 60, before, after, passing_samples(),
                                passing_timing(), **verdict_kwargs())
        self.assertTrue(result["checks"]["head_cadence"])
        self.assertEqual(result["source_fps"], [30.0, 30.0])

    def test_failed_smoke_never_starts_long_gate(self):
        class StagedHarness(Harness):
            def __init__(self, settings, outcomes):
                super().__init__(settings, docker=FakeDocker(), port_check=lambda _url: True)
                self.outcomes, self.calls = outcomes, []
            def run_stage(self, name, seconds):
                self.calls.append((name, seconds))
                return {"stage": name, "passed": self.outcomes[name]}
        self.settings.evidence_dir.mkdir()
        failed = StagedHarness(self.settings, {"smoke": False, "gate": True})
        failed_result = failed.qualify()
        self.assertFalse(failed_result["passed"])
        self.assertNotIn("clean-test", json.dumps(failed_result))
        self.assertEqual(failed.calls, [("smoke", 60)])
        (self.settings.evidence_dir / "result.json").unlink()
        passed = StagedHarness(self.settings, {"smoke": True, "gate": True})
        self.assertTrue(passed.qualify()["passed"])
        self.assertEqual(passed.calls, [("smoke", 60), ("gate", 180)])

    def test_catchup_restart_and_resource_limits_fail(self):
        samples = passing_samples()
        samples[0].update(bank_restarts=1, bank_cpu_percent=301,
                          bank_rss_bytes=501 * 1024 * 1024)
        timing = [{"event": "ndi_bank_timing", "catchup_ticks": 1, "max_frames_behind": 2}
                  for _ in range(15)]
        result = evaluate_stage(
            "gate", 180, health(1000), health(6400, routed=12800), samples, timing,
            **verdict_kwargs())
        self.assertFalse(result["passed"])
        for key in ("no_restarts", "no_catchup", "bank_cpu", "bank_rss"):
            self.assertFalse(result["checks"][key])

    def test_timing_coverage_core_resources_idle_and_rss_growth_are_strict(self):
        samples = [dict(item) for item in passing_samples() for _ in range(2)]
        samples[0].update(bank_rss_bytes=300 * 1024 * 1024, core_rss_bytes=200 * 1024 * 1024)
        samples[1].update(bank_rss_bytes=300 * 1024 * 1024, core_rss_bytes=200 * 1024 * 1024)
        samples[2].update(bank_rss_bytes=400 * 1024 * 1024, core_rss_bytes=300 * 1024 * 1024,
                          core_cpu_percent=201, host_idle_percent=19)
        samples[3].update(bank_rss_bytes=400 * 1024 * 1024, core_rss_bytes=513 * 1024 * 1024)
        result = evaluate_stage("smoke", 60, health(1000), health(2800, routed=5600),
                                samples, passing_timing(1), **verdict_kwargs())
        self.assertFalse(result["passed"])
        for key in ("timing_coverage", "bank_rss_growth", "core_cpu", "core_rss",
                    "core_rss_growth", "host_idle"):
            self.assertFalse(result["checks"][key])

    def test_sample_refuses_an_unrelated_running_container(self):
        docker = FakeDocker()
        harness = Harness(self.settings, docker=docker, port_check=lambda _url: True,
                          cpu_reader=lambda: (50, 100))
        harness.preflight(); harness.start()
        harness.flow_ids = [FLOW_A, FLOW_B]
        docker.containers["unrelated"] = {"Id": "e" * 64}
        with self.assertRaisesRegex(QualificationError, "container set changed"):
            harness._sample()

    def test_proc_stat_counts_steal_as_busy(self):
        path = pathlib.Path(self.tmp.name) / "proc-stat"
        path.write_text("cpu  10 0 0 80 0 0 0 10 7 3\n")
        idle, total = read_host_cpu(path)
        self.assertEqual((idle, total), (80, 100))

    def test_timing_parser_ignores_other_logs_and_rejects_malformed_event(self):
        value = timing_events("noise\n" + json.dumps(passing_timing()[0]) + "\n")
        self.assertEqual(len(value), 1)
        with self.assertRaisesRegex(QualificationError, "malformed"):
            timing_events('{"event":"ndi_bank_timing",bad}\n')

    def test_docker_boundary_never_uses_shell(self):
        calls = []
        def run(argv, **_kwargs):
            calls.append(argv)
            return type("Result", (), {"returncode": 0, "stdout": "sha256:" + "f" * 64 + "\n", "stderr": ""})()
        self.assertTrue(Docker(run).image_id("example:immutable").startswith("sha256:"))
        self.assertEqual(calls[0][:3], ["docker", "image", "inspect"])
        self.assertIsInstance(calls[0], list)

    def test_command_failure_does_not_echo_private_stderr(self):
        secret = "private/path/00000000-0000-4000-8000-000000000999"
        def run(_argv, **_kwargs):
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": secret})()
        with self.assertRaises(QualificationError) as caught:
            Docker(run).read("info")
        self.assertNotIn(secret, str(caught.exception))

    def test_main_does_not_echo_raw_oserror_paths(self):
        secret = "/private/evidence/00000000-0000-4000-8000-000000000999"
        stderr = io.StringIO()
        argv = ["--deployment-id", "safe-id", "--bank-image", "bank:x",
                "--core-image", "core:x", "--source-config", "source.json",
                "--core-config", "core.conf", "--definitions", "definitions",
                "--evidence-dir", "evidence"]
        with mock.patch("inputs.ndi.qualify_clean_host.Harness.preflight",
                        side_effect=OSError(secret)), contextlib.redirect_stderr(stderr):
            self.assertEqual(main(argv), 2)
        self.assertNotIn(secret, stderr.getvalue())
        self.assertIn("local I/O validation failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
