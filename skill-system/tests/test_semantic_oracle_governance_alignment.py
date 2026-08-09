from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
GOVERNANCE_TESTS = (
    ROOT / "services/agent-service/tests/architecture/test_quality_loop_governance.py"
)


class SemanticOracleGovernanceAlignmentTests(unittest.TestCase):
    def test_production_matcher_keeps_requested_effect_as_authority(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")
        self.assertIn(
            'expected_effect = _effect_identity(expected.get("requested_effect"))',
            source,
        )
        self.assertIn("if not all(expected_effect):", source)
        self.assertIn(
            '_effect_identity(row.get("requested_effect")) == expected_effect',
            source,
        )

    def test_adversarial_governance_fixtures_supply_authoritative_effects(self) -> None:
        source = GOVERNANCE_TESTS.read_text(encoding="utf-8")
        start = source.index(
            "def test_protected_goal_smoke_accepts_schema_compliant_goals_without_expected_tools"
        )
        end = source.index(
            "def test_process_group_cleanup_handles_permission_error_after_parent_exit",
            start,
        )
        segment = source[start:end]

        # These counterexamples test tool-independence, literal span extension,
        # fuzzy rejection and ambiguous containment. They must reach those
        # assertions through the current semantic contract rather than failing
        # early because their fixture still speaks only the legacy goal_type hint.
        self.assertGreaterEqual(segment.count('"requested_effect":'), 10)
        self.assertIn('"domain": "order"', segment)
        self.assertIn('"domain": "refund"', segment)
        self.assertIn("ambiguous_effect =", segment)


if __name__ == "__main__":
    unittest.main()
