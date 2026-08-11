from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class SemanticSingleWriterInvariantTests(unittest.TestCase):
    def test_goal_declaration_prompt_is_capability_blind_before_freeze(self) -> None:
        source = _source("services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py")
        start = source.index("def _loop_runtime_prompt")
        end = source.index("def _loop_system_prompt", start)
        prompt_builder = source[start:end]

        self.assertNotIn(
            "capability_effect_index(capability_registry) if capability_registry is not None",
            prompt_builder,
        )

    def test_static_semantic_writer_contract_does_not_require_registry_identity_alignment(self) -> None:
        source = _source("services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py")
        start = source.index("def _loop_static_system_prompt")
        end = source.index("def _loop_runtime_prompt", start)
        static_prompt = source[start:end]

        self.assertNotIn("当前部署登记的业务效果身份", static_prompt)
        self.assertNotIn("必须逐字段使用该精确身份", static_prompt)

    def test_goal_declaration_provider_surface_remains_single_writer_only(self) -> None:
        source = _source("services/agent-service/src/agent_core/lifecycle/protocol.py")
        start = source.index("def planning_schemas")
        end = source.index("def _unique_provider_values", start)
        planning_surface = source[start:end]

        self.assertIn("DECLARE_TURN_GOALS_SCHEMA", planning_surface)
        self.assertNotIn("CapabilityRegistry", planning_surface)
        self.assertNotIn("agent_loop_schemas", planning_surface)

    def test_frozen_semantic_contract_is_still_the_single_formal_authority(self) -> None:
        source = _source("services/agent-service/src/agent_core/lifecycle/semantic_contract.py")
        self.assertIn('"authority": "sole_formal_turn_semantics"', source)
        self.assertIn('"immutable": True', source)
        self.assertIn('"semantic_rewrite_allowed_after_freeze": False', source)


if __name__ == "__main__":
    unittest.main()
