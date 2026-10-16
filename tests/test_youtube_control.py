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

    def test_page_has_masked_key_and_never_embeds_a_key_value(self):
        body = control.page("csrf-value", 4).decode("utf-8")
        self.assertIn('type="password"', body)
        self.assertIn("csrf-value", body)
        self.assertIn("kept only in memory", body)
        self.assertNotIn("stream-key-file", body)

    def test_control_parser_does_not_require_secret_on_command_line(self):
        args = control.parser().parse_args(["--audio-flow", "audio-id"])
        self.assertIsNone(args.stream_key_file)
        self.assertEqual(args.port, 18121)
        self.assertFalse(args.container_bind)
        self.assertEqual(args.source_count, 4)
        self.assertEqual(args.active_seconds, 30.0)


if __name__ == "__main__":
    unittest.main()
