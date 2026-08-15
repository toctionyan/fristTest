from agent_modules.ecommerce.semantic_vocabulary import SEMANTIC_OUTPUTS


def _semantic_output_index():
    return {row.output_id: row for row in SEMANTIC_OUTPUTS}


def test_refund_application_history_is_distinct_from_eligibility_records():
    index = _semantic_output_index()
    refund_status = index["refund.status"]
    eligibility_records = index["refund.eligibility.collection"]

    assert refund_status.subject_type == "refund"
    assert eligibility_records.subject_type == "refund_eligibility"

    # The model-facing domain vocabulary must make the two kinds of records
    # unambiguous without exposing tools, capabilities, or availability.
    assert "退款申请记录" in refund_status.description
    assert "退款历史" in refund_status.description
    assert "资格核验结论记录" in refund_status.description

    assert "资格核验结论记录集合" in eligibility_records.description
    assert "不是退款申请记录或退款历史" in eligibility_records.description


def test_refund_record_distinction_remains_capability_independent():
    index = _semantic_output_index()
    descriptions = "\n".join(
        index[output_id].description
        for output_id in ("refund.status", "refund.eligibility.collection")
    )

    for forbidden in (
        "list_refunds",
        "list_active_eligibilities",
        "ecommerce.refunds.list",
        "runtime.eligibility.list",
        "可用能力",
    ):
        assert forbidden not in descriptions



def test_refund_status_exposes_capability_independent_result_boundary():
    refund_status = _semantic_output_index()["refund.status"]
    assert refund_status.included_result_meanings == (
        "已存在退款申请或退款历史记录本身，以及这些记录当前处于什么处理状态",
    )
    assert refund_status.excluded_result_meanings == (
        "退款资金预计或承诺何时完成结算、入账或到账的时间结果",
    )

    public = refund_status.public_snapshot()
    assert public["included_result_meanings"] == list(refund_status.included_result_meanings)
    assert public["excluded_result_meanings"] == list(refund_status.excluded_result_meanings)

    boundary_text = "\n".join(
        refund_status.included_result_meanings + refund_status.excluded_result_meanings
    )
    for forbidden in (
        "list_refunds",
        "refund.query_status:refund",
        "ecommerce.refunds.list",
        "可用能力",
    ):
        assert forbidden not in boundary_text


def test_alignment_projection_preserves_semantic_boundaries_without_capability_metadata(monkeypatch):
    from agent_core.lifecycle.goal_planning import _semantic_vocabulary_for_alignment
    import agent_core.modules.registry as registry_module

    class _Registry:
        @staticmethod
        def semantic_vocabulary_snapshot():
            return {
                "version": "semantic-output-vocabulary@2",
                "outputs": [
                    {
                        "output_id": "domain.sample",
                        "subject_type": "sample",
                        "effect_kinds": ["read"],
                        "description": "sample meaning",
                        "included_result_meanings": ["included dimension"],
                        "excluded_result_meanings": ["excluded dimension"],
                        "legacy_effect_aliases": ["must.not.leak"],
                        "tool_name": "must_not_leak",
                        "available": True,
                    }
                ],
            }

    monkeypatch.setattr(registry_module, "current_module_registry", lambda: _Registry())
    projected = _semantic_vocabulary_for_alignment()
    assert projected["version"] == "semantic-output-vocabulary@2"
    assert projected["availability_exposed"] is False
    assert projected["tool_names_exposed"] is False
    assert projected["outputs"] == [
        {
            "output_id": "domain.sample",
            "subject_type": "sample",
            "effect_kinds": ["read"],
            "description": "sample meaning",
            "included_result_meanings": ["included dimension"],
            "excluded_result_meanings": ["excluded dimension"],
        }
    ]


def test_semantic_output_rejects_contradictory_boundary_meaning():
    import pytest
    from agent_core.modules.contracts import SemanticOutputDefinition

    with pytest.raises(ValueError, match="cannot include and exclude the same result meaning"):
        SemanticOutputDefinition(
            output_id="sample.result",
            subject_type="sample",
            effect_kinds=("read",),
            description="sample",
            included_result_meanings=("same result dimension",),
            excluded_result_meanings=("Same Result Dimension",),
        )
