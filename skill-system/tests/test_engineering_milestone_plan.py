from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from engineering_milestone_plan import (  # noqa: E402
    EngineeringMilestoneError,
    bind_plan,
    build_case_evidence,
    certify_plan,
    validate_binding,
    validate_plan,
)

PLAN_PATH = ROOT / "governance" / "milestones" / "issue167-pack-a.json"
OBSERVED_PATH = ROOT / "governance" / "milestones" / "issue167-pack-a-observed.json"
EXPECTED_CASES = [
    "correction_earphone_to_keyboard_two_goals",
    "correction_refund_to_logistics",
    "pronoun_them_after_signed_filter",
    "pronoun_second_item_from_visible_list",
    "pronoun_not_hidden_draft_focus",
    "visible_subset_then_action_clarify",
    "task_refund_pause_logistics_resume",
    "task_cancelled_then_new_task",
    "similar_courier_phone_not_logistics",
    "multiwrite_all_signed_refund",
    "unsupported_modify_refund_review_result",
    "authority_old_grant_expired",
]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _bound() -> tuple[dict, dict, dict]:
    plan = _load(PLAN_PATH)
    observed = _load(OBSERVED_PATH)
    binding = bind_plan(
        plan,
        candidate_head_sha=observed["candidate_head_sha"],
        quality_run_id=observed["quality_run_id"],
        quality_run_attempt=observed["quality_run_attempt"],
    )
    return plan, observed, binding


def _evidence(binding: dict, observed: dict) -> list[dict]:
    return [
        build_case_evidence(
            binding,
            case_id=item["id"],
            status=item["status"],
            evidence_ref=f"{observed['product_evidence_ref']}#{item['test_name']}",
            test_name=item["test_name"],
        )
        for item in observed["cases"]
    ]


