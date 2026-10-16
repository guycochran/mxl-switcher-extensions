import importlib.util
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "online-demo" / "mxl_demo.py"
SPEC = importlib.util.spec_from_file_location("mxl_online_demo", MODULE_PATH)
demo = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(demo)


class OnlineDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.domain = self.root / "domain"
        self.domain.mkdir()
        self.core = self.root / "core"
        (self.core / "deploy" / "confidence").mkdir(parents=True)
        (self.core / "deploy" / "confidence" / "mediamtx.yml").write_text("paths: {}\n")
        self.config = {
            "deployment_id": "test-demo",
            "source_count": 4,
            "domain_host_path": str(self.domain),
            "domain_mount_source": str(self.domain),
            "docker": ["docker"],
            "base_image": "mxl-lab:latest",
            "core_repository": "https://example.invalid/core.git",
            "core_ref": "a" * 40,
            "core_repo": str(self.core),
            "core_image": "core:test",
            "operator_image": "operator:test",
            "youtube_image": "youtube:test",
            "mediamtx_image": "mediamtx:test",
            "core_port": 18085,
            "skin_port": 9710,
            "youtube_port": 18123,
            "rtmp_port": 1935,
            "mediamtx_api_port": 9997,
            "webrtc_port": 8889,
            "ice_tcp_port": 8189,
            "confidence_browser_base": "http://127.0.0.1:13089",
            "input_delay_frames": 1,
            "state_dir": str(self.root / "state"),
        }
        self.config_file = self.root / "config.json"
        self.config_file.write_text(json.dumps(self.config))
        for number in range(1, 5):
            self.flow(number, "Video", "video/v210", f"video-{number}")
            self.flow(number, "Audio", "audio/float32", f"audio-{number}")

    def tearDown(self):
        self.temp.cleanup()

    def flow(self, number, essence, media_type, flow_id, age=0):
        directory = self.domain / f"{flow_id}.mxl-flow"
        directory.mkdir()
        definition = {
            "id": flow_id,
            "device_id": f"device-{number}",
            "label": f"ZoomISO Cloud Instance Output {number} {essence}",
            "format": "urn:x-nmos:format:video" if essence == "Video" else "urn:x-nmos:format:audio",
            "media_type": media_type,
            "grain_rate": {"numerator": 30, "denominator": 1},
            "frame_width": 1920,
            "frame_height": 1080,
            "interlace_mode": "progressive",
            "colorspace": "BT709",
            "components": [],
        }
        (directory / "flow_def.json").write_text(json.dumps(definition))
        data = directory / "data"
        data.write_bytes(b"x")
        if age:
            old = time.time() - age
            os.utime(data, (old, old))

    def test_discovery_requires_paired_fresh_flows(self):
        cfg = demo.load_config(self.config_file)
        found = demo.discover(cfg)
        self.assertEqual([row["flow_id"] for row in found], [f"video-{n}" for n in range(1, 5)])
        self.assertEqual([row["audio_flow_id"] for row in found], [f"audio-{n}" for n in range(1, 5)])
        stale = self.domain / "audio-4.mxl-flow" / "data"
        old = time.time() - 120
        os.utime(stale, (old, old))
        self.assertEqual(len(demo.discover(cfg)), 3)

    def test_discovery_rejects_wrong_audio_media_type(self):
        definition = self.domain / "audio-4.mxl-flow" / "flow_def.json"
        value = json.loads(definition.read_text())
        value["media_type"] = "audio/L24"
        definition.write_text(json.dumps(value))
        cfg = demo.load_config(self.config_file)
        self.assertEqual(len(demo.discover(cfg)), 3)

    def test_discovery_refuses_a_different_bind_mount_domain(self):
        value = dict(self.config)
        value["domain_mount_source"] = str(self.root / "other-domain")
        self.config_file.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, "MXL domain mismatch"):
            demo.discover(demo.load_config(self.config_file))

    def test_discovery_refuses_a_named_volume_pointing_elsewhere(self):
        value = dict(self.config)
        value["domain_mount_source"] = "zoomiso-domain"
        self.config_file.write_text(json.dumps(value))
        cfg = demo.load_config(self.config_file)
        with mock.patch.object(demo, "volume_mountpoint",
                               return_value=str(self.root / "other-domain")):
            with self.assertRaisesRegex(RuntimeError, "MXL domain mismatch"):
                demo.discover(cfg)

    def test_discovery_accepts_a_named_volume_matching_the_host_path(self):
        value = dict(self.config)
        value["domain_mount_source"] = "zoomiso-domain"
        self.config_file.write_text(json.dumps(value))
        cfg = demo.load_config(self.config_file)
        with mock.patch.object(demo, "volume_mountpoint", return_value=str(self.domain)):
            self.assertEqual(len(demo.discover(cfg)), 4)

    def test_prepare_keeps_runtime_ids_out_of_repository(self):
        cfg = demo.load_config(self.config_file)
        first = demo.prepare(cfg)
        second = demo.prepare(cfg)
        self.assertEqual(first["program_flow_id"], second["program_flow_id"])
        state = pathlib.Path(cfg["state_dir"])
        self.assertTrue((state / "core.conf").exists())
        self.assertIn("initial_program=zoomiso-1", (state / "core.conf").read_text())
        self.assertIn("input_delay_frames=1", (state / "core.conf").read_text())
        program = json.loads((state / "definitions" / "program.json").read_text())
        self.assertEqual(program["id"], first["program_flow_id"])

    def test_prepare_defaults_to_qualified_two_frame_presentation_delay(self):
        value = dict(self.config)
        value.pop("input_delay_frames")
        self.config_file.write_text(json.dumps(value))

        cfg = demo.load_config(self.config_file)
        demo.prepare(cfg)

        core_config = pathlib.Path(cfg["state_dir"], "core.conf").read_text()
        self.assertIn("input_delay_frames=1", core_config)

    def test_plan_includes_skin_one_wall_and_secret_free_youtube_control(self):
        cfg = demo.load_config(self.config_file)
        resolved = demo.prepare(cfg)
        commands = demo.commands(cfg, resolved)
        rendered = "\n".join(demo.quote(command) for command in commands)
        self.assertEqual(len(commands), 7)
        self.assertIn("http://127.0.0.1:18085/api/mxl/v1/health", rendered)
        self.assertIn("--health-start-period 10s", rendered)
        self.assertIn("test-demo-multiview-confidence", rendered)
        self.assertIn("--layout wall", rendered)
        self.assertIn("rtmp://127.0.0.1:1935/mxl-multiview", rendered)
        self.assertIn("test-demo-skin", rendered)
        self.assertIn("--port 9710", rendered)
        self.assertIn("http://127.0.0.1:13089/mxl-program/", rendered)
        self.assertIn("test-demo-youtube", rendered)
        self.assertIn("--mix-source-audio", rendered)
        self.assertEqual(rendered.count("--preferred-flow"), 4)
        self.assertNotIn("stream-key", rendered)
        self.assertNotIn("supersource-helper", rendered)

    def test_containers_are_labelled_and_never_auto_restart(self):
        cfg = demo.load_config(self.config_file)
        commands = demo.commands(cfg, demo.prepare(cfg))
        for command in commands:
            self.assertIn("--restart=no", command)
            label = command.index("--label") + 1
            self.assertEqual(command[label], f"{demo.OWNER_LABEL}=test-demo")
        self.assertNotIn("unless-stopped", "\n".join(demo.quote(c) for c in commands))

    def fake_docker(self, owners):
        """Simulate `docker inspect` label lookups and record every other call."""
        calls = []

        def run(command, *args, **kwargs):
            if command[1] == "inspect":
                name = command[-1]
                if name not in owners:
                    return demo.subprocess.CompletedProcess(command, 1, "", "")
                return demo.subprocess.CompletedProcess(command, 0, owners[name] + "\n", "")
            calls.append(command)
            return demo.subprocess.CompletedProcess(command, 0, "", "")

        return run, calls

    def test_stop_refuses_a_same_named_container_it_did_not_create(self):
        cfg = demo.load_config(self.config_file)
        run, calls = self.fake_docker({
            "test-demo-core": "test-demo",
            "test-demo-mediamtx": "<no value>",
        })
        with mock.patch.object(demo.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "test-demo-mediamtx"):
                demo.stop(cfg, apply=True)
        self.assertEqual(calls, [])

    def test_stop_removes_only_labelled_containers(self):
        cfg = demo.load_config(self.config_file)
        run, calls = self.fake_docker({
            "test-demo-core": "test-demo", "test-demo-youtube": "test-demo",
        })
        with mock.patch.object(demo.subprocess, "run", side_effect=run):
            demo.stop(cfg, apply=True)
        self.assertEqual(calls, [["docker", "rm", "-f", "test-demo-core", "test-demo-youtube"]])

    def test_invalid_ports_and_names_fail_closed(self):
        value = dict(self.config)
        value["deployment_id"] = "Bad Name"
        self.config_file.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            demo.load_config(self.config_file)
        value = dict(self.config)
        value["confidence_browser_base"] = "http://public.example:8889"
        self.config_file.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            demo.load_config(self.config_file)
        value = dict(self.config)
        value["skin_port"] = value["core_port"]
        self.config_file.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            demo.load_config(self.config_file)

    def test_confidence_stats_are_fresh_within_the_acceptance_window(self):
        cfg = demo.load_config(self.config_file)
        commands = demo.commands(cfg, demo.prepare(cfg))
        encoders = [c for c in commands if "/opt/mxl-operator/mxl_confidence_encoder.py" in c]
        self.assertEqual(len(encoders), 3)
        for command in encoders:
            every = float(command[command.index("--stats-every-s") + 1])
            # The default 30 s gate must see several new stats lines per encoder.
            self.assertLessEqual(3 * every, 30)
        with self.assertRaisesRegex(ValueError, "at least 15 seconds"):
            demo.acceptance(cfg, 10)

    def test_init_writes_a_valid_local_config_from_one_detected_volume(self):
        target = self.root / "generated.json"
        with mock.patch.object(demo, "detect_docker_command", return_value=["docker"]), \
             mock.patch.object(demo, "discover_domain_volumes", return_value=["zoomiso-domain"]), \
             mock.patch.object(demo, "volume_mountpoint", return_value=str(self.domain)), \
             mock.patch.object(demo, "image_exists", return_value=True):
            demo.init_config(target, domain_volume=None, domain_path=None,
                             base_image="mxl-lab:latest",
                             source_count=4, force=False)
        value = json.loads(target.read_text())
        self.assertEqual(value["domain_mount_source"], "zoomiso-domain")
        self.assertEqual(value["domain_host_path"], str(self.domain))
        self.assertEqual(value["source_count"], 4)
        self.assertEqual(value["docker"], ["docker"])
        demo.load_config(target)

    def test_init_requires_an_unambiguous_domain_and_refuses_overwrite(self):
        target = self.root / "generated.json"
        with mock.patch.object(demo, "detect_docker_command", return_value=["docker"]), \
             mock.patch.object(demo, "discover_domain_volumes", return_value=["one", "two"]):
            with self.assertRaisesRegex(RuntimeError, "--domain-volume"):
                demo.init_config(target, domain_volume=None, domain_path=None,
                                 base_image="mxl-lab:latest",
                                 source_count=8, force=False)
        target.write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "--force"):
            demo.init_config(target, domain_volume="one", domain_path=None,
                             base_image="mxl-lab:latest",
                             source_count=8, force=False)

    def test_init_supports_a_direct_mxl_domain_path(self):
        target = self.root / "bind-config.json"
        with mock.patch.object(demo, "detect_docker_command", return_value=["docker"]), \
             mock.patch.object(demo, "image_exists", return_value=True):
            demo.init_config(target, domain_volume=None, domain_path=str(self.domain),
                             base_image="mxl-lab:latest", source_count=4, force=False)
        value = json.loads(target.read_text())
        expected = str(self.domain.resolve())
        self.assertEqual(value["domain_host_path"], expected)
        self.assertEqual(value["domain_mount_source"], expected)

    def test_init_refuses_both_domain_selectors(self):
        with mock.patch.object(demo, "detect_docker_command", return_value=["docker"]):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                demo.init_config(self.root / "bad.json", domain_volume="one",
                                 domain_path=str(self.domain), base_image="mxl-lab:latest",
                                 source_count=4, force=False)

    def test_up_runs_guarded_sequence_and_aliases_are_simple(self):
        cfg = demo.load_config(self.config_file)
        order = mock.Mock()
        with mock.patch.object(demo, "doctor", return_value=0) as doctor, \
             mock.patch.object(demo, "ensure_no_conflicts") as conflicts, \
             mock.patch.object(demo, "bootstrap") as bootstrap, \
             mock.patch.object(demo, "launch") as launch:
            order.attach_mock(conflicts, "conflicts")
            order.attach_mock(bootstrap, "bootstrap")
            demo.up(cfg, apply=True)
        doctor.assert_called_once_with(cfg)
        bootstrap.assert_called_once_with(cfg, apply=True)
        launch.assert_called_once_with(cfg, apply=True)
        # Leftover containers and busy ports are refused before slow image builds.
        self.assertEqual([c[0] for c in order.mock_calls], ["conflicts", "bootstrap"])

    def test_unreadable_domain_gives_the_next_safe_action(self):
        # Docker-group users cannot traverse /var/lib/docker; Python 3.12 raises
        # PermissionError from Path.is_dir there instead of returning False.
        denied = mock.patch.object(demo.pathlib.Path, "is_dir",
                                   side_effect=PermissionError(13, "Permission denied"))
        cfg = demo.load_config(self.config_file)
        with denied:
            with self.assertRaisesRegex(RuntimeError, "sudo python3"):
                demo.discover(cfg)
        target = self.root / "generated.json"
        with mock.patch.object(demo, "detect_docker_command", return_value=["docker"]), \
             mock.patch.object(demo, "discover_domain_volumes", return_value=["zoomiso-domain"]), \
             mock.patch.object(demo, "volume_mountpoint", return_value=str(self.domain)), \
             denied:
            with self.assertRaisesRegex(RuntimeError, "not readable.*sudo python3"):
                demo.init_config(target, domain_volume=None, domain_path=None,
                                 base_image="mxl-lab:latest",
                                 source_count=4, force=False)
        self.assertFalse(target.exists())

    def test_doctor_requires_only_documented_executables(self):
        cfg = demo.load_config(self.config_file)
        checked = []

        def which(name):
            checked.append(name)
            return "/usr/bin/" + name

        with mock.patch.object(demo.shutil, "which", side_effect=which), \
             mock.patch.object(demo.subprocess, "run",
                               return_value=demo.subprocess.CompletedProcess([], 1)), \
             mock.patch.object(demo, "discover", return_value=[]):
            demo.doctor(cfg)
        self.assertEqual(checked, ["docker", "git"])

    def test_doctor_probes_the_licensed_base_image_contract(self):
        cfg = demo.load_config(self.config_file)
        with mock.patch.object(demo.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(demo, "discover", return_value=demo.discover(cfg)), \
             mock.patch.object(demo, "image_exists", return_value=True), \
             mock.patch.object(demo, "base_image_contract",
                               return_value=(False, "gstreamer:mxlsrc")) as probe, \
             mock.patch.object(demo.subprocess, "run",
                               return_value=demo.subprocess.CompletedProcess([], 0)):
            self.assertEqual(demo.doctor(cfg), 1)
        probe.assert_called_once_with(cfg)
        self.assertIn('"identity"', demo.BASE_IMAGE_PROBE)

    def test_plan_rediscovers_current_flows(self):
        cfg = demo.load_config(self.config_file)
        stale = pathlib.Path(cfg["state_dir"])
        stale.mkdir()
        (stale / "resolved.json").write_text(json.dumps({"sources": [{"flow_id": "stale"}]}))
        with mock.patch.object(demo, "load_config", return_value=cfg), \
             mock.patch.object(demo, "prepare", wraps=demo.prepare) as prepare, \
             mock.patch.object(demo, "run"):
            self.assertEqual(demo.main(["plan", "--config", str(self.config_file)]), 0)
        prepare.assert_called_once_with(cfg)

    def test_launch_removes_a_deployment_whose_container_exits_after_start(self):
        cfg = demo.load_config(self.config_file)
        resolved = demo.prepare(cfg)
        removed = []

        def run(command, *args, **kwargs):
            if command[1] == "inspect" and "{{.State.Status}}|{{.RestartCount}}" in command:
                name = command[-1]
                status = "exited|0" if name.endswith("multiview-confidence") else "running|0"
                return demo.subprocess.CompletedProcess(command, 0, status + "\n", "")
            if command[1] == "logs":
                return demo.subprocess.CompletedProcess(command, 0, "missing flow\n", "")
            if command[1] == "rm":
                removed.append(command)
            return demo.subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(demo, "ensure_launchable"), \
             mock.patch.object(demo, "prepare", return_value=resolved), \
             mock.patch.object(demo, "wait_url"), \
             mock.patch.object(demo.time, "sleep"), \
             mock.patch.object(demo.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "test-demo-multiview-confidence \\(exited\\|0\\)"):
                demo.launch(cfg, apply=True)
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(removed[0]) - 3, 7, "all seven started containers must be removed")
        log = pathlib.Path(cfg["state_dir"]) / "launch-failure-test-demo-multiview-confidence.log"
        self.assertEqual(log.read_text(), "missing flow\n")
        self.assertEqual(demo.parse_args(["check"]).config, pathlib.Path("demo-config.json"))
        self.assertEqual(demo.parse_args(["down"]).command, "down")

    def test_acceptance_evaluator_passes_a_clean_advancing_demo(self):
        source = {"torn": 0, "missing": 0, "live": True}
        before = {
            "containers": {suffix: "running|0|test-demo" for suffix in demo.OWNED_SUFFIXES},
            "core": {**{key: 0 for key in demo.HARD_COUNTERS}, "status": "running", "routed": 100},
            "youtube": {"source_count": 4, "audio_ready": True, "running": False},
            "confidence": {
                suffix: {"frames": 10, "encoder_restarts": 0, "sources": {"one": source}}
                for suffix in ("program-confidence", "preview-confidence", "multiview-confidence")
            },
        }
        after = json.loads(json.dumps(before))
        after["core"]["routed"] = 200
        for item in after["confidence"].values():
            item["frames"] = 20
        result = demo.evaluate_acceptance(before, after, 4)
        self.assertTrue(result["passed"])
        self.assertTrue(result["checks"]["core_hard_counters_zero"])

    def test_acceptance_evaluator_fails_counter_motion_and_terminal_torn(self):
        source = {"torn": 0, "missing": 0, "live": True}
        before = {
            "containers": {suffix: "running|0|test-demo" for suffix in demo.OWNED_SUFFIXES},
            "core": {**{key: 0 for key in demo.HARD_COUNTERS}, "status": "running", "routed": 100},
            "youtube": {"source_count": 4, "audio_ready": True, "running": True,
                        "output": {"ready": True}},
            "confidence": {
                suffix: {"frames": 10, "encoder_restarts": 0, "sources": {"one": source}}
                for suffix in ("program-confidence", "preview-confidence", "multiview-confidence")
            },
        }
        after = json.loads(json.dumps(before))
        after["core"]["routed"] = 200
        after["core"]["too_late"] = 1
        for item in after["confidence"].values():
            item["frames"] = 20
        after["confidence"]["multiview-confidence"]["sources"]["one"]["torn"] = 1
        result = demo.evaluate_acceptance(before, after, 4)
        self.assertFalse(result["passed"])
        self.assertEqual(result["hard_counter_deltas"]["too_late"], 1)


if __name__ == "__main__":
    unittest.main()
