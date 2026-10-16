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
            "domain_mount_source": "test-domain",
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
            "input_delay_frames": 2,
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

    def test_prepare_keeps_runtime_ids_out_of_repository(self):
        cfg = demo.load_config(self.config_file)
        first = demo.prepare(cfg)
        second = demo.prepare(cfg)
        self.assertEqual(first["program_flow_id"], second["program_flow_id"])
        state = pathlib.Path(cfg["state_dir"])
        self.assertTrue((state / "core.conf").exists())
        self.assertIn("initial_program=zoomiso-1", (state / "core.conf").read_text())
        program = json.loads((state / "definitions" / "program.json").read_text())
        self.assertEqual(program["id"], first["program_flow_id"])

    def test_plan_includes_skin_one_wall_and_secret_free_youtube_control(self):
        cfg = demo.load_config(self.config_file)
        resolved = demo.prepare(cfg)
        commands = demo.commands(cfg, resolved)
        rendered = "\n".join(demo.quote(command) for command in commands)
        self.assertEqual(len(commands), 7)
        self.assertIn("test-demo-multiview-confidence", rendered)
        self.assertIn("--layout wall", rendered)
        self.assertIn("test-demo-skin", rendered)
        self.assertIn("--port 9710", rendered)
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
        value["skin_port"] = value["core_port"]
        self.config_file.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            demo.load_config(self.config_file)


if __name__ == "__main__":
    unittest.main()
