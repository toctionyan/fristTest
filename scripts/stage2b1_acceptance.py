#!/usr/bin/env python3
"""Explicit command for recording the Stage2B1 acceptance TaskRun condition.

The command is deliberately an adapter, not a discovery workflow.  Every
decision, binding, contract, gate, and human decision is supplied by the
caller.  The existing stage acceptance writer remains the only mutation
boundary, and the TaskRun remains the only completion authority.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from stage_acceptance_writer import (  # type: ignore  # noqa: E402
    StageAcceptanceWriteError,
    write_stage_acceptance,
)
from task_run import TaskRunError, TaskRunStore  # type: ignore  # noqa: E402


class Stage2B1AcceptanceCommandError(ValueError):
    """Raised when an explicit command input cannot be loaded safely."""


def _load_object(path: Path, *, field: str) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink():
        raise Stage2B1AcceptanceCommandError(f"{field} is missing or unsafe")
    resolved = path.resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise Stage2B1AcceptanceCommandError(f"{field} is missing or unsafe")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Stage2B1AcceptanceCommandError(f"{field} is unreadable") from exc
    if not isinstance(payload, dict):
        raise Stage2B1AcceptanceCommandError(f"{field} must be an object")
    return payload


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Stage2B1AcceptanceCommandError(f"{field} must be a non-empty string")
    return value.strip()


def record_stage_acceptance(
    *,
    workspace: Path,
    task_run_path: Path,
    decision_path: Path,
    expected_binding_path: Path,
    change_contract_path: Path,
    change_contract_digest: str,
    human_gate_path: Path,
    human_decision_path: Path,
    expected_human_outcome: str = "ACCEPT_STAGE2B1",
) -> dict[str, Any]:
    """Load explicit inputs and delegate to the existing write boundary.

    No path is discovered, no latest artifact is selected, and no governance
    document is opened for writing here.  The writer validates every input
    before it mutates the supplied TaskRun.
    """

    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise Stage2B1AcceptanceCommandError("workspace is missing")

    task_payload = _load_object(task_run_path, field="task_run")
    task = TaskRunStore(task_run_path.resolve(), task_payload)
    decision = _load_object(decision_path, field="decision")
    expected_binding = _load_object(expected_binding_path, field="expected_binding")
    change_contract = _load_object(change_contract_path, field="change_contract")

    result = write_stage_acceptance(
        task,
        decision,
        expected_binding=expected_binding,
        change_contract=change_contract,
        change_contract_digest=_required_text(
            change_contract_digest,
            field="change_contract_digest",
        ),
        workspace=workspace,
        human_gate_path=human_gate_path,
        human_decision_path=human_decision_path,
        expected_human_outcome=_required_text(
            expected_human_outcome,
            field="expected_human_outcome",
        ),
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record explicit Stage2B1 acceptance evidence in TaskRun."
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--task-run", required=True, type=Path)
    parser.add_argument("--decision", required=True, type=Path)
    parser.add_argument("--expected-binding", required=True, type=Path)
    parser.add_argument("--change-contract", required=True, type=Path)
    parser.add_argument("--change-contract-digest", required=True)
    parser.add_argument("--human-gate", required=True, type=Path)
    parser.add_argument("--human-decision", required=True, type=Path)
    parser.add_argument(
        "--expected-human-outcome",
        default="ACCEPT_STAGE2B1",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = record_stage_acceptance(
            workspace=args.workspace,
            task_run_path=args.task_run,
            decision_path=args.decision,
            expected_binding_path=args.expected_binding,
            change_contract_path=args.change_contract,
            change_contract_digest=args.change_contract_digest,
            human_gate_path=args.human_gate,
            human_decision_path=args.human_decision,
            expected_human_outcome=args.expected_human_outcome,
        )
    except (
        OSError,
        Stage2B1AcceptanceCommandError,
        StageAcceptanceWriteError,
        TaskRunError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema": "stage-acceptance-command@1",
                    "status": "BLOCKED",
                    "error": str(exc),
                    "active_change_written": False,
                    "governance_state_changed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
