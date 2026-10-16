from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate.py"
SPEC = importlib.util.spec_from_file_location("validate_extensions", MODULE_PATH)
assert SPEC and SPEC.loader
validate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate)


class ManifestValidationTests(unittest.TestCase):
    def manifest(self):
        return {
            "schema_version": 1,
            "name": "example-output",
            "version": "1.0.0",
            "kind": "output",
            "maturity": "prototype",
            "requires_core_api": "^1.0",
            "entrypoint": None,
            "capabilities": [],
            "flow_roles": {"reads": ["program.video"], "publishes": []},
            "health": {
                "state_path": "/api/v1/state",
                "liveness_path": "/api/v1/health/live",
                "readiness_path": "/api/v1/health/ready",
                "bind_scope": "loopback",
            },
        }

    def write(self, directory, value):
        path = pathlib.Path(directory) / "extension.json"
        path.write_text(json.dumps(value))
        return path

    def test_common_health_contract_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest()
            validate.extension(self.write(directory, manifest))
            del manifest["health"]
            with self.assertRaisesRegex(ValueError, "health.bind_scope"):
                validate.extension(self.write(directory, manifest))

    def test_profiles_cannot_select_undocumented_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            old_root = validate.ROOT
            try:
                validate.ROOT = pathlib.Path(directory)
                selected = validate.ROOT / "outputs" / "example"
                selected.mkdir(parents=True)
                profile = {
                    "schema_version": 1,
                    "name": "example-profile",
                    "audience": "test",
                    "core": {"repository": "https://example.invalid/core", "ref": "main"},
                    "extensions": ["outputs/example"],
                }
                path = validate.ROOT / "profile.json"
                path.write_text(json.dumps(profile))
                with self.assertRaisesRegex(ValueError, "missing extension manifest"):
                    validate.profile(path)
                (selected / "extension.json").write_text("{}")
                validate.profile(path)
            finally:
                validate.ROOT = old_root

    def test_profiles_cannot_escape_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            old_root = validate.ROOT
            try:
                validate.ROOT = pathlib.Path(directory) / "repo"
                validate.ROOT.mkdir()
                path = validate.ROOT / "profile.json"
                path.write_text(json.dumps({
                    "schema_version": 1,
                    "name": "escape-profile",
                    "audience": "test",
                    "core": {"repository": "https://example.invalid/core", "ref": "v1"},
                    "extensions": ["../../outside"],
                }))
                with self.assertRaisesRegex(ValueError, "escapes repository"):
                    validate.profile(path)
            finally:
                validate.ROOT = old_root

    def test_health_paths_and_loopback_scope_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest()
            manifest["health"]["readiness_path"] = "ready"
            with self.assertRaisesRegex(ValueError, "health.readiness_path"):
                validate.extension(self.write(directory, manifest))
            manifest = self.manifest()
            manifest["health"]["bind_scope"] = "public"
            with self.assertRaisesRegex(ValueError, "health.bind_scope"):
                validate.extension(self.write(directory, manifest))


if __name__ == "__main__":
    unittest.main()
