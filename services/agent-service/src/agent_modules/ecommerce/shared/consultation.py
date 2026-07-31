"""Policy consultation projection for fixed ecommerce capability."""
from __future__ import annotations

from typing import Any

from agent_core.config import retrieval_min_score, retrieval_top_k
from agent_core.ledger import scope_for_state
from agent_core.rag.access import normalize_scope, scope_filter
from agent_core.rag.retriever import retrieve

from .context import (
    _fresh_order_rows_for_target,
    _ok,
    _require_current_or_resumed_span,
    _require_span,
    _result_payload,
    _target_members,
)
from agent_core.ledger import result_entry

def _retrieve_with_scope(query: str, *, top_k: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Invoke scoped retrieval with an explicit local test double fallback.

    Production providers accept ``filters``.  The fallback exists only for
    injected two-argument test doubles and is not reachable from the serving
    retrieval stack.
    """
    try:
        return list(retrieve(query, top_k=top_k, filters=filters) or [])
    except TypeError:
        return list(retrieve(query, top_k=top_k) or [])


def _consultation_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or "").strip()
        title = str(row.get("title") or "知识库资料").strip()
        source = str(row.get("source") or "").strip()
        if not content:
            continue
        sources.append({
            "doc_id": str(row.get("doc_id") or ""),
            "chunk_id": str(row.get("chunk_id") or ""),
            "title": title,
            "source": source,
            "content": content,
            "score": float(row.get("score") or 0),
        })
    return sources


_POLICY_TOPICS: dict[str, dict[str, str]] = {
    "invoice": {"query": "发票 开票政策 开票条件", "label": "发票政策"},
    "refund": {"query": "退款 退货政策 退款规则", "label": "退款政策"},
    "after_sales": {"query": "售后 质量问题 售后政策", "label": "售后政策"},
    "warranty": {"query": "保修 厂家保修 保修政策", "label": "保修政策"},
}


def _row_policy_domain(row: dict[str, Any]) -> str:
    """Read a declared policy domain; recognize only legacy builtin seed ids.

    Uploaded knowledge without a declared ``policy_domain`` is deliberately not
    released by a domain-scoped consultation.  This fail-closed rule prevents a
    high-scoring refund chunk from leaking into an invoice-only answer.
    """
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    declared = str(metadata.get("policy_domain") or "").strip()
    if declared:
        return declared
    doc_id = str(row.get("doc_id") or "")
    if doc_id.startswith("policy_invoice_"):
        return "invoice"
    if doc_id.startswith("policy_refund_"):
        return "refund"
    if doc_id.startswith("policy_after_sales_"):
        return "after_sales"
    if doc_id.startswith("policy_warranty_"):
        return "warranty"
    return ""


def execute_consult_order_policy(
    state: dict[str, Any],
    args: dict[str, Any],
    *,
    policy_domain: str,
) -> dict[str, Any]:
    """Return one evidence-bound, domain-scoped order consultation.

    The selected public capability fixes ``policy_domain`` before the model is
    called.  The model can identify evidence and a target, but it cannot widen
    an invoice answer into refund/after-sales/logistics.  Retrieval and release
    both enforce the same registered domain.
    """
    topic = _POLICY_TOPICS.get(str(policy_domain or ""))
    if topic is None:
        return {
            "ok": False,
            "code": "POLICY_DOMAIN_NOT_REGISTERED",
            "message": "当前政策咨询能力没有注册明确的知识域。",
            "data": {"policy_domain": str(policy_domain or "")},
        }
    evidence = _require_span(state, str(args.get("reference_span") or ""), field="当前引用")
    if evidence:
        return evidence
    evidence = _require_current_or_resumed_span(
        state, str(args.get("question_span") or ""), field="咨询问题",
    )
    if evidence:
        return evidence
    issue_span = str(args.get("issue_span") or "").strip()
    if issue_span:
        evidence = _require_current_or_resumed_span(state, issue_span, field="咨询主题")
        if evidence:
            return evidence
    question_span = str(args.get("question_span") or "").strip()
    target_expression = args.get("target") if isinstance(args.get("target"), dict) else {}
    generic_policy = (
        str(target_expression.get("mode") or "") == "all_orders"
        and not str(target_expression.get("attribute_span") or "").strip()
        and not str(target_expression.get("status") or "").strip()
    )
    if generic_policy:
        target = {
            "mode": "all_orders",
            "target": dict(target_expression),
            "member_handles": [],
            "entries": [],
        }
        rows: list[dict[str, Any]] = []
        additions: list[dict[str, Any]] = []
        stable_handles: list[str] = []
        order = {
            "order_id": f"policy:{policy_domain}",
            "product_name": f"通用{topic['label']}",
            "status": "",
        }
    else:
        target, target_error = _target_members(
            state,
            target_expression,
            expected_shape="one",
            allowed_resource_types={"order"},
        )
        if target_error:
            return target_error
        assert target is not None
        rows, additions, row_error, stable_handles = _fresh_order_rows_for_target(
            state, target, source="contextual:order_issue_consultation"
        )
        if row_error:
            return row_error
        assert rows is not None and len(rows) == 1 and len(stable_handles) == 1
        order = rows[0]
    # This query only combines already verified facts with the user's cited
    # consultation text.  It does not turn a consultation into a business
    # decision or an application.
    product_name = str(order.get("product_name") or "")
    order_status = str(order.get("status") or "")
    # Query variants retain the user's verified text but may add only the fixed
    # domain owned by the selected capability.  Generic "订单政策" expansion is
    # intentionally forbidden because it broadens a single-intent answer.
    rag_queries = [
        " ".join(part for part in (product_name, issue_span, question_span, topic["query"]) if part),
        " ".join(part for part in (issue_span, question_span, topic["query"]) if part),
        " ".join(part for part in (product_name, order_status, topic["query"]) if part),
    ]
    knowledge_status = "available"
    by_chunk: dict[str, dict[str, Any]] = {}
    try:
        for rag_query in dict.fromkeys(query for query in rag_queries if query.strip()):
            rag_scope = scope_filter(normalize_scope(
                tenant_id=str(state.get("current_tenant_id") or "default"),
                user_id=str(state.get("current_user_id") or ""),
            ))
            rag_scope["policy_domain"] = policy_domain
            for row in _retrieve_with_scope(rag_query, top_k=retrieval_top_k(), filters=rag_scope):
                if not isinstance(row, dict):
                    continue
                candidate = dict(row)
                if _row_policy_domain(candidate) != policy_domain:
                    continue
                key = str(candidate.get("chunk_id") or candidate.get("doc_id") or "")
                current = by_chunk.get(key)
                if current is None or float(candidate.get("score") or 0) > float(current.get("score") or 0):
                    by_chunk[key] = candidate
    except Exception as exc:
        knowledge_status = f"unavailable:{exc.__class__.__name__}"
    raw_docs = sorted(by_chunk.values(), key=lambda row: float(row.get("score") or 0), reverse=True)
    min_score = retrieval_min_score()
    evidence_docs = [row for row in raw_docs if float(row.get("score") or 0) >= min_score and str(row.get("content") or "").strip()]
    sources = _consultation_sources(evidence_docs)
    scope = scope_for_state(state)
    label = f"{order.get('product_name') or '订单'}{topic['label']}咨询"
    if generic_policy:
        result = result_entry(
            capability="orders.general_policy_consultation",
            member_handles=[],
            labels=[],
            scope=scope,
            turn=int(state.get("turn_index") or 0),
            source_target={"mode": "all_orders", "target": dict(target_expression), "policy_domain": policy_domain},
        )
        all_entries = [result]
    else:
        result, all_entries = _result_payload(
            "orders.issue_consultation",
            target_info=target,
            handles=stable_handles,
            labels=[label],
            scope=scope,
            state=state,
            additions=additions,
        )
    return _ok(
        {
            "capability": "orders.issue_consultation",
            "result_handle": result["handle"],
            "order": order,
            "issue": issue_span,
            "question": question_span,
            "policy_domain": policy_domain,
            "knowledge_status": knowledge_status,
            "knowledge_available": bool(sources),
            "policy_evidence": sources,
            "consultation_only": True,
            "policy_scope": "general" if generic_policy else "order",
        },
        entries=all_entries,
        sources=sources,
    )


def execute_consult_invoice_policy(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return execute_consult_order_policy(state, args, policy_domain="invoice")


def execute_consult_refund_policy(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return execute_consult_order_policy(state, args, policy_domain="refund")


def execute_consult_after_sales_policy(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return execute_consult_order_policy(state, args, policy_domain="after_sales")


def execute_consult_warranty_policy(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    return execute_consult_order_policy(state, args, policy_domain="warranty")
