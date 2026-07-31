#!/usr/bin/env python3
"""Enrich a real browser conversation campaign with runtime trace evidence.

The browser artifact remains the public truth.  This analyzer joins every
rendered turn to its graph snapshot and transaction rows so failures can be
assigned to the earliest broken boundary rather than blamed on the model by
default.  It writes only customer-safe orchestration evidence; secrets and raw
model prompts are intentionally excluded.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "services/agent-service/runtime/sqlite/app.db"
GENERIC_FAILURES = (
    "系统未获得可继续办理的明确结果",
    "系统未能证明当前结果完整满足你的查询条件",
    "未获得可继续办理的明确结果",
    "未确认创建或提交任何业务申请",
    "请刷新后查看事务中心",
    "请重新说明需要处理的事项",
    "结果暂时无法完整展示",
    "系统需要基于已核验结果重新整理回答",
)
ACTION_TOOLS = {
    "prepare_refund", "prepare_refund_from_eligibility", "prepare_invoice",
    "prepare_after_sales_request", "prepare_cancel_order",
}
SUPPORTED_TOOL_NAMES = {
    "list_orders", "get_order_details", "get_order_logistics",
    "consult_refund_policy", "evaluate_refund_eligibility", "list_refunds",
    "consult_invoice_policy", "list_invoices", "consult_after_sales_policy",
    "list_after_sales_requests", "consult_warranty_policy",
    "prepare_refund", "prepare_refund_from_eligibility", "prepare_invoice",
    "prepare_after_sales_request", "prepare_cancel_order",
    "list_active_eligibilities", "list_active_offers", "dismiss_eligibility",
    "dismiss_offer", "query_transaction_lifecycle",
}
ORDER_RE = re.compile(r"1000[1-4]")


def _state(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
    return state if isinstance(state, dict) else {}


def _turn_pass(text: str, expected: dict[str, Any]) -> bool:
    if not str(text or "").strip():
        return False
    if any(marker not in text for marker in expected.get("requiredAll") or []):
        return False
    required_any = list(expected.get("requiredAny") or [])
    if required_any and not any(marker in text for marker in required_any):
        return False
    if any(marker in text for marker in expected.get("forbidden") or []):
        return False
    if not expected.get("allowControlledFailure") and any(marker in text for marker in GENERIC_FAILURES):
        return False
    return True


def _safe_tool(row: dict[str, Any]) -> dict[str, Any]:
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    runtime_outcome = data.get("runtime_outcome") if isinstance(data.get("runtime_outcome"), dict) else {}
    payload = runtime_outcome.get("payload") if isinstance(runtime_outcome.get("payload"), dict) else {}
    target = args.get("target") if isinstance(args.get("target"), dict) else None
    return {
        "name": str(row.get("name") or ""),
        "classification": str(row.get("classification") or ""),
        "args": {
            key: args.get(key)
            for key in (
                "goal_ids", "target", "reference_span", "status_span", "question_span",
                "query_span", "clarification_resolution", "service_type", "reason",
            )
            if key in args
        },
        "ok": result.get("ok"),
        "code": result.get("code"),
        "count": data.get("count", payload.get("count")),
        "target_mode": target.get("mode") if target else None,
        "customer_safe_summary": str(runtime_outcome.get("customer_safe_summary") or "")[:300],
        "execution_kind": ((result.get("match_proof") or {}).get("execution_kind")
                           if isinstance(result.get("match_proof"), dict) else None),
        "permit_reason": ((result.get("match_proof") or {}).get("reason_code")
                          if isinstance(result.get("match_proof"), dict) else None),
    }


def _declared_goal(tool_trace: list[dict[str, Any]]) -> dict[str, Any]:
    for row in tool_trace:
        if str(row.get("name") or "") == "declare_turn_goals":
            args = row.get("args") if isinstance(row.get("args"), dict) else {}
            return {
                "summary": args.get("summary"),
                "goals": args.get("goals"),
                "clarification_resolution": args.get("clarification_resolution"),
            }
    return {}


def _orders(value: Any) -> list[str]:
    return sorted(set(ORDER_RE.findall(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)))


def _classify(
    *,
    turn: dict[str, Any],
    state: dict[str, Any],
    tools: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    if turn.get("pass"):
        return "pass", []
    response = str(turn.get("response") or "")
    expected = turn.get("expected") if isinstance(turn.get("expected"), dict) else {}
    candidate = str(state.get("current_final_answer") or "")
    candidate_pass = _turn_pass(candidate, expected)
    required_orders = set(_orders(expected.get("requiredAll") or []))
    response_orders = set(_orders(response))
    candidate_orders = set(_orders(candidate))
    tool_orders = set(_orders(tools))
    wrong_orders = (response_orders | candidate_orders | tool_orders) - required_orders
    visible_generic = any(marker in response for marker in GENERIC_FAILURES)
    release = state.get("answer_release_alignment") if isinstance(state.get("answer_release_alignment"), dict) else {}
    violations = list(state.get("presentation_contract_violations") or [])
    tool_names = {str(row.get("name") or "") for row in tools}
    zero_result = any(row.get("count") == 0 for row in tools)
    has_action = bool(tool_names & ACTION_TOOLS) or bool(transactions)
    flags: list[str] = []

    if candidate_pass and not turn.get("pass"):
        flags.append("backend_candidate_satisfies_browser_oracle")
    if str(release.get("decision") or "").lower() == "reject":
        flags.append("answer_release_rejected")
    if violations:
        flags.append("presentation_contract_violation")
    if zero_result:
        flags.append("verified_empty_result")
    if visible_generic:
        flags.append("generic_customer_fallback")
    if required_orders and wrong_orders:
        flags.append("wrong_target_present:" + ",".join(sorted(wrong_orders)))
    if state.get("pending_clarification"):
        flags.append("clarification_state_active")
    if "report_unsupported_request" in tool_names or "没有查询" in candidate and "能力" in candidate:
        flags.append("unsupported_selected")
    if has_action:
        flags.append("transaction_or_action_path")

    if has_action and required_orders and wrong_orders:
        return "stale_transaction_target", flags
    if required_orders and wrong_orders:
        return "stale_or_wrong_target", flags
    if candidate_pass and zero_result:
        return "empty_result_false_reject", flags
    if candidate_pass and (str(release.get("decision") or "").lower() == "reject" or violations):
        return "answer_release_false_reject", flags
    if candidate_pass and visible_generic:
        return "presentation_false_reject", flags
    if "report_unsupported_request" in tool_names and not expected.get("allowControlledFailure"):
        return "capability_discovery_miss", flags
    if not tool_names.intersection(SUPPORTED_TOOL_NAMES) and not expected.get("allowControlledFailure"):
        return "capability_or_goal_planning_miss", flags
    if state.get("pending_clarification") and len(str(turn.get("prompt") or "")) <= 16:
        return "continuation_state_failure", flags
    if visible_generic:
        return "runtime_evidence_or_release_failure", flags
    return "semantic_or_oracle_gap", flags


def _transaction_rows(connection: sqlite3.Connection, thread_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT draft_id,draft_revision,draft_state,action_id,projection_json,created_at,updated_at "
        "FROM transaction_drafts WHERE thread_id=? ORDER BY created_at,draft_revision",
        (thread_id,),
    ).fetchall()
    result = []
    for row in rows:
        try:
            projection = json.loads(row[4] or "{}")
        except json.JSONDecodeError:
            projection = {}
        result.append({
            "draft_id": row[0],
            "revision": row[1],
            "state": row[2],
            "action_id": row[3],
            "projection": projection,
            "created_at": row[5],
            "updated_at": row[6],
        })
    return result


def analyze(campaign_path: Path, database: Path) -> dict[str, Any]:
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    enriched: list[dict[str, Any]] = []
    primary_counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    tag_failures: defaultdict[str, Counter[str]] = defaultdict(Counter)

    with sqlite3.connect(database) as connection:
        for scenario in list(campaign.get("scenarios") or []):
            thread_id = str(scenario.get("threadId") or "")
            snapshots = [
                {"trace_id": trace_id, "state": _state(raw), "created_at": created_at}
                for trace_id, raw, created_at in connection.execute(
                    "SELECT id,output_json,created_at FROM trace_logs "
                    "WHERE thread_id=? AND event_type='graph_snapshot' ORDER BY id",
                    (thread_id,),
                ).fetchall()
            ]
            transactions = _transaction_rows(connection, thread_id)
            consumed: set[int] = set()
            turns = []
            for public_turn in list(scenario.get("turns") or []):
                prompt = str(public_turn.get("prompt") or "")
                match_index = next((
                    index for index, snapshot in enumerate(snapshots)
                    if index not in consumed
                    and str(snapshot["state"].get("current_user_input") or "") == prompt
                ), None)
                if match_index is None:
                    state: dict[str, Any] = {}
                    trace_id = None
                    created_at = None
                else:
                    consumed.add(match_index)
                    snapshot = snapshots[match_index]
                    state = snapshot["state"]
                    trace_id = snapshot["trace_id"]
                    created_at = snapshot["created_at"]
                raw_trace = [row for row in list(state.get("tool_trace") or []) if isinstance(row, dict)]
                tools = [_safe_tool(row) for row in raw_trace]
                primary, flags = _classify(
                    turn=public_turn,
                    state=state,
                    tools=tools,
                    transactions=transactions,
                )
                primary_counts[primary] += 1
                for flag in flags:
                    flag_counts[flag.split(":", 1)[0]] += 1
                if primary != "pass":
                    for tag in list(scenario.get("tags") or []):
                        tag_failures[str(tag)][primary] += 1
                release = state.get("answer_release_alignment") if isinstance(state.get("answer_release_alignment"), dict) else {}
                turns.append({
                    **public_turn,
                    "primary_failure_class": primary,
                    "contributing_flags": flags,
                    "trace": {
                        "trace_id": trace_id,
                        "created_at": created_at,
                        "status": state.get("status"),
                        "backend_candidate_answer": state.get("current_final_answer"),
                        "declared_goal": _declared_goal(raw_trace),
                        "tools": tools,
                        "pending_clarification": state.get("pending_clarification"),
                        "answer_release_alignment": {
                            "decision": release.get("decision"),
                            "reason_code": str(release.get("reason_code") or "")[:1600],
                            "source": release.get("source"),
                            "independent": release.get("independent"),
                        } if release else None,
                        "presentation_contract_violations": state.get("presentation_contract_violations"),
                        "capability_surface": state.get("capability_surface"),
                    },
                })
            enriched.append({
                "id": scenario.get("id"),
                "tags": scenario.get("tags"),
                "thread_id": thread_id,
                "reload_equivalent": scenario.get("reloadEquivalent"),
                "transactions": transactions,
                "turns": turns,
            })

    return {
        "schema_version": 1,
        "source_campaign": str(campaign_path),
        "database": str(database),
        "campaign_summary": campaign.get("summary"),
        "failure_summary": {
            "primary_counts": dict(primary_counts.most_common()),
            "contributing_flag_counts": dict(flag_counts.most_common()),
            "failures_by_tag": {
                tag: dict(counts.most_common()) for tag, counts in sorted(tag_failures.items())
            },
        },
        "scenarios": enriched,
    }


def markdown_report(analysis: dict[str, Any]) -> str:
    campaign = analysis.get("campaign_summary") or {}
    failures = analysis["failure_summary"]
    lines = [
        "# Strong-context browser campaign analysis",
        "",
        "## Baseline",
        "",
        f"- Scenarios: {campaign.get('completedScenarios')} / 20",
        f"- Turns: {campaign.get('totalTurns')} / 200",
        f"- Passed turns: {campaign.get('passedTurns')}",
        f"- Failed turns: {campaign.get('failedTurns')}",
        f"- Turn pass rate: {campaign.get('turnPassRate')}",
        f"- Reload failures: {len(campaign.get('reloadFailures') or [])}",
        "",
        "## Earliest failure boundary",
        "",
        "| Failure class | Turns |",
        "|---|---:|",
    ]
    for name, count in failures["primary_counts"].items():
        lines.append(f"| `{name}` | {count} |")
    lines.extend(["", "## Contributing signals", "", "| Signal | Turns |", "|---|---:|"])
    for name, count in failures["contributing_flag_counts"].items():
        lines.append(f"| `{name}` | {count} |")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- `*_false_reject` means the backend candidate already satisfied the browser oracle, but Answer Release or presentation governance replaced it with a generic failure.",
        "- `stale_*_target` means an older ResultRef, eligibility or transaction target overrode an explicit current-turn target.",
        "- `capability_*_miss` means no supported runtime capability reached execution despite a supported user request.",
        "- `continuation_state_failure` means a short reply or suspended interaction was not resumed from the correct scope.",
        "- `semantic_or_oracle_gap` requires manual review because the visible answer may be safe but incomplete, or the campaign oracle may be stricter than product requirements.",
        "",
        "The enriched JSON contains every public prompt/response, thread id, goal declaration, safe tool trace, clarification state, release decision, presentation violation and transaction projection.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    report = Path(args.report).resolve()
    analysis = analyze(Path(args.campaign).resolve(), Path(args.database).resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report.write_text(markdown_report(analysis), encoding="utf-8")
    print(json.dumps(analysis["failure_summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

