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
