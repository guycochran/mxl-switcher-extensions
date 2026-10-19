import importlib.util
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RECIPE = ROOT / "deploy" / "online-demo" / "base-image"
SPEC = importlib.util.spec_from_file_location(
    "mxl_online_demo_recipe", ROOT / "deploy" / "online-demo" / "mxl_demo.py")
demo = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(demo)

# Ubuntu 24.04 package that provides each element the launcher's contract probe
# requires (`mxlsrc` and `mxlsink` come from the recipe's own gst-mxl-rs build).
ELEMENT_PACKAGE = {
    "appsrc": "gstreamer1.0-plugins-base", "queue": "gstreamer1.0-tools",
    "identity": "gstreamer1.0-tools", "x264enc": "gstreamer1.0-plugins-ugly",
    "h264parse": "gstreamer1.0-plugins-bad", "flvmux": "gstreamer1.0-plugins-good",
    "rtmpsink": "gstreamer1.0-plugins-bad", "rtmp2sink": "gstreamer1.0-plugins-bad",
    "compositor": "gstreamer1.0-plugins-base", "videoscale": "gstreamer1.0-plugins-base",
    "videoconvert": "gstreamer1.0-plugins-base", "videotestsrc": "gstreamer1.0-plugins-base",
    "cairooverlay": "gstreamer1.0-plugins-good", "audiomixer": "gstreamer1.0-plugins-base",
    "audioconvert": "gstreamer1.0-plugins-base", "audioresample": "gstreamer1.0-plugins-base",
    "rglimiter": "gstreamer1.0-plugins-good", "avenc_aac": "gstreamer1.0-libav",
    "aacparse": "gstreamer1.0-plugins-good",
}


class BaseImageRecipeTests(unittest.TestCase):
    def setUp(self):
        self.dockerfile = (RECIPE / "Dockerfile").read_text()
        self.guide = (RECIPE / "README.md").read_text()

    def test_pins_public_mxl_release_matching_documented_zoomiso(self):
        self.assertIn("ARG MXL_REF=v1.1.0", self.dockerfile)
        self.assertIn("https://github.com/dmf-mxl/mxl.git", self.dockerfile)
        self.assertIn("--branch ${MXL_REF}", self.dockerfile)
        self.assertIn("cargo build --release -p gst-mxl-rs", self.dockerfile)
        self.assertIn("0.1.0.77", self.guide)
        self.assertIn("v1.1.0", self.guide)

    def test_installs_every_contract_element_package_and_runtime(self):
        probe = demo.BASE_IMAGE_PROBE
        listed = set(re.findall(r'"([a-z0-9_]+)"', probe[probe.index("required = ("):]))
        for element, package in ELEMENT_PACKAGE.items():
            if element in listed:
                self.assertIn(package, self.dockerfile, element)
        self.assertTrue({"mxlsrc", "mxlsink"}.issubset(listed))
        for element in listed:
            # A new contract element must be mapped to a recipe package here.
            self.assertTrue(element in {"mxlsrc", "mxlsink"} or element in ELEMENT_PACKAGE, element)
        for runtime in ("python3-gi", "python3-numpy", "python3-opencv", "libcairo2",
                        "build-essential", "make"):
            self.assertIn(runtime, self.dockerfile)

    def test_headers_land_where_core_compiles_and_probe_checks(self):
        self.assertIn("/src/mxl/lib/include", self.dockerfile)
        self.assertIn("/src/mxl/lib/include/mxl", demo.BASE_IMAGE_PROBE)
        self.assertIn("COPY --from=build /usr/local/include/mxl/ /src/mxl/lib/include/mxl/",
                      self.dockerfile)

    def test_recipe_is_public_safe_and_builds_locally(self):
        text = self.dockerfile + self.guide
        self.assertIsNone(re.search(r"\b(?:10|172|192)\.\d+\.\d+\.\d+\b", text))
        for forbidden in ("docker login", "docker push", "password", "token"):
            self.assertNotIn(forbidden, text.lower())
        self.assertIn("not publish", self.guide)

    def test_guides_link_the_recipe(self):
        for doc in ("PREREQUISITES.md", "BASE_IMAGE.md"):
            self.assertIn("base-image/README.md",
                          (ROOT / "deploy" / "online-demo" / doc).read_text())


if __name__ == "__main__":
    unittest.main()
