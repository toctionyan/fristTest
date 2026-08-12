from __future__ import annotations


def _installed_registry():
    from agent_core.modules.registry import ModuleRegistry, configure_registry_providers
    from agent_modules.ecommerce.module import EcommerceModule

    modules = ModuleRegistry([EcommerceModule()])
    configure_registry_providers(
        runtime_registry=modules.build_runtime_registry,
        module_registry=lambda: modules,
    )
    return modules, modules.build_runtime_registry().capabilities


def _goal(goal_id: str, *output_ids: str, effect_kind: str = "read", subject_type: str = "shipment") -> dict:
    return {
        "goal_id": goal_id,
        "required": True,
        "requested_effect": {
            "effect_kind": effect_kind,
            "subject_type": subject_type,
            "requested_outputs": [
                {"output_id": output_id, "evidence_span": output_id}
                for output_id in output_ids
            ],
            "raw_description": "test",
        },
    }


def test_semantic_vocabulary_contains_zero_capability_meaning_without_availability_leak() -> None:
    modules, registry = _installed_registry()
    snapshot = modules.semantic_vocabulary_snapshot()
    outputs = {row["output_id"]: row for row in snapshot["outputs"]}
    assert snapshot["availability_exposed"] is False
    assert snapshot["tool_names_exposed"] is False
    assert "courier.contact.phone" in outputs
    serialized = str(snapshot)
    assert "get_order_logistics" not in serialized
    assert "report_unsupported_request" not in serialized

    from agent_core.runtime.capability_effects import discover_exact_effect_surface
    result = discover_exact_effect_surface(registry, [_goal("g1", "courier.contact.phone", subject_type="courier")])
    row = result["goals"][0]
    assert row["status"] == "absent_proven"
    assert row["completion_tools"] == []
    assert "report_unsupported_request" in row["candidate_tools"]


def test_logistics_status_eta_and_exact_combination_use_one_deterministic_coverage_compiler() -> None:
    _modules, registry = _installed_registry()
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    for outputs in [
        ("shipment.current_status",),
        ("shipment.eta",),
        ("shipment.tracking",),
        ("shipment.current_status", "shipment.eta"),
    ]:
        result = discover_exact_effect_surface(registry, [_goal("g1", *outputs)])
        row = result["goals"][0]
        assert row["status"] == "exact_supported"
        assert row["completion_tools"] == ["get_order_logistics"]
        assert row["match_basis"] == "structured_identity_exact_only"
        assert row["similarity_used"] is False


def test_supported_and_unsupported_siblings_do_not_collapse_into_each_other() -> None:
    _modules, registry = _installed_registry()
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    result = discover_exact_effect_surface(
        registry,
        [
            _goal("status", "shipment.current_status"),
            _goal("phone", "courier.contact.phone", subject_type="courier"),
        ],
    )
    by_id = {row["goal_id"]: row for row in result["goals"]}
    assert by_id["status"]["completion_tools"] == ["get_order_logistics"]
    assert by_id["phone"]["completion_tools"] == []
    assert by_id["phone"]["status"] == "absent_proven"
    assert result["unsupported_goal_ids"] == ["phone"]


def test_open_output_never_auto_coerces_to_registered_capability_and_legacy_checkpoint_still_reads() -> None:
    _modules, registry = _installed_registry()
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    open_goal = _goal("open", "open", subject_type="courier")
    open_goal["requested_effect"]["requested_outputs"][0]["open_description"] = "配送人员的私人联系方式"
    result = discover_exact_effect_surface(registry, [open_goal])
    assert result["goals"][0]["status"] == "absent_proven"

    legacy = {
        "goal_id": "legacy",
        "required": True,
        "requested_effect": {
            "domain": "order",
            "operation": "query_logistics",
            "object_type": "order",
        },
    }
    legacy_result = discover_exact_effect_surface(registry, [legacy])
    assert legacy_result["goals"][0]["completion_tools"] == ["get_order_logistics"]
