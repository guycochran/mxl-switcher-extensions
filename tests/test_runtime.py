from __future__ import annotations

import math
import unittest

from common.runtime import ExtensionRuntime, RestartBudget


class ExtensionRuntimeTests(unittest.TestCase):
    def test_lifecycle_snapshot_is_revisioned_and_readiness_is_explicit(self):
        runtime = ExtensionRuntime("program-meeting-return", "output")
        self.assertEqual(runtime.snapshot()["state"], "disabled")
        runtime.transition("starting")
        runtime.transition("ready")
        runtime.transition("running")
        runtime.publish_metrics({"video_queue_frames": 2, "audio_queue_ms": 40.0})
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["state"], "running")
        self.assertTrue(snapshot["ready"])
        self.assertEqual(snapshot["revision"], 4)
        self.assertEqual(snapshot["metrics"]["video_queue_frames"], 2)

    def test_degraded_can_remain_ready_but_requires_explicit_choice(self):
        runtime = ExtensionRuntime("program-ndi", "output")
        runtime.transition("starting")
        runtime.transition("ready")
        runtime.transition("running")
        runtime.transition("degraded", ready=True, error="receiver reconnecting")
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["state"], "degraded")
        self.assertTrue(snapshot["ready"])
        self.assertEqual(snapshot["last_error"], "receiver reconnecting")

    def test_invalid_transition_and_unexplained_failure_are_rejected(self):
        runtime = ExtensionRuntime("program-srt", "output")
        with self.assertRaisesRegex(ValueError, "disabled -> running"):
            runtime.transition("running")
        runtime.transition("starting")
        with self.assertRaisesRegex(ValueError, "requires an error"):
            runtime.transition("failed")

    def test_metrics_are_copied_json_safe_and_secret_free(self):
        runtime = ExtensionRuntime("program-rtmp", "output")
        metrics = {"queue": {"depth": 3}}
        runtime.publish_metrics(metrics)
        metrics["queue"]["depth"] = 99
        self.assertEqual(runtime.snapshot()["metrics"]["queue"]["depth"], 3)
        with self.assertRaisesRegex(ValueError, "sensitive keys"):
            runtime.publish_metrics({"api_token": "do-not-publish"})
        with self.assertRaises(ValueError):
            runtime.publish_metrics({"rate": math.nan})

    def test_restart_count_is_distinct_from_restart_budget(self):
        runtime = ExtensionRuntime("program-recorder", "recorder")
        runtime.record_restart()
        runtime.record_restart()
        self.assertEqual(runtime.snapshot()["restarts"], 2)


class RestartBudgetTests(unittest.TestCase):
    def test_budget_fails_closed_then_recovers_when_window_expires(self):
        budget = RestartBudget(2, 10.0)
        self.assertTrue(budget.claim(100.0))
        self.assertTrue(budget.claim(101.0))
        self.assertFalse(budget.claim(102.0))
        state = budget.snapshot(102.0)
        self.assertEqual(state["remaining"], 0)
        self.assertEqual(state["retry_after_seconds"], 8.0)
        self.assertTrue(budget.claim(110.0))
        self.assertEqual(budget.snapshot(110.0)["used"], 2)

    def test_zero_restart_budget_never_permits_restart(self):
        budget = RestartBudget(0, 60.0)
        self.assertFalse(budget.claim(1.0))
        self.assertEqual(budget.snapshot(1.0)["remaining"], 0)


if __name__ == "__main__":
    unittest.main()
