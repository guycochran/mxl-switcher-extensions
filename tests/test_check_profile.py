from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_profile.py"
SPEC = importlib.util.spec_from_file_location("check_profile", MODULE_PATH)
assert SPEC and SPEC.loader
check_profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_profile)


class ProfileReadinessTests(unittest.TestCase):
    def fixture(self, directory, *, core_ref="v1.0.0", maturity="candidate", entrypoint="run.py"):
        root = pathlib.Path(directory)
        package = root / "outputs" / "example"
        package.mkdir(parents=True)
        (package / "extension.json").write_text(json.dumps({
            "name": "example-output",
            "version": "1.0.0",
            "maturity": maturity,
            "entrypoint": entrypoint,
        }))
        profile = root / "profile.json"
        profile.write_text(json.dumps({
            "name": "example-profile",
            "core": {"repository": "https://example.invalid/core", "ref": core_ref},
            "extensions": ["outputs/example"],
        }))
        return root, profile

    def test_pinned_candidate_profile_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root, profile = self.fixture(directory)
            result = check_profile.evaluate(root, profile)
            self.assertTrue(result["promotion_ready"])
            self.assertEqual(result["blockers"], [])

    def test_mutable_core_concept_and_missing_entrypoint_all_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root, profile = self.fixture(
                directory, core_ref="main", maturity="concept", entrypoint=None
            )
            result = check_profile.evaluate(root, profile)
            self.assertFalse(result["promotion_ready"])
            self.assertEqual(
                {item["reason"] for item in result["blockers"]},
                {"mutable_ref", "maturity", "no_entrypoint"},
            )

    def test_current_full_demo_is_truthfully_not_promotion_ready(self):
        result = check_profile.evaluate(ROOT, ROOT / "profiles/full-demo/profile.json")
        self.assertFalse(result["promotion_ready"])
        self.assertTrue(any(item["reason"] == "maturity" for item in result["blockers"]))

    def test_profile_cannot_escape_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "repo"
            root.mkdir()
            profile = root / "profile.json"
            profile.write_text(json.dumps({
                "name": "escape",
                "core": {"repository": "https://example.invalid/core", "ref": "v1"},
                "extensions": ["../../outside"],
            }))
            with self.assertRaisesRegex(ValueError, "escapes repository"):
                check_profile.evaluate(root, profile)


if __name__ == "__main__":
    unittest.main()
