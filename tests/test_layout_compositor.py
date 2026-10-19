import argparse
import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "processing" / "layout-compositor"


def load(name):
    spec = importlib.util.spec_from_file_location(name, MODULE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


layout = load("layout_compositor")


class LayoutCompositorTests(unittest.TestCase):
    def test_templates_cover_canvas_without_overlap(self):
        for count in layout.SUPPORTED_LAYOUTS:
            tiles = layout.layout_tiles(count)
            self.assertEqual(len(tiles), count)
            self.assertTrue(all(tile.width > 0 and tile.height > 0 for tile in tiles))
            self.assertTrue(all(tile.x + tile.width <= 1920 for tile in tiles))
            self.assertTrue(all(tile.y + tile.height <= 1080 for tile in tiles))
            area = sum(tile.width * tile.height for tile in tiles)
            self.assertEqual(area, 1920 * 1080)

    def test_only_defined_templates_are_accepted(self):
        for count in (1, 5, 7, 9):
            with self.assertRaises(ValueError):
                layout.layout_tiles(count)

    def test_pipeline_reads_each_source_and_publishes_one_paced_flow(self):
        args = argparse.Namespace(
            domain="/mxl-domain", input=["one", "two", "three"], output_flow_id="scene",
            output_label="scene", width=1920, height=1080, rate="30000/1001",
        )
        value = layout.pipeline_description(args)
        self.assertEqual(value.count("mxlsrc "), 3)
        self.assertEqual(value.count("mxlsink "), 1)
        self.assertIn("flow-id=scene", value)
        self.assertIn("clocksync sync-to-first=false", value)
        self.assertIn("sink_0::width=1280", value)
        self.assertIn("sink_1::xpos=1280", value)
        self.assertIn("sink_0::sizing-policy=keep-aspect-ratio", value)
        self.assertNotIn("videoconvert", value)

    def test_bank_client_is_loopback_only_and_posts_exact_scene(self):
        sys.path.insert(0, str(MODULE_DIR))
        try:
            client = load("bank_client")
        finally:
            sys.path.remove(str(MODULE_DIR))
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch.object(client.urllib.request, "urlopen", return_value=response) as opened, \
             mock.patch.object(client.json, "load", return_value={"ready": True, "bank": "a"}):
            result = client.prepare("a", 3, [1, 2, 3])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:9721/api/v1/scene")
        self.assertEqual(request.method, "POST")
        self.assertTrue(result["ready"])
        with mock.patch.dict(client.os.environ, {"MXL_LAYOUT_BANK_A_URL": "http://public.invalid:9721"}):
            with self.assertRaisesRegex(ValueError, "loopback"):
                client.bank_url("a")


if __name__ == "__main__":
    unittest.main()
