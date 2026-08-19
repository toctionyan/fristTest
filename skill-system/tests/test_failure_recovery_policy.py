from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from failure_recovery_policy import (  # noqa: E402
    AUTO_DIAGNOSE,
    AUTO_REPAIR,
    AUTO_RETRY,
    HUMAN_REQUIRED,
    WAIT_EXTERNAL,
    decide_recovery,
)


class FailureRecoveryPolicyTests(unittest.TestCase):
    def test_bounded_repair_route_continues_without_user_interruption(self) -> None:
        decision = decide_recovery(
            repair_route={
                "repair_class": "CONTROL_PLANE_IMPLEMENTATION_REPAIRABLE",
                "automatic_write_allowed": True,
                "human_required": False,
            },
            classification="control_plane_implementation",
        )
        self.assertEqual(decision["disposition"], AUTO_REPAIR)
        self.assertTrue(decision["source_write_allowed"])
        self.assertFalse(decision["human_required"])
        self.assertFalse(decision["merge_allowed"])

    def test_transient_failure_retries_same_candidate_before_interrupting_user(self) -> None:
        decision = decide_recovery(
            repair_route={"repair_class": "TRANSIENT_INFRA_RETRYABLE"},
            classification="timeout",
            retry_count=1,
            max_retry_count=3,
        )
        self.assertEqual(decision["disposition"], AUTO_RETRY)
        self.assertTrue(decision["retry_allowed"])
        self.assertFalse(decision["source_write_allowed"])
        self.assertFalse(decision["human_required"])

    def test_environment_block_waits_without_mutating_source(self) -> None:
        decision = decide_recovery(
            repair_route={"repair_class": "ENVIRONMENT_BLOCKED"},
            classification="environment",
        )
        self.assertEqual(decision["disposition"], WAIT_EXTERNAL)
        self.assertFalse(decision["source_write_allowed"])
        self.assertFalse(decision["human_required"])

    def test_unknown_failure_gets_bounded_read_only_diagnosis_before_handoff(self) -> None:
        first = decide_recovery(
            repair_route={"repair_class": "UNKNOWN"},
            classification="unknown_failure_without_gate_evidence",
            diagnosis_attempt=0,
            max_diagnosis_attempts=2,
        )
        second = decide_recovery(
            repair_route={"repair_class": "UNKNOWN"},
            classification="unknown_failure_without_gate_evidence",
            diagnosis_attempt=2,
            max_diagnosis_attempts=2,
        )
        self.assertEqual(first["disposition"], AUTO_DIAGNOSE)
        self.assertTrue(first["diagnostic_allowed"])
        self.assertFalse(first["human_required"])
        self.assertEqual(second["disposition"], HUMAN_REQUIRED)
        self.assertTrue(second["human_required"])

    def test_baseline_oracle_and_test_boundaries_require_human_decision(self) -> None:
        for repair_class, classification in (
            ("AUTHORITY_ORACLE_CHANGE_REQUIRED", "protected_baseline_drift"),
            ("TEST_HARNESS_REPAIRABLE", "test_defect"),
            ("HUMAN_GATE", "policy_or_approval"),
        ):
            with self.subTest(repair_class=repair_class):
                decision = decide_recovery(
                    repair_route={"repair_class": repair_class},
                    classification=classification,
                )
                self.assertEqual(decision["disposition"], HUMAN_REQUIRED)
                self.assertTrue(decision["human_required"])
                self.assertFalse(decision["source_write_allowed"])
                self.assertFalse(decision["retry_allowed"])
                self.assertFalse(decision["diagnostic_allowed"])


if __name__ == "__main__":
    unittest.main()
