from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from common.health import handler_for_snapshot, readiness


class HealthContractTests(unittest.TestCase):
    def test_readiness_fails_closed(self):
        status, payload = readiness({"state": "starting", "revision": 7})
        self.assertEqual(status, 503)
        self.assertEqual(payload, {"ready": False, "state": "starting", "revision": 7})

    def test_ready_snapshot_is_healthy(self):
        status, payload = readiness({"ready": True, "state": "running", "revision": 8})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])

    def test_http_contract_separates_live_ready_and_state(self):
        snapshot = {
            "name": "test-output",
            "state": "starting",
            "ready": False,
            "revision": 3,
            "metrics": {},
        }
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), handler_for_snapshot(lambda: dict(snapshot))
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/v1/health/live") as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), {"live": True})
            with urllib.request.urlopen(base + "/api/v1/state") as response:
                self.assertEqual(json.load(response)["revision"], 3)
            with self.assertRaises(urllib.error.HTTPError) as failure:
                urllib.request.urlopen(base + "/api/v1/health/ready")
            try:
                self.assertEqual(failure.exception.code, 503)
            finally:
                failure.exception.close()
            snapshot.update({"state": "running", "ready": True, "revision": 4})
            with urllib.request.urlopen(base + "/api/v1/health") as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(json.load(response)["ready"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
