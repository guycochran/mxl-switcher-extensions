#!/usr/bin/env python3
import importlib.util
import pathlib
import sys
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "outputs" / "rtmp" / "youtube_8up_canvas.py"
SPEC = importlib.util.spec_from_file_location("youtube_8up_canvas", MODULE_PATH)
canvas = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = canvas
SPEC.loader.exec_module(canvas)


class YouTubeCanvasTests(unittest.TestCase):
    def test_canvas_is_three_by_three_and_assets_are_dependency_free(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "brand.png"
            canvas.write_label_png(path, 1280, 720, "MXL LIVE | 4K")
            self.assertTrue(path.is_file())
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(canvas.tile_positions()[8], (2560, 1440))

    def test_four_up_uses_native_1080p_quadrants(self):
        width, height, positions = canvas.tile_layout(4)
        self.assertEqual((width, height), (1920, 1080))
        self.assertEqual(positions, [(0, 0), (1920, 0), (0, 1080), (1920, 1080)])

    def test_inputs_are_label_and_flow_pairs(self):
        self.assertEqual(canvas.parse_input("SOURCE 1=flow-1"), canvas.Input("SOURCE 1", "flow-1"))
        with self.assertRaises(ValueError):
            canvas.parse_input("flow-1")


if __name__ == "__main__":
    unittest.main()
