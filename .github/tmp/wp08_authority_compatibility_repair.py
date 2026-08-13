from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one replacement in {path}, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


protocol = "services/agent-service/src/agent_core/lifecycle/protocol.py"
# Keep the canonical static schema backward compatible for existing readers,
# while making the provider-facing projection use a direct required property.
replace_once(
    protocol,
    '''                                "required": ["domain", "operation", "object_type", "requested_outputs"],
''',
    '''                                "required": ["domain", "operation", "object_type"],
                                "allOf": [{"required": ["requested_outputs"]}],
''',
)
replace_once(
    protocol,
    '''    schema = deepcopy(DECLARE_TURN_GOALS_SCHEMA)
    ids = list(dict.fromkeys(
''',
    '''    schema = deepcopy(DECLARE_TURN_GOALS_SCHEMA)
    effect_schema = (
        schema["function"]["parameters"]["properties"]["goals"]["items"]
        ["properties"]["requested_effect"]
    )
    provider_required = list(effect_schema.get("required") or [])
    if "requested_outputs" not in provider_required:
        provider_required.append("requested_outputs")
    effect_schema["required"] = provider_required
    # DeepSeek/OpenAI-compatible function calling does not reliably enforce
    # an allOf-only required field.  Flatten only the provider projection;
    # canonical Runtime validation remains strict and capability-independent.
    effect_schema.pop("allOf", None)
    ids = list(dict.fromkeys(
''',
)

planning = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    planning,
    '''    errors: list[str] = []
    if not raw_goals:
''',
    '''    errors: list[str] = []
    semantic_output_identity_errors: list[str] = []
    if not raw_goals:
''',
)
replace_once(
    planning,
    '''            requested_effect = normalize_requested_effect(raw_effect, description=description)
            if not isinstance(requested_effect.get("requested_outputs"), list):
                raise ValueError("requested_effect.requested_outputs_required_for_new_turn")
            errors.extend(_validate_semantic_output_effect(
                requested_effect,
                user_text=user_text,
                goal_evidence_span=evidence_span,
                goal_id=goal_id,
            ))
            effect_source = "model_semantic_output_effect"
''',
    '''            requested_effect = normalize_requested_effect(raw_effect, description=description)
            if not isinstance(requested_effect.get("requested_outputs"), list):
                semantic_output_identity_errors.append(
                    f"invalid_requested_effect:{goal_id}:requested_effect.requested_outputs_required_for_new_turn"
                )
            errors.extend(_validate_semantic_output_effect(
                requested_effect,
                user_text=user_text,
                goal_evidence_span=evidence_span,
                goal_id=goal_id,
            ))
            effect_source = (
                "model_semantic_output_effect"
                if "requested_outputs" in requested_effect
                else "legacy_direct_compatibility_effect"
            )
''',
)
replace_once(
    planning,
    '''    contract_goals = []
    for goal in goals:
''',
    '''    # Preserve independent alignment/granularity feedback priority, but
    # never freeze a new formal contract whose sole effect identity is the
    # legacy compatibility triple.  Historical readers remain compatible.
    if semantic_output_identity_errors:
        return ({
            "ok": False,
            "code": "GOAL_DECLARATION_INVALID",
            "message": "本轮正式语义输出身份缺失，Runtime 不会以兼容字段替代 canonical semantic output。",
            "data": {
                "errors": semantic_output_identity_errors,
                "alignment_proof": alignment.as_dict(),
                "granularity_proof": granularity.as_dict(),
                **_goal_declaration_repair_context(user_text),
            },
        }, None)

    contract_goals = []
    for goal in goals:
''',
)

verifier = "services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py"
replace_once(
    verifier,
    '''    contract: ToolCapabilityContract,
) -> dict[str, Any]:
''',
    '''    contract: ToolCapabilityContract | None = None,
) -> dict[str, Any]:
''',
)

# The new regression must exercise the post-verifier fail-closed boundary
# without calling an external model.
test = "services/agent-service/tests/runtime/test_wp08_attempt3_single_authority_repairs.py"
replace_once(
    test,
    '''from __future__ import annotations

from agent_core.context.reference_resolution''',
    '''from __future__ import annotations

from types import SimpleNamespace

from agent_core.context.reference_resolution''',
)
replace_once(
    test,
    '''def test_new_goal_declaration_rejects_legacy_effect_as_sole_identity() -> None:
    registry = _installed_registry()
    result, plan = validate_goal_declaration(
''',
    '''def test_new_goal_declaration_rejects_legacy_effect_as_sole_identity(monkeypatch) -> None:
    import agent_core.lifecycle.goal_planning as planning_module

    exact = SimpleNamespace(
        exact=True,
        verdict="exact",
        as_dict=lambda: {"verdict": "exact", "source": "test"},
    )
    monkeypatch.setattr(planning_module, "verify_goal_alignment", lambda **_: exact)
    monkeypatch.setattr(planning_module, "verify_goal_granularity", lambda **_: exact)
    registry = _installed_registry()
    result, plan = validate_goal_declaration(
''',
)