class EngineeringMilestonePlanTests(unittest.TestCase):
    def test_issue167_pack_a_manifest_is_exact_and_ordered(self) -> None:
        plan = validate_plan(_load(PLAN_PATH))
        self.assertEqual(plan["issue_number"], 167)
        self.assertEqual(plan["source_pr"], 1580)
        self.assertEqual([item["id"] for item in plan["milestones"]], EXPECTED_CASES)
        self.assertEqual(len(plan["milestones"]), 12)
        self.assertEqual(plan["authority_effect"], "none")
        self.assertFalse(plan["merge_allowed"])
        self.assertFalse(plan["deploy_allowed"])

    def test_real_recertification_proves_all_twelve_cases_and_transport(self) -> None:
        _, observed, binding = _bound()
        result = certify_plan(
            binding,
            _evidence(binding, observed),
            product_verdict=observed["product_verdict"],
            transport_verdict=observed["transport_verdict"],
            product_evidence_ref=observed["product_evidence_ref"],
            transport_evidence_ref=observed["transport_evidence_ref"],
        )
        self.assertEqual(result["milestone_count"], 12)
        self.assertEqual(result["passed_milestone_count"], 12)
        self.assertTrue(result["product_certified"])
        self.assertTrue(result["final_certified"])
        self.assertEqual(result["decision"], "CERTIFIED")
        self.assertFalse(result["product_repair_authorized"])
        carrier = observed["recertification"]
        self.assertEqual(carrier["carrier_pr"], 1875)
        self.assertEqual(carrier["transport_fix_pr"], 1779)
        self.assertEqual(
            carrier["merge_snapshot_parent_head"],
            observed["candidate_head_sha"],
        )
        self.assertEqual(
            carrier["merge_snapshot_parent_base"],
            carrier["transport_fix_head_sha"],
        )
        self.assertFalse(carrier["product_head_changed"])
        self.assertFalse(carrier["product_runtime_changed"])

    def test_transport_failure_never_reclassifies_green_product_as_product_red(self) -> None:
        _, observed, binding = _bound()
        result = certify_plan(
            binding,
            _evidence(binding, observed),
            product_verdict="PASS",
            transport_verdict="FAIL",
            product_evidence_ref="quick-green",
            transport_evidence_ref="publisher-red",
        )
        self.assertEqual(result["decision"], "WAIT_TRANSPORT")
        self.assertEqual(result["failed_milestones"], [])
        self.assertFalse(result["product_repair_authorized"])

    def test_all_product_and_transport_green_is_final_certification(self) -> None:
        _, observed, binding = _bound()
        result = certify_plan(
            binding,
            _evidence(binding, observed),
            product_verdict="PASS",
            transport_verdict="PASS",
            product_evidence_ref="quick-green",
            transport_evidence_ref="required-status-green",
        )
        self.assertEqual(result["decision"], "CERTIFIED")
        self.assertTrue(result["product_certified"])
        self.assertTrue(result["final_certified"])
        self.assertFalse(result["merge_allowed"])
        self.assertFalse(result["deploy_allowed"])

    def test_missing_case_evidence_cannot_certify(self) -> None:
        _, observed, binding = _bound()
        evidence = _evidence(binding, observed)[:-1]
        result = certify_plan(
            binding,
            evidence,
            product_verdict="PASS",
            transport_verdict="PASS",
            product_evidence_ref="quick-green",
            transport_evidence_ref="required-status-green",
        )
        self.assertEqual(result["decision"], "INCOMPLETE_EVIDENCE")
        self.assertEqual(result["missing_milestones"], ["authority_old_grant_expired"])
        self.assertFalse(result["final_certified"])

    def test_real_case_red_is_product_red_even_when_transport_is_green(self) -> None:
        _, observed, binding = _bound()
        evidence = _evidence(binding, observed)
        target = evidence[2]
        evidence[2] = build_case_evidence(
            binding,
            case_id=target["case_id"],
            status="FAIL",
            evidence_ref=target["evidence_ref"],
            test_name=target["test_name"],
        )
        result = certify_plan(
            binding,
            evidence,
            product_verdict="FAIL",
            transport_verdict="PASS",
            product_evidence_ref="quick-red",
            transport_evidence_ref="required-status-green",
        )
        self.assertEqual(result["decision"], "PRODUCT_RED")
        self.assertEqual(result["first_failure"], "pronoun_them_after_signed_filter")
        self.assertFalse(result["product_certified"])

    def test_stale_head_is_rejected_before_certification(self) -> None:
        _, _, binding = _bound()
        with self.assertRaisesRegex(EngineeringMilestoneError, "candidate head drifted"):
            validate_binding(binding, current_head_sha="f" * 40)

    def test_case_evidence_cannot_be_replayed_on_another_run(self) -> None:
        plan, observed, binding = _bound()
        evidence = _evidence(binding, observed)[0]
        other = bind_plan(
            plan,
            candidate_head_sha=observed["candidate_head_sha"],
            quality_run_id=observed["quality_run_id"] + 1,
            quality_run_attempt=1,
        )
        with self.assertRaisesRegex(
            EngineeringMilestoneError,
            "case evidence binding mismatch|binding digest mismatch",
        ):
            certify_plan(
                other,
                [evidence],
                product_verdict="PASS",
                transport_verdict="PASS",
                product_evidence_ref="x",
                transport_evidence_ref="y",
            )

    def test_skipped_case_is_not_executable_certification(self) -> None:
        _, _, binding = _bound()
        with self.assertRaisesRegex(EngineeringMilestoneError, "executed PASS or FAIL"):
            build_case_evidence(
                binding,
                case_id=EXPECTED_CASES[0],
                status="SKIPPED",
                evidence_ref="junit:test",
                test_name="test_case",
            )

    def test_tampered_binding_digest_fails_closed(self) -> None:
        _, _, binding = _bound()
        tampered = dict(binding)
        tampered["quality_run_id"] += 1
        with self.assertRaisesRegex(EngineeringMilestoneError, "binding digest mismatch"):
            validate_binding(tampered)

    def test_duplicate_case_evidence_fails_closed(self) -> None:
        _, observed, binding = _bound()
        one = _evidence(binding, observed)[0]
        with self.assertRaisesRegex(EngineeringMilestoneError, "duplicate milestone evidence"):
            certify_plan(
                binding,
                [one, one],
                product_verdict="PASS",
                transport_verdict="PASS",
                product_evidence_ref="x",
                transport_evidence_ref="y",
            )


if __name__ == "__main__":
    unittest.main()
