from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .contract import load_contract
    from .repair_governance import (
        create_permit,
        load_chain,
        validate_begin_ready,
        validate_verification_ready,
        write_closure_matrix,
        write_diff_review,
    )
except ImportError:
    from contract import load_contract  # type: ignore
    from repair_governance import (  # type: ignore
        create_permit,
        load_chain,
        validate_begin_ready,
        validate_verification_ready,
        write_closure_matrix,
        write_diff_review,
    )


def _workspace() -> Path:
    return Path.cwd().resolve()


def _contract() -> dict[str, object]:
    return load_contract(_workspace(), require_approved=False).payload


def _evidence(values: list[str]) -> dict[str, str]:
    rows: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError("evidence must use dimension=relative/path")
        dimension, path = raw.split("=", 1)
        if not dimension.strip() or not path.strip():
            raise ValueError("evidence must use dimension=relative/path")
        rows[dimension.strip()] = path.strip()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Governed repair evidence and ChangePermit lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("issue-permit")
    validate = sub.add_parser("validate")
    validate.add_argument("--stage", choices=["permit", "verification"], default="permit")
    validate.add_argument("--result", default="CONVERGED")
    diff = sub.add_parser("diff-review")
    diff.add_argument("--decision", choices=["PASS", "REJECT"], default="PASS")
    diff.add_argument("--finding", action="append", default=[])
    close = sub.add_parser("closure-record")
    close.add_argument("--result", required=True)
    close.add_argument("--loop-outcome", required=True)
    close.add_argument("--evidence", action="append", default=[], required=True)
    close.add_argument("--residual-risk", action="append", default=[])
    sub.add_parser("status")
    args = parser.parse_args()

    workspace = _workspace()
    payload = _contract()
    try:
        if args.command == "issue-permit":
            path = create_permit(workspace, payload)
            result = {"status": "PASS", "permit": path.relative_to(workspace).as_posix()}
        elif args.command == "validate":
            result = (
                validate_begin_ready(workspace, payload)
                if args.stage == "permit"
                else validate_verification_ready(workspace, payload, expected_result=args.result)
            )
        elif args.command == "diff-review":
            path = write_diff_review(
                workspace,
                payload,
                requested_decision=args.decision,
                reviewer_findings=args.finding,
            )
            result = {"status": "PASS", "diff_review": path.relative_to(workspace).as_posix()}
        elif args.command == "closure-record":
            path = write_closure_matrix(
                workspace,
                payload,
                result=args.result,
                evidence=_evidence(args.evidence),
                loop_outcome=args.loop_outcome,
                residual_risks=args.residual_risk,
            )
            result = {"status": "PASS", "closure_matrix": path.relative_to(workspace).as_posix()}
        else:
            chain = load_chain(workspace, payload, include_diff=False, include_closure=False)
            result = {
                "status": "PASS",
                "change_id": payload.get("change_id"),
                "case_dir": chain.case_dir.relative_to(workspace).as_posix(),
                "permit_digest": chain.permit_digest,
                "permit_status": chain.permit.get("status"),
            }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "FAIL", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
