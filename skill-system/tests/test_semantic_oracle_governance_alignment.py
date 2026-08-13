from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
GOVERNANCE_TESTS = (
    ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py"
)


class SemanticOracleGovernanceAlignmentTests(unittest.TestCase):
    def test_production_matcher_keeps_canonical_requested_outputs_as_authority(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn(
            "accepted_outputs = _oracle_output_sets(case_id=case_id, expected=expected)",
            source,
        )
        self.assertIn(
            "_requested_output_identity(row) in accepted_outputs",
            source,
        )
        self.assertIn("require_canonical_output_identity=True", source)
        self.assertIn("planning_schemas(semantic_output_ids=semantic_output_ids)", source)
        self.assertNotIn(
            '_effect_identity(row.get("requested_effect")) == expected_effect',
            source,
        )

    def test_adversarial_governance_fixtures_still_cover_nonsemantic_match_boundaries(self) -> None:
        source = GOVERNANCE_TESTS.read_text(encoding="utf-8")
        start = source.index(
            "def test_protected_goal_smoke_accepts_schema_compliant_goals_without_expected_tools"
        )
        end = source.index(
            "def test_process_group_cleanup_handles_permission_error_after_parent_exit",
            start,
        )
        segment = source[start:end]

        # This source segment owns the historical span/fuzzy/ambiguity fixtures.
        # Duplicate-goal coverage lives later in the same architecture test file
        # and is intentionally not pulled into this segment assertion.
        self.assertGreaterEqual(segment.count("_match_oracle("), 4)
        self.assertIn("literal-span-extension", segment)
        self.assertIn("fuzzy-is-forbidden", segment)
        self.assertIn("ambiguous_effect =", segment)


if __name__ == "__main__":
    unittest.main()
