from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

CONTROLLER = Path(__file__).resolve().parent
if str(CONTROLLER) not in sys.path:
    sys.path.insert(0, str(CONTROLLER))

from contract import load_contract, validate_contract_payload  # type: ignore
from product_contract_gate import verify as verify_product_contract  # type: ignore
from product_scope import MODE_ORDER  # type: ignore
from verification import source_fingerprint  # type: ignore

ROOT = CONTROLLER.parents[1]
QUALITY_LOOP = ROOT / "scripts" / "quality_loop.py"


def _now_slug() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_contract(path: Path, payload: dict[str, Any]) -> None:
    errors = validate_contract_payload(payload)
    if errors:
        raise ValueError("refusing to write invalid contract: " + "; ".join(errors))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_python() -> str:
    resolver = ROOT / "services" / "agent-service" / "scripts" / "resolve_python.py"
    completed = subprocess.run([sys.executable, str(resolver)], cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip().splitlines()[-1]
    return sys.executable


def _relative_file(raw: object, *, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} is required")
    path = (ROOT / raw).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"{label} escapes workspace") from exc
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {raw}")
    return path


def _verify_evidence_attestation(evidence_dir: Path) -> None:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from quality_loop import verify_evidence_attestation  # type: ignore
    verify_evidence_attestation(ROOT, evidence_dir)


