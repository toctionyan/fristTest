from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from task_run import TaskRunStore, evaluate_completion


def load_task_run(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Reuse the production validator without inventing a second schema path.
    TaskRunStore(path, payload)
    return payload


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    decision = evaluate_completion(payload)
    checkpoints = payload.get("checkpoints") or []
    attempts = payload.get("action_attempts") or []
    blockers = payload.get("blockers") or []
    return {
        "schema_version": 1,
        "task_id": payload.get("task_id"),
        "task_kind": payload.get("task_kind"),
        "status": payload.get("status"),
        "phase": payload.get("phase"),
        "revision": payload.get("revision"),
        "completion_eligible": decision.eligible,
        "missing_conditions": list(decision.missing_conditions),
        "invalid_conditions": list(decision.invalid_conditions),
        "checkpoint_count": len(checkpoints),
        "last_checkpoint": checkpoints[-1] if checkpoints else None,
        "action_attempt_count": len(attempts),
        "last_action_attempt": attempts[-1] if attempts else None,
        "blocker_count": len(blockers),
        "last_blocker": blockers[-1] if blockers else None,
        "next_action": (
            str(blockers[-1].get("next_action") or "")
            if blockers
            else str((payload.get("metadata") or {}).get("next_action") or "")
        ),
        "updated_at": payload.get("updated_at"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect durable repair task-run checkpoints.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "guard"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--file", required=True)
    args = parser.parse_args()

    path = Path(args.file).resolve()
    payload = load_task_run(path)
    report = summarize(payload)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.command == "guard":
        return 0 if payload.get("status") == "COMPLETED" and report["completion_eligible"] else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
