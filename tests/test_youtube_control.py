#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "outputs" / "rtmp" / "youtube_control.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("youtube_control", MODULE_PATH)
control = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(control)


class YouTubeControlTests(unittest.TestCase):
    def make_flow(self, root, flow_id, output, device="device-a", age=0):
        directory = pathlib.Path(root) / f"{flow_id}.mxl-flow"
        directory.mkdir()
        (directory / "flow_def.json").write_text(json.dumps({
            "id": flow_id,
            "label": f"ZoomISO Cloud Instance Output {output} Video",
            "media_type": "video/v210",
            "device_id": device,
        }), encoding="utf-8")
        data = directory / "data"
        data.write_bytes(b"")
        when = time.time() - age
        data.touch()
        os.utime(data, (when, when))

    def test_discovery_returns_only_active_zoomiso_video_in_stable_order(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_flow(directory, "flow-2", 2)
            self.make_flow(directory, "flow-1b", 1, "device-b")
            self.make_flow(directory, "flow-1a", 1, "device-a")
            self.make_flow(directory, "stale", 3, age=20)
            found = control.discover_zoomiso_video(pathlib.Path(directory), active_seconds=5)
            self.assertEqual([item.flow_id for item in found], ["flow-1a", "flow-2"])

    def make_audio_flow(
        self, root, flow_id, media_type="audio/float32", age=0,
        output=None, device="audio-device",
    ):
        directory = pathlib.Path(root) / f"{flow_id}.mxl-flow"
        directory.mkdir()
        label = "Program Audio" if output is None else f"ZoomISO Cloud Instance Output {output} Audio"
        (directory / "flow_def.json").write_text(json.dumps({
            "id": flow_id, "label": label, "media_type": media_type,
            "device_id": device,
        }), encoding="utf-8")
        data = directory / "data"
        data.write_bytes(b"")
        when = time.time() - age
        os.utime(data, (when, when))

    def controller(self, root, audio_flow):
        args = control.parser().parse_args([
            "--domain", str(root), "--audio-flow", audio_flow,
            "--source-count", "4", "--active-seconds", "5",
        ])
        for output in range(1, 5):
            self.make_flow(root, f"video-{output}", output)
        return control.Controller(args)

    def test_audio_ready_requires_live_program_audio_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_audio_flow(directory, "audio-live")
            self.make_audio_flow(directory, "audio-stale", age=20)
            self.make_audio_flow(directory, "not-audio", media_type="video/v210")
            root = pathlib.Path(directory)
            self.assertTrue(control.audio_flow_active(root, "audio-live", 5))
            self.assertFalse(control.audio_flow_active(root, "audio-stale", 5))
            self.assertFalse(control.audio_flow_active(root, "not-audio", 5))
            self.assertFalse(control.audio_flow_active(root, "missing", 5))
            self.assertFalse(control.audio_flow_active(root, "../audio-live", 5))

    def test_start_refuses_missing_program_audio_before_opening_mxl(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self.controller(directory, "audio-missing")
            snapshot = controller.snapshot()
            self.assertEqual(snapshot["source_count"], 4)
            self.assertFalse(snapshot["audio_ready"])
            with self.assertRaisesRegex(ValueError, "Program audio"):
                controller.configure_and_start(
                    "rtmps://a.rtmps.youtube.com/live2", "abc-def"
                )
            self.assertIsNone(controller.worker)
            self.assertFalse(controller.configured)

    def test_mix_source_audio_pairs_every_selected_video_device(self):
        with tempfile.TemporaryDirectory() as directory:
            args = control.parser().parse_args([
                "--domain", directory, "--audio-flow", "fallback",
                "--source-count", "4", "--active-seconds", "5",
                "--mix-source-audio",
            ])
            for output in range(1, 5):
                device = f"device-{output}"
                self.make_flow(directory, f"video-{output}", output, device)
                self.make_audio_flow(
                    directory, f"audio-{output}", output=output, device=device,
                )
            controller = control.Controller(args)
            sources = controller.sources()
            self.assertEqual(
                controller.audio_flows(sources),
                ["audio-1", "audio-2", "audio-3", "audio-4"],
            )
            self.assertTrue(controller.audio_ready())
            snapshot = controller.snapshot()
            self.assertEqual(snapshot["audio_source_count"], 4)
            self.assertIn("4-source Program mix", snapshot["profile"]["audio"])

    def test_page_has_masked_key_and_never_embeds_a_key_value(self):
        body = control.page("csrf-value", 4).decode("utf-8")
        self.assertIn('type="password"', body)
        self.assertIn("csrf-value", body)
        self.assertIn("kept only in memory", body)
        self.assertNotIn("stream-key-file", body)

    def test_cpu_page_identifies_cpu_encoder(self):
        body = control.page("csrf-value", 4, "x264enc", 20000).decode("utf-8")
        self.assertIn("x264 CPU H.264 High", body)
        self.assertNotIn("NVENC H.264 High", body)
        self.assertIn("20M", body)
        self.assertIn("encoded fps", body)
        self.assertIn("sent Mbps", body)
        self.assertIn("CPU cores", body)
        self.assertIn("RSS MiB", body)
        self.assertIn("AAC buffers/s", body)
        self.assertIn("send queue ms", body)
        self.assertIn("source late ms", body)
        self.assertIn("all demo gates passing", body)

    def test_discovery_can_pin_an_instance_by_flow_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            self.make_flow(directory, "primary-1", 1, "primary-device")
            self.make_flow(directory, "secondary-1", 1, "secondary-device")
            self.make_flow(directory, "primary-2", 2, "primary-device-2")
            found = control.discover_zoomiso_video(
                pathlib.Path(directory),
                preferred_flow_ids={"primary-1", "primary-2"},
            )
            self.assertEqual([item.flow_id for item in found], ["primary-1", "primary-2"])

    def test_control_parser_does_not_require_secret_on_command_line(self):
        args = control.parser().parse_args(["--audio-flow", "audio-id"])
        self.assertIsNone(args.stream_key_file)
        self.assertEqual(args.port, 18121)
        self.assertFalse(args.container_bind)
        self.assertEqual(args.source_count, 4)
        self.assertEqual(args.active_seconds, 30.0)
        self.assertEqual(args.audio_rate_hz, 48000)
        self.assertEqual(args.rate, "30000/1001")
        self.assertEqual(args.video_encoder, "x264enc")
        self.assertIsNone(args.preferred_flow)
        self.assertEqual(args.audio_flow, ["audio-id"])
        self.assertFalse(args.mix_source_audio)


if __name__ == "__main__":
    unittest.main()
