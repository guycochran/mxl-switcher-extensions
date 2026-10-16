from __future__ import annotations

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "check_health.py"
SPEC = importlib.util.spec_from_file_location("check_health", MODULE_PATH)
assert SPEC and SPEC.loader
check_health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_health)


MANIFEST = {
    "name": "program-meeting-return",
    "health": {
        "state_path": "/api/v1/state",
        "liveness_path": "/api/v1/health/live",
        "readiness_path": "/api/v1/health/ready",
    },
}


class CheckHealthTests(unittest.TestCase):
    def test_ready_adapter_passes_all_checks(self):
        responses = {
            "/api/v1/health/live": (200, {"live": True}),
            "/api/v1/health/ready": (200, {"ready": True}),
            "/api/v1/state": (200, {
                "name": "program-meeting-return", "state": "running",
                "ready": True, "revision": 4, "restarts": 0,
            }),
        }
        result = check_health.check(
            MANIFEST,
            "http://127.0.0.1:18120/",
            lambda url: responses[url.removeprefix("http://127.0.0.1:18120")],
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["revision"], 4)

    def test_live_but_not_ready_fails_closed(self):
        def fetch(url):
            if url.endswith("/live"):
                return 200, {"live": True}
            if url.endswith("/ready"):
                return 503, {"ready": False, "state": "starting"}
            return 200, {"name": "program-meeting-return", "state": "starting", "ready": False}

        result = check_health.check(MANIFEST, "http://localhost:18120", fetch)
        self.assertFalse(result["passed"])
        self.assertTrue(result["checks"]["live"])
        self.assertFalse(result["checks"]["ready"])

    def test_wrong_adapter_identity_fails(self):
        def fetch(url):
            if url.endswith("/live"):
                return 200, {"live": True}
            if url.endswith("/ready"):
                return 200, {"ready": True}
            return 200, {"name": "unexpected-writer", "state": "running", "ready": True}

        result = check_health.check(MANIFEST, "http://[::1]:18120", fetch)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["identity"])

    def test_non_loopback_and_credentialed_urls_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            check_health.validate_loopback_url("http://192.0.2.1:18120")
        with self.assertRaisesRegex(ValueError, "user info"):
            check_health.validate_loopback_url("http://user@127.0.0.1:18120")


if __name__ == "__main__":
    unittest.main()
