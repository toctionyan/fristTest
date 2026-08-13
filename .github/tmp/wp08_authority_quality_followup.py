from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one replacement in {path}, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


planning = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    planning,
    '''def validate_goal_declaration(
    *,
    state: dict[str, Any],
    args: dict[str, Any],
    capability_registry: CapabilityRegistry,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
''',
    '''def validate_goal_declaration(
    *,
    state: dict[str, Any],
    args: dict[str, Any],
    capability_registry: CapabilityRegistry,
    require_canonical_output_identity: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
''',
)
replace_once(
    planning,
    '''            if not isinstance(requested_effect.get("requested_outputs"), list):
                semantic_output_identity_errors.append(
                    f"invalid_requested_effect:{goal_id}:requested_effect.requested_outputs_required_for_new_turn"
                )
''',
    '''            if (
                require_canonical_output_identity
                and not isinstance(requested_effect.get("requested_outputs"), list)
            ):
                semantic_output_identity_errors.append(
                    f"invalid_requested_effect:{goal_id}:requested_effect.requested_outputs_required_for_new_turn"
                )
''',
)
replace_once(
    planning,
    '''    # Preserve independent alignment/granularity feedback priority, but
    # never freeze a new formal contract whose sole effect identity is the
    # legacy compatibility triple.  Historical readers remain compatible.
''',
    '''    # The live semantic-writer boundary opts into canonical output identity.
    # Direct/internal compatibility callers may still validate historical
    # declarations, but production model declarations cannot freeze a new
    # contract whose sole effect identity is the legacy compatibility triple.
''',
)

runtime = "services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py"
replace_once(
    runtime,
    '''                result, declared = validate_goal_declaration(
                    state=state,
                    args=args,
                    capability_registry=capability_registry,
                )
''',
    '''                result, declared = validate_goal_declaration(
                    state=state,
                    args=args,
                    capability_registry=capability_registry,
                    require_canonical_output_identity=True,
                )
''',
)

test = "services/agent-service/tests/runtime/test_wp08_attempt3_single_authority_repairs.py"
replace_once(
    test,
    '''        capability_registry=registry,
    )
    assert plan is None
''',
    '''        capability_registry=registry,
        require_canonical_output_identity=True,
    )
    assert plan is None
''',
)
