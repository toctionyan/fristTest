#!/usr/bin/env python3
"""Prepare and validate an exact-SHA product target-bound Quick run.

The helper deliberately keeps the candidate's governance records while
resetting only the frozen product paths in a disposable baseline workspace.
This lets CI create a real transition baseline without trusting an uploaded or
stale local ``.quality`` directory.  Candidate source files explicitly named
as claim evidence are retained so a new regression test can run against the
pre-change product implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


def _workspace(path: str) -> Path:
    value = Path(path).expanduser().resolve()
    if not value.is_dir():
        raise ValueError(f"workspace does not exist: {value}")
    return value


def _load_contract(candidate: Path) -> dict[str, Any]:
    path = candidate / "governance" / "active-change.json"
    if not path.is_file():
        raise ValueError("candidate active product contract is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("active product contract must be an object")
    if payload.get("profile") not in {"product-code", "product-migration"}:
        raise ValueError("active contract is not a product contract")
    change_id = str(payload.get("change_id") or "").strip()
    target = str(payload.get("quality_target") or "").strip()
    allowed = payload.get("allowed_paths")
    if not change_id or not target or not isinstance(allowed, list) or not allowed:
        raise ValueError("active product contract is missing target binding fields")
    patterns = tuple(str(item).strip() for item in allowed)
    if any(not item or any(character in item for character in "*?[") for item in patterns):
        raise ValueError("target-bound CI requires explicit product allowed paths")
    return {
        "change_id": change_id,
        "quality_target": target,
        "allowed_paths": patterns,
        "minimum_quality_mode": str(payload.get("minimum_quality_mode") or ""),
    }


def _target_identity(candidate: Path, target: Path) -> dict[str, str]:
    text = target.read_text(encoding="utf-8")
    marker = "- 目标 ID："
    target_id = next(
        (line.split(marker, 1)[1].strip() for line in text.splitlines() if line.startswith(marker)),
        "",
    )
    if not target_id:
        raise ValueError("quality target does not declare 目标 ID")
    claim_line = next(
        (line for line in text.splitlines() if line.startswith("- 声明清单：")),
        "",
    )
    claim_path = claim_line.split("：", 1)[1].strip().strip("`") if claim_line else ""
    if not claim_path or not (candidate / claim_path).is_file():
        raise ValueError("quality target claim manifest is missing")
    return {
        "id": target_id,
        "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "claim_manifest": claim_path,
        "claim_sha256": hashlib.sha256((candidate / claim_path).read_bytes()).hexdigest(),
    }


def _claim_evidence_paths(candidate: Path, claim_manifest: Path) -> set[str]:
    """Return candidate source paths needed to execute the target's claims.

    A transition target commonly adds its regression tests together with the
    implementation.  Those tests are the executable definition of the
    expected transition and must therefore be run against the base product
    code when recording the baseline.  The old implementation removed every
    allowed path that was absent from base, which deleted precisely those
    tests before the quality controller could validate the claim manifest.
    """
    payload = json.loads(claim_manifest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("claim manifest must be an object")
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise ValueError("claim manifest must contain a claims array")
    paths: set[str] = set()
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            raise ValueError(f"claim #{index} must be an object")
        refs = claim.get("evidence_refs")
        if not isinstance(refs, list):
            raise ValueError(f"claim #{index} must contain evidence_refs")
        for raw_ref in refs:
            ref = str(raw_ref).strip()
            if not ref or ref.startswith("gate-log:"):
                continue
            path_text = ref.split("::", 1)[0].strip()
            relative = Path(path_text)
            if (
                not path_text
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                raise ValueError(f"claim evidence ref is not a safe workspace path: {ref}")
            resolved = (candidate / relative).resolve()
            try:
                resolved.relative_to(candidate.resolve())
            except ValueError as exc:
                raise ValueError(f"claim evidence ref escapes the candidate workspace: {ref}") from exc
            if not resolved.is_file():
                raise ValueError(f"claim evidence ref does not exist in candidate: {ref}")
            paths.add(relative.as_posix())
    return paths


def prepare_baseline(candidate_raw: str, base_raw: str, baseline_raw: str, output_raw: str, candidate_sha: str, base_sha: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", candidate_sha) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("candidate_sha and base_sha must be full lowercase commit SHAs")
    candidate = _workspace(candidate_raw)
    base = _workspace(base_raw)
    baseline = Path(baseline_raw).expanduser().resolve()
    if baseline.exists():
        shutil.rmtree(baseline)
    baseline.mkdir(parents=True)
    shutil.copytree(
        candidate,
        baseline,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", ".venv", "node_modules", ".quality"),
    )

    contract = _load_contract(candidate)
    target = (candidate / contract["quality_target"]).resolve()
    target.relative_to(candidate)
    identity = _target_identity(candidate, target)
    if identity["id"] != contract["change_id"]:
        raise ValueError("quality target ID does not match active contract change_id")
    if contract["minimum_quality_mode"] != "quick":
        raise ValueError("Stage2B1 target-bound CI requires minimum_quality_mode=quick")
    claim_evidence_paths = _claim_evidence_paths(
        candidate, candidate / identity["claim_manifest"]
    )

    for raw_path in contract["allowed_paths"]:
        relative = Path(raw_path)
        if relative.as_posix() in claim_evidence_paths:
            continue
        source = base / relative
        destination = baseline / relative
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif destination.exists():
            destination.unlink()

    output = Path(output_raw).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    binding = {
        "schema": "product-target-bound-ci@1",
        "candidate_sha": candidate_sha,
        "base_sha": base_sha,
        "target_path": contract["quality_target"],
        "target_identity": identity,
        "active_change_id": contract["change_id"],
        "allowed_paths": list(contract["allowed_paths"]),
        "baseline_workspace": str(baseline),
    }
    output.write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return binding


def validate_run(candidate_raw: str, evidence_raw: str, binding_raw: str, output_raw: str) -> dict[str, Any]:
    candidate = _workspace(candidate_raw)
    evidence = Path(evidence_raw).expanduser().resolve()
    binding = json.loads(Path(binding_raw).read_text(encoding="utf-8"))
    summary_path = evidence / "run-summary.json"
    attestation_path = evidence / "evidence-attestation.json"
    if not summary_path.is_file() or not attestation_path.is_file():
        raise ValueError("target-bound product evidence is incomplete")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    contract = _load_contract(candidate)
    target = (candidate / contract["quality_target"]).resolve()
    identity = _target_identity(candidate, target)
    target_identity = summary.get("target_identity")
    if not isinstance(target_identity, dict) or str(target_identity.get("id") or "") != identity["id"]:
        raise ValueError("verification evidence belongs to a different target")
    if binding.get("candidate_sha") in {None, ""} or binding.get("base_sha") in {None, ""}:
        raise ValueError("exact candidate/base SHA binding is missing")
    checks = {
        "run_kind": summary.get("run_kind") == "verification",
        "decision": summary.get("decision") == "PASS",
        "loop_status": summary.get("loop_status") == "CONVERGED",
        "completion_eligible": summary.get("completion_eligible") is True,
        "target_id": str(target_identity.get("id") or "") == contract["change_id"],
        "target_sha256": str(binding.get("target_identity", {}).get("target_sha256") or "") == identity["target_sha256"],
        "claim_sha256": str(binding.get("target_identity", {}).get("claim_sha256") or "") == identity["claim_sha256"],
    }
    if not all(checks.values()):
        raise ValueError("target-bound product verification did not converge: " + json.dumps(checks, sort_keys=True))
    result = {
        "schema": "product-target-bound-ci-result@1",
        "status": "PASS",
        "candidate_sha": binding["candidate_sha"],
        "base_sha": binding["base_sha"],
        "target_id": contract["change_id"],
        "evidence_dir": str(evidence),
        "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "checks": checks,
    }
    output = Path(output_raw).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-baseline")
    prepare.add_argument("--candidate", required=True)
    prepare.add_argument("--base", required=True)
    prepare.add_argument("--baseline", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--candidate-sha", required=True)
    prepare.add_argument("--base-sha", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--candidate", required=True)
    validate.add_argument("--evidence", required=True)
    validate.add_argument("--binding", required=True)
    validate.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare-baseline":
            result = prepare_baseline(args.candidate, args.base, args.baseline, args.output, args.candidate_sha, args.base_sha)
        else:
            result = validate_run(args.candidate, args.evidence, args.binding, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