def _validated_evidence(
    *, contract: Any, evidence_dir: Path, selected: str
) -> tuple[dict[str, Any], str, int]:
    evidence_dir = evidence_dir.resolve()
    try:
        evidence_dir.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("product verification evidence escapes workspace") from exc
    summary_path = evidence_dir / "run-summary.json"
    if not summary_path.is_file():
        raise ValueError("product verification run-summary.json is missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise ValueError("product verification summary is invalid")
    if summary.get("run_kind") != "verification" or summary.get("decision") != "PASS":
        raise ValueError("product verification did not produce a PASS decision")
    if summary.get("loop_status") != "CONVERGED" or summary.get("completion_eligible") is not True:
        raise ValueError("product verification is not completion eligible")
    summary_mode = str(summary.get("mode") or "")
    if summary_mode not in MODE_ORDER or MODE_ORDER[summary_mode] < MODE_ORDER[selected]:
        raise ValueError("product verification summary mode is below the required mode")
    target_identity = summary.get("target_identity")
    if not isinstance(target_identity, dict) or str(target_identity.get("id") or "") != contract.change_id:
        raise ValueError("product verification belongs to a different Target")
    _verify_evidence_attestation(evidence_dir)
    fingerprint, file_count = source_fingerprint(ROOT, contract.allowed_paths)
    return summary, fingerprint, file_count


def record(evidence: str, mode: str, bridge_result: str | None = None) -> dict[str, Any]:
    contract = load_contract(ROOT, require_approved=False)
    minimum = str(contract.payload.get("minimum_quality_mode") or "static")
    if mode not in MODE_ORDER or MODE_ORDER[mode] < MODE_ORDER[minimum]:
        raise ValueError(f"recorded mode {mode!r} is below contract minimum {minimum!r}")
    evidence_dir = (ROOT / evidence).resolve()
    summary, fingerprint, file_count = _validated_evidence(
        contract=contract, evidence_dir=evidence_dir, selected=mode
    )
    payload = dict(contract.payload)
    validation = dict(payload.get("product_validation") or {})
    validation.update({
        "verification": evidence_dir.relative_to(ROOT).as_posix(),
        "verification_mode": str(summary.get("mode") or mode),
        "verification_source_fingerprint": fingerprint,
        "verification_source_file_count": file_count,
        "verification_recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    if bridge_result:
        validation["verification_bridge_result"] = bridge_result
    payload["product_validation"] = validation
    _write_contract(contract.path, payload)
    return {
        "status": "PASS",
        "run_kind": "record-verification",
        "mode": mode,
        "evidence_dir": evidence_dir.relative_to(ROOT).as_posix(),
        "source_fingerprint": fingerprint,
        "source_file_count": file_count,
    }


def current(mode: str | None = None) -> dict[str, Any]:
    contract = load_contract(ROOT, require_approved=False)
    minimum = str(contract.payload.get("minimum_quality_mode") or "static")
    selected = mode or minimum
    if selected not in MODE_ORDER or MODE_ORDER[selected] < MODE_ORDER[minimum]:
        raise ValueError(f"requested mode {selected!r} is below contract minimum {minimum!r}")
    validation = contract.payload.get("product_validation")
    if not isinstance(validation, dict):
        raise ValueError("current product verification is missing; run product-verify or product-repair-loop first")
    stored_mode = str(validation.get("verification_mode") or "")
    if stored_mode not in MODE_ORDER or MODE_ORDER[stored_mode] < MODE_ORDER[selected]:
        raise ValueError(f"current product verification mode {stored_mode!r} is below required {selected!r}")
    raw_evidence = validation.get("verification")
    if not isinstance(raw_evidence, str) or not raw_evidence.strip():
        raise ValueError("current product verification evidence path is missing")
    evidence_dir = (ROOT / raw_evidence).resolve()
    _summary, fingerprint, file_count = _validated_evidence(
        contract=contract, evidence_dir=evidence_dir, selected=selected
    )
    if validation.get("verification_source_fingerprint") != fingerprint:
        raise ValueError("governed product source changed after product verification")
    if validation.get("verification_source_file_count") != file_count:
        raise ValueError("governed product source file set changed after product verification")
    return {
        "status": "PASS",
        "run_kind": "current-verification",
        "mode": selected,
        "verified_mode": stored_mode,
        "evidence_dir": raw_evidence,
        "source_fingerprint": fingerprint,
        "source_file_count": file_count,
        "reused_current_evidence": True,
    }


def _run_quality(
    *,
    baseline: bool,
    mode: str,
    evidence_dir: Path,
    state_dir: Path,
    baseline_oracle: Path | None = None,
) -> dict[str, Any]:
    errors = verify_product_contract()
    if errors and not (baseline and errors == ["product_transition_requires_baseline_evidence"]):
        raise ValueError("product contract gate failed: " + "; ".join(errors))
    contract = load_contract(ROOT, require_approved=False)
    target = _relative_file(contract.payload.get("quality_target"), label="quality_target")
    argv = [
        _resolve_python(), "-B", str(QUALITY_LOOP),
        "--workspace-root", str(ROOT),
        "--mode", mode,
        "--target", str(target),
        "--evidence-dir", str(evidence_dir),
        "--state-dir", str(state_dir),
    ]
    if baseline:
        argv.append("--baseline")
        if baseline_oracle is not None:
            argv.extend(["--baseline-oracle", str(baseline_oracle)])
    else:
        if baseline_oracle is not None:
            raise ValueError("baseline oracle transport is valid only for product baseline")
        raw_baseline = contract.payload.get("baseline_evidence")
        if contract.target_kind.value in {"repair", "migration", "revert"}:
            if not isinstance(raw_baseline, str) or not raw_baseline.strip():
                raise ValueError("transition verification requires product-baseline first")
            baseline_path = (ROOT / raw_baseline).resolve()
            argv.extend(["--baseline-evidence", str(baseline_path)])
    completed = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, check=False)
    semantic_pass = completed.returncode == 0
    baseline_summary: dict[str, Any] | None = None
    if baseline:
        summary_path = evidence_dir / "run-summary.json"
        record_path = evidence_dir / "baseline-record.json"
        if summary_path.is_file():
            try:
                loaded = json.loads(summary_path.read_text(encoding="utf-8"))
                baseline_summary = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                baseline_summary = None
        semantic_pass = bool(
            record_path.is_file()
            and baseline_summary
            and baseline_summary.get("run_kind") == "baseline"
            and baseline_summary.get("loop_status") == "BASELINE_RECORDED"
        )
    result = {
        "status": "PASS" if semantic_pass else "FAIL",
        "run_kind": "baseline" if baseline else "verification",
        "process_exit_code": completed.returncode,
        "expected_red_baseline": bool(baseline and semantic_pass and completed.returncode != 0),
        "mode": mode,
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "evidence_dir": evidence_dir.relative_to(ROOT).as_posix(),
    }
    bridge_dir = evidence_dir.parent / "bridge-results"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    result_path = bridge_dir / f"{evidence_dir.name}.json"
    result["bridge_result"] = result_path.relative_to(ROOT).as_posix()
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not baseline and semantic_pass:
        record(
            evidence_dir.relative_to(ROOT).as_posix(),
            mode,
            result_path.relative_to(ROOT).as_posix(),
        )
    return result


def baseline(force: bool = False, baseline_oracle: str | None = None) -> dict[str, Any]:
    contract = load_contract(ROOT, require_approved=False)
    if contract.target_kind.value not in {"repair", "migration", "revert"}:
        raise ValueError("product baseline is valid only for repair, migration or revert")
    mode = str(contract.payload.get("minimum_quality_mode") or "static")
    evidence_dir = ROOT / ".quality" / "product-code" / contract.change_id / "baseline"
    state_dir = ROOT / ".quality" / "product-code" / contract.change_id / "state"
    if evidence_dir.exists():
        if not force:
            raise ValueError(f"baseline evidence already exists: {evidence_dir.relative_to(ROOT)}")
        shutil.rmtree(evidence_dir)
    oracle_path = (
        _relative_file(baseline_oracle, label="baseline_oracle")
        if baseline_oracle is not None
        else None
    )
    result = _run_quality(
        baseline=True,
        mode=mode,
        evidence_dir=evidence_dir,
        state_dir=state_dir,
        baseline_oracle=oracle_path,
    )
    if result["status"] == "PASS":
        payload = dict(contract.payload)
        payload["baseline_evidence"] = evidence_dir.relative_to(ROOT).as_posix()
        payload["product_validation"] = {
            "baseline": result["evidence_dir"],
            "baseline_mode": mode,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        _write_contract(contract.path, payload)
    return result


def verify(mode: str | None = None) -> dict[str, Any]:
    contract = load_contract(ROOT, require_approved=False)
    minimum = str(contract.payload.get("minimum_quality_mode") or "static")
    selected = mode or minimum
    if selected not in MODE_ORDER or MODE_ORDER[selected] < MODE_ORDER[minimum]:
        raise ValueError(f"requested mode {selected!r} is below contract minimum {minimum!r}")
    evidence_dir = ROOT / ".quality" / "product-code" / contract.change_id / f"verification-{selected}-{_now_slug()}"
    state_dir = ROOT / ".quality" / "product-code" / contract.change_id / "state"
    return _run_quality(baseline=False, mode=selected, evidence_dir=evidence_dir, state_dir=state_dir)


def status() -> dict[str, Any]:
    contract = load_contract(ROOT, require_approved=False)
    return {
        "status": "PASS",
        "change_id": contract.change_id,
        "profile": contract.profile,
        "target_kind": contract.target_kind.value,
        "minimum_quality_mode": contract.payload.get("minimum_quality_mode"),
        "quality_target": contract.payload.get("quality_target"),
        "baseline_evidence": contract.payload.get("baseline_evidence"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    baseline_parser = sub.add_parser("baseline")
    baseline_parser.add_argument("--force", action="store_true")
    baseline_parser.add_argument(
        "--baseline-oracle",
        help="workspace-relative immutable oracle manifest under .quality/baseline-oracles",
    )
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--mode", choices=tuple(MODE_ORDER))
    current_parser = sub.add_parser("current")
    current_parser.add_argument("--mode", choices=tuple(MODE_ORDER))
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--evidence", required=True)
    record_parser.add_argument("--mode", choices=tuple(MODE_ORDER), required=True)
    record_parser.add_argument("--bridge-result")
    sub.add_parser("status")
    args = parser.parse_args()
    try:
        if args.command == "baseline":
            result = baseline(force=args.force, baseline_oracle=args.baseline_oracle)
        elif args.command == "verify":
            result = verify(args.mode)
        elif args.command == "current":
            result = current(args.mode)
        elif args.command == "record":
            result = record(args.evidence, args.mode, args.bridge_result)
        else:
            result = status()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "FAIL", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
