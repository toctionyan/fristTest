from __future__ import annotations

import json

from agent_core.modules import ModuleRegistry
from agent_core.runtime import capability_effects
from agent_modules.ecommerce import EcommerceModule
from agent_modules.ecommerce.capabilities import CAPABILITIES
from agent_modules.ecommerce.semantic_vocabulary import SEMANTIC_OUTPUTS


def _definitions():
    return {row.output_id: row for row in SEMANTIC_OUTPUTS}


def _capabilities():
    return {row.tool_name: row for row in CAPABILITIES}


def test_order_semantics_separate_attribute_discovery_from_prebound_details() -> None:
    definitions = _definitions()
    collection = definitions["order.collection"]
    details = definitions["order.details"]

    assert "发现、筛选或列出订单本身" in collection.description
    assert "即使最终只匹配一笔" in collection.description
    assert "已经" in details.description and "唯一绑定" in details.description
    assert "不用于按商品、状态或其他属性寻找订单本身" in details.description
    assert collection.legacy_effect_aliases == ("order.list:order",)
    assert details.legacy_effect_aliases == ("order.query_details:order",)

    capabilities = _capabilities()
    list_orders = capabilities["list_orders"]
    get_details = capabilities["get_order_details"]
    assert "键盘订单" in list_orders.discovery_examples
    assert "键盘订单" in get_details.exclusion_examples
    assert list_orders.completion_effects == ("order.list:order",)
    assert get_details.completion_effects == ("order.query_details:order",)


def test_refund_eligibility_semantics_separate_current_assessment_from_record_collection() -> None:
    definitions = _definitions()
    assessment = definitions["refund.eligibility"]
    records = definitions["refund.eligibility.collection"]

    assert "当前能否退款" in assessment.description
    assert "不等同于检索此前已经存在的资格记录集合" in assessment.description
    assert "此前已经产生" in records.description
    assert "不表示现在对一笔或多笔订单重新判断能否退款" in records.description
    assert assessment.legacy_effect_aliases == ("refund.assess_eligibility:order",)
    assert records.legacy_effect_aliases == (
        "refund.list_eligibilities:refund_eligibility",
    )

    capabilities = _capabilities()
    evaluate = capabilities["evaluate_refund_eligibility"]
    list_records = capabilities["list_active_eligibilities"]
    assert "可以退款吗" in evaluate.discovery_examples
    assert "可以退款吗" in list_records.exclusion_examples
    assert evaluate.completion_effects == ("refund.assess_eligibility:order",)
    assert list_records.completion_effects == (
        "refund.list_eligibilities:refund_eligibility",
    )


def test_exact_effect_surface_cannot_swap_refund_assessment_and_record_collection(monkeypatch) -> None:
    module_registry = ModuleRegistry((EcommerceModule(),))
    aliases = module_registry.legacy_semantic_output_aliases()
    monkeypatch.setattr(capability_effects, "_legacy_semantic_aliases", lambda: aliases)

    capabilities = _capabilities()
    assessment_effects = set(
        capability_effects.completion_effects_for_contract(
            capabilities["evaluate_refund_eligibility"].contract
        )
    )
    record_effects = set(
        capability_effects.completion_effects_for_contract(
            capabilities["list_active_eligibilities"].contract
        )
    )

    assert "semantic-output:refund.eligibility" in assessment_effects
    assert "semantic-output:refund.eligibility.collection" not in assessment_effects
    assert "semantic-output:refund.eligibility.collection" in record_effects
    assert "semantic-output:refund.eligibility" not in record_effects

    snapshot = module_registry.semantic_vocabulary_snapshot()
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert snapshot["availability_exposed"] is False
    assert snapshot["tool_names_exposed"] is False
    assert "evaluate_refund_eligibility" not in serialized
    assert "list_active_eligibilities" not in serialized
