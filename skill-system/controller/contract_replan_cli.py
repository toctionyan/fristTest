from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

CONTROLLER = Path(__file__).resolve().parent
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

import change_contract_cli as base_cli  # type: ignore
from contract import ACTIVE_CONTRACT, load_contract, validate_contract_payload  # type: ignore

PENDING_REPLAN = Path("governance/pending-replan.json")
CHANGE_HISTORY_ROOT = Path("governance/change-history")
TRANSITION_KINDS = {"repair", "migration", "revert"}
REPLAN_RESULT = "ARCHITECTURE_REPLAN_REQUIRED"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _workspace() -> Path:
    return Path.cwd().resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_contract(path: Path, payload: dict[str, Any]) -> None:
    errors = validate_contract_payload(payload)
    if errors:
        raise SystemExit("refusing to write invalid contract: " + "; ".join(errors))
    _write_json(path, payload)


def _load_pending(workspace: Path) -> tuple[Path, dict[str, Any]]:
    path = workspace / PENDING_REPLAN
    if not path.is_file():
        raise SystemExit(f"pending replan record does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"pending replan record is invalid JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SystemExit(f"pending replan record has invalid schema: {path}")
    required = ("predecessor_change_id", "successor_change_id", "replan_record", "replan_record_sha256")
    if any(not str(payload.get(key) or "").strip() for key in required):
        raise SystemExit("pending replan record is incomplete")
    record_path = (workspace / str(payload["replan_record"])).resolve()
    try:
        record_path.relative_to(workspace)
    except ValueError as exc:
        raise SystemExit("pending replan record points outside workspace") from exc
    if not record_path.is_file() or _sha256(record_path) != str(payload["replan_record_sha256"]):
        raise SystemExit("pending replan record is missing or changed")
    return path, payload


def cmd_replan(args: argparse.Namespace) -> int:
    workspace = _workspace()
    contract = load_contract(workspace, require_approved=False)
    if contract.status not in {"implementing", "review"}:
        raise SystemExit(f"replan is allowed only from implementing/review; got {contract.status}")
    if contract.target_kind.value not in TRANSITION_KINDS:
        raise SystemExit("replan is allowed only for transition contracts")
    if contract.payload.get("verification") is not None:
        raise SystemExit("cannot replan a contract that already has deterministic verification")
    if contract.payload.get("repair_governance_consumed_at") is not None:
        raise SystemExit("cannot replan after repair governance has been consumed")
    if any(
        isinstance(row, dict) and row.get("role") == "release-judge"
        for row in contract.payload.get("review_attestations", [])
    ):
        raise SystemExit("cannot replan after release-judge attestation exists")
    if str(contract.payload.get("result") or "PENDING") != "PENDING":
        raise SystemExit("replan requires a PENDING contract result")

    successor = str(args.successor_change_id or "").strip()
    reason = str(args.reason or "").strip()
    if not successor or successor == contract.change_id:
        raise SystemExit("replan requires a distinct non-empty successor change_id")
    if not reason:
        raise SystemExit("replan reason must be non-empty")
    pending_path = workspace / PENDING_REPLAN
    if pending_path.exists():
        raise SystemExit(f"another replan successor is already pending: {pending_path}")

    evidence = Path(args.evidence).expanduser().resolve()
    try:
        evidence_rel = evidence.relative_to(workspace)
    except ValueError as exc:
        raise SystemExit("replan evidence must be inside the workspace") from exc
    if not evidence.is_file():
        raise SystemExit(f"replan evidence does not exist: {evidence}")
    if not evidence_rel.as_posix().startswith("governance/"):
        raise SystemExit("replan evidence must be preserved under governance/")

    history_dir = workspace / CHANGE_HISTORY_ROOT / contract.change_id
    if history_dir.exists():
        raise SystemExit(f"change history already exists; refusing to overwrite: {history_dir}")
    history_dir.mkdir(parents=True, exist_ok=False)

    before_path = history_dir / "contract-before-replan.json"
    before_path.write_bytes(contract.path.read_bytes())

    recorded_at = _now()
    replanned = dict(contract.payload)
    replanned["status"] = "rejected"
    replanned["result"] = REPLAN_RESULT
    replanned["verification"] = None
    replanned["replan"] = {
        "successor_change_id": successor,
        "reason": reason,
        "evidence": evidence_rel.as_posix(),
        "evidence_sha256": _sha256(evidence),
        "recorded_at": recorded_at,
    }
    replanned_path = history_dir / "contract-replanned.json"
    _write_contract(replanned_path, replanned)

    replan_record = {
        "schema_version": 1,
        "predecessor_change_id": contract.change_id,
        "successor_change_id": successor,
        "result": REPLAN_RESULT,
        "reason": reason,
        "evidence": evidence_rel.as_posix(),
        "evidence_sha256": _sha256(evidence),
        "contract_before": before_path.relative_to(workspace).as_posix(),
        "contract_before_sha256": _sha256(before_path),
        "contract_replanned": replanned_path.relative_to(workspace).as_posix(),
        "contract_replanned_sha256": _sha256(replanned_path),
        "repair_governance_permit_digest": contract.payload.get("repair_governance_permit_digest"),
        "repair_governance_consumed_at": None,
        "verification": None,
        "recorded_at": recorded_at,
    }
    replan_path = history_dir / "replan.json"
    _write_json(replan_path, replan_record)

    pending = {
        "schema_version": 1,
        "predecessor_change_id": contract.change_id,
        "successor_change_id": successor,
        "replan_record": replan_path.relative_to(workspace).as_posix(),
        "replan_record_sha256": _sha256(replan_path),
        "recorded_at": recorded_at,
    }
    _write_json(pending_path, pending)

    # Only release the active pointer after all immutable history and successor
    # binding records are durable. Any earlier error therefore fails closed.
    contract.path.unlink()
    print(replan_path)
    return 0


def _add_successor_init_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", choices=["skill-only", "product-code"], default="skill-only")
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--target-kind", choices=base_cli.TARGET_KINDS, default="repair")
    parser.add_argument("--allow", action="append", default=[])
    parser.add_argument("--forbid", action="append", default=[])
    parser.add_argument("--affected-module", action="append", default=[])
    parser.add_argument("--invariant", action="append", default=[])
    parser.add_argument("--minimum-mode", choices=["static", "quick", "integration", "release"], default="static")
    parser.add_argument("--quality-target")
    parser.add_argument("--baseline-evidence")
    parser.add_argument("--decision-record")
    parser.add_argument("--variance", action="append", default=[])
    parser.add_argument("--architecture-policy-delta")
    parser.add_argument("--baseline-policy-id")
    parser.add_argument("--repair-governance")
    parser.add_argument("--approve", action="store_true")


def cmd_init_successor(args: argparse.Namespace) -> int:
    workspace = _workspace()
    active_path = workspace / ACTIVE_CONTRACT
    if active_path.exists():
        raise SystemExit(f"active contract already exists: {active_path}")
    pending_path, pending = _load_pending(workspace)
    successor = str(pending["successor_change_id"])
    if args.change_id != successor:
        raise SystemExit(f"pending replan requires successor change_id {successor}; got {args.change_id}")

    payload = (
        base_cli._skill_payload(args)
        if args.profile == "skill-only"
        else base_cli._product_payload(args, workspace)
    )
    payload["predecessor_change_id"] = str(pending["predecessor_change_id"])
    payload["replan_record"] = str(pending["replan_record"])
    payload["replan_record_sha256"] = str(pending["replan_record_sha256"])
    _write_contract(active_path, payload)
    pending_path.unlink()
    print(active_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed Change Contract replan/successor lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)

    replan = sub.add_parser("replan")
    replan.add_argument("--successor-change-id", required=True)
    replan.add_argument("--reason", required=True)
    replan.add_argument("--evidence", required=True)
    replan.set_defaults(func=cmd_replan)

    init = sub.add_parser("init-successor")
    _add_successor_init_args(init)
    init.set_defaults(func=cmd_init_successor)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
