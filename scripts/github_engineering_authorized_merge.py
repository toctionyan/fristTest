#!/usr/bin/env python3
from __future__ import annotations

"""Trusted CLI for creating and consuming bounded EngineeringMergeGrant evidence."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from engineering_autonomy_handoff import validate_handoff_bundle  # type: ignore  # noqa: E402
from engineering_merge_grant import (  # type: ignore  # noqa: E402
    EngineeringMergeGrantError,
    compile_merge_network_request,
    create_merge_grant,
    evaluate_merge_gate,
    validate_merge_grant_for_task,
)
from autonomy_grant import task_binding_fingerprint  # type: ignore  # noqa: E402

LOCATOR_MARKER = "<!-- engineering-merge-grant-ref@1 -->"


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EngineeringMergeGrantError(f"JSON object required: {path}")
    return payload


def _write(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _output(path: str | None, **values: object) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _list(path: str | Path) -> list[Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("workflow_runs", "files", "reviews", "nodes"):
            if isinstance(value.get(key), list):
                return list(value[key])
    raise EngineeringMergeGrantError(f"JSON list required: {path}")


def cmd_authorize(args: argparse.Namespace) -> int:
    bundle = validate_handoff_bundle(_load(args.bundle))
    task = bundle["task"]
    grant = create_merge_grant(
        task=task,
        repository=args.repository,
        source_pr_number=int(bundle["source_pr_number"]),
        issued_by=args.actor,
        owner_authorization_ref=args.owner_authorization_ref,
    )
    _write(args.output, grant)
    _output(
        args.github_output,
        grant_id=grant["grant_id"],
        grant_sha256=grant["grant_sha256"],
        task_binding_fingerprint=grant["task_binding_fingerprint"],
        artifact_name=f"engineering-merge-grant-{grant['task_binding_fingerprint']}",
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    task = _load(args.task)
    grant = validate_merge_grant_for_task(_load(args.grant), task=task)
    _output(
        args.github_output,
        grant_id=grant["grant_id"],
        grant_sha256=grant["grant_sha256"],
        task_binding_fingerprint=grant["task_binding_fingerprint"],
    )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    threads = _load(args.review_threads) if args.review_threads else {"nodes": []}
    nodes = threads.get("nodes") if isinstance(threads.get("nodes"), list) else []
    unresolved = sum(1 for row in nodes if isinstance(row, dict) and row.get("isResolved") is False)
    human_gates = _list(args.human_gates) if args.human_gates else []
    reasons = [str(item.get("reason") if isinstance(item, dict) else item) for item in human_gates]
    decision = evaluate_merge_gate(
        _load(args.grant),
        task=_load(args.task),
        pr=_load(args.pr),
        lineage_result=_load(args.lineage),
        exact_head_result=_load(args.exact_head_result),
        exact_head_ci_state=_load(args.exact_head_ci_state),
        workflow_runs=_list(args.workflow_runs),
        changed_paths=[str(row.get("filename") if isinstance(row, dict) else row) for row in _list(args.files)],
        reviews=_list(args.reviews),
        unresolved_review_threads=unresolved,
        human_gate_reasons=reasons,
    )
    _write(args.output, decision)
    _output(
        args.github_output,
        status=decision["status"],
        mark_ready_allowed=str(decision["mark_ready_allowed"]).lower(),
        merge_allowed=str(decision["merge_allowed"]).lower(),
        expected_head_sha=decision.get("expected_head_sha") or "",
        expected_base_sha=decision.get("expected_base_sha") or "",
        pr_number=decision.get("pr_number") or "",
    )
    return 0


def cmd_request(args: argparse.Namespace) -> int:
    request = compile_merge_network_request(_load(args.grant), _load(args.decision), current_pr=_load(args.pr))
    _write(args.output, request)
    _output(args.github_output, request_sha256=request["request_sha256"], path=request["path"])
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    value = task_binding_fingerprint(_load(args.task))
    print(value)
    _output(args.github_output, task_binding_fingerprint=value)
    return 0


def cmd_locator(args: argparse.Namespace) -> int:
    text = Path(args.body).read_text(encoding="utf-8") if Path(args.body).is_file() else args.body
    if LOCATOR_MARKER not in text:
        return 1
    run = re.search(r"authorize_run:\s*([0-9]+)\/([0-9]+)", text)
    digest = re.search(r"grant_sha256:\s*([0-9a-f]{64})", text)
    task_fp = re.search(r"task_binding_fingerprint:\s*([0-9a-f]{64})", text)
    if not run or not digest or not task_fp:
        raise EngineeringMergeGrantError("merge-grant locator is malformed")
    payload = {
        "schema": "engineering-merge-grant-locator@1",
        "authorize_run_id": int(run.group(1)),
        "authorize_run_attempt": int(run.group(2)),
        "grant_sha256": digest.group(1),
        "task_binding_fingerprint": task_fp.group(1),
    }
    _write(args.output, payload)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    p = sub.add_parser("authorize")
    p.add_argument("--bundle", required=True); p.add_argument("--repository", required=True)
    p.add_argument("--actor", required=True); p.add_argument("--owner-authorization-ref", required=True)
    p.add_argument("--output", required=True); p.add_argument("--github-output")
    p.set_defaults(func=cmd_authorize)

    p = sub.add_parser("verify-task-grant")
    p.add_argument("--task", required=True); p.add_argument("--grant", required=True); p.add_argument("--github-output")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("gate")
    for name in ("grant", "task", "pr", "lineage", "exact-head-result", "exact-head-ci-state", "workflow-runs", "files", "reviews"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--review-threads"); p.add_argument("--human-gates")
    p.add_argument("--output", required=True); p.add_argument("--github-output")
    p.set_defaults(func=cmd_gate)

    p = sub.add_parser("request")
    p.add_argument("--grant", required=True); p.add_argument("--decision", required=True); p.add_argument("--pr", required=True)
    p.add_argument("--output", required=True); p.add_argument("--github-output")
    p.set_defaults(func=cmd_request)

    p = sub.add_parser("fingerprint")
    p.add_argument("--task", required=True); p.add_argument("--github-output")
    p.set_defaults(func=cmd_fingerprint)

    p = sub.add_parser("locator")
    p.add_argument("--body", required=True); p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_locator)
    return root


def main() -> int:
    try:
        return int(parser().parse_args().func(parser().parse_args()))
    except (OSError, json.JSONDecodeError, ValueError, EngineeringMergeGrantError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc), "merge_allowed": False, "deploy_allowed": False, "production_closed": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
