from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .candidate_freeze import freeze_candidate, validate_candidate_freeze
    from .contract import load_contract
except ImportError:
    from candidate_freeze import freeze_candidate, validate_candidate_freeze  # type: ignore
    from contract import load_contract  # type: ignore


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze and validate a Codex multi-agent candidate")
    sub = parser.add_subpar(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--candidate-commit")
    sub.add_parser("validate")
    sub.add_parser("status")
    args = parser.parse_args()

    workspace = Path.cwd().resolve()
    contract = load_contract(workspace, require_approved=False).payload
    try:
        if args.command == "freeze":
            path = freeze_candidate(
                workspace,
                contract,
                candidate_commit=args.candidate_commit,
            )
            result = {
                "status": "PASS",
                "candidate_freeze": path.relative_to(workspace).as_posix(),
            }
        else:
            payload = validate_candidate_freeze(workspace, contract)
            result = {
                "status": "PASS",
                "candidate_commit": payload.get("candidate_commit"),
                "candidate_tree": payload.get("candidate_tree"),
                "candidate_source_fingerprint": payload.get("candidate_source_fingerprint"),
            }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "FAIL", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
