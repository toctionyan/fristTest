from __future__ import annotations

from tests.support.paths import agent_root


def _source(relative: str) -> str:
    return (agent_root(__file__) / relative).read_text(encoding="utf-8")


def test_goal_declaration_prompt_is_capability_blind_before_freeze() -> None:
    source = _source("src/agent_core/lifecycle/dialogue_runtime.py")
    start = source.index("def _loop_runtime_prompt")
    end = source.index("def _loop_system_prompt", start)
    prompt_builder = source[start:end]

    # The sole semantic writer may receive verified conversation/context, but
    # no current deployment effect vocabulary or capability semantic guidance
    # before FrozenSemanticContract exists. Tool hiding alone is insufficient:
    # publishing registered effect identities still biases open user meaning
    # toward the nearest installed capability.
    assert "capability_effect_index(capability_registry) if capability_registry is not None" not in prompt_builder


def test_static_semantic_writer_contract_does_not_require_registry_identity_alignment() -> None:
    source = _source("src/agent_core/lifecycle/dialogue_runtime.py")
    start = source.index("def _loop_static_system_prompt")
    end = source.index("def _loop_runtime_prompt", start)
    static_prompt = source[start:end]

    # Semantic meaning must be authored independently of deployment inventory.
    # Exact capability matching is a post-freeze Runtime responsibility.
    assert "当前部署登记的业务效果身份" not in static_prompt
    assert "必须逐字段使用该精确身份" not in static_prompt


def test_goal_declaration_provider_surface_remains_single_writer_only() -> None:
    source = _source("src/agent_core/lifecycle/protocol.py")
    start = source.index("def planning_schemas")
    end = source.index("def _unique_provider_values", start)
    planning_surface = source[start:end]

    assert "DECLARE_TURN_GOALS_SCHEMA" in planning_surface
    assert "CapabilityRegistry" not in planning_surface
    assert "agent_loop_schemas" not in planning_surface


def test_frozen_semantic_contract_is_still_the_single_formal_authority() -> None:
    source = _source("src/agent_core/lifecycle/semantic_contract.py")
    assert '"authority": "sole_formal_turn_semantics"' in source
    assert '"immutable": True' in source
    assert '"semantic_rewrite_allowed_after_freeze": False' in source
