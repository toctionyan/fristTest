from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[1]
D1D = REPO / ".github/recovery_d1d_wrapper.py"
ROOT = Path("/tmp/v2018-baseline")
RUNNER_TEMP = Path(os.environ["RUNNER_TEMP"])
SUCCESSOR = "migration-v20.18-semantic-single-writer-output-coverage-r1"
EXPECTED_PROFILES = [
    "product-contract",
    "product-portable-conformance",
    "product-security",
    "product-quality-quick",
]
EXPECTED_CLOSURE_DIMS = {
    "original_failure",
    "focused_tests",
    "counterexamples",
    "regression",
    "negative_paths",
    "runtime_trace",
    "authority_boundary",
    "diff_review",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_capture(*argv: str) -> str:
    completed = subprocess.run(
        list(argv),
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed.stdout or ""


# Reconstruct and prove the exact H5 -> D1d lifecycle first. D2 never bypasses
# or imports prior artifacts as authority; it independently replays the closure.
subprocess.run(["python3", "-B", str(D1D)], cwd=REPO, check=True)

d1 = load(RUNNER_TEMP / "recovery-d1-summary.json")
assert d1["status"] == "PASS", d1
assert d1["formal_quick"] == {
    "decision": "PASS",
    "loop_status": "CONVERGED",
    "completion_eligible": True,
    "claim_status": "VERIFIED",
}, d1
assert d1["diff_review"]["decision"] == "PASS", d1
assert d1["diff_review"]["changed_path_count"] == 14, d1
assert d1["diff_review"]["out_of_scope_paths"] == [], d1
assert d1["diff_review"]["deterministic_findings"] == [], d1
assert set(d1["closure_dimensions"]) == EXPECTED_CLOSURE_DIMS, d1
assert d1["repair_governance_verification"] == "PASS", d1
assert d1["contract_closed"] is False, d1

active_path = ROOT / "governance/active-change.json"
active_before = load(active_path)
assert active_before["change_id"] == SUCCESSOR, active_before
assert active_before["status"] == "review", active_before
assert active_before["result"] == "PENDING", active_before
assert active_before["required_profiles"] == EXPECTED_PROFILES, active_before
assert active_before.get("verification") is None, active_before
assert active_before.get("repair_governance_consumed_at") is None, active_before

case_dir = ROOT / "governance/repair-cases" / SUCCESSOR
closure = load(case_dir / "closure-matrix.json")
assert closure["result"] == "CONVERGED", closure
assert closure["final_decision"] == "CLOSED_VERIFIED", closure
assert closure["loop_outcome"] == "CONVERGED", closure
assert closure.get("residual_risks") == [], closure
rows = closure.get("evidence") or []
assert {row["dimension"] for row in rows} == EXPECTED_CLOSURE_DIMS, rows
assert all(row.get("status") == "PASS" for row in rows), rows

py = ROOT / "services/agent-service/.venv/bin/python"

# Canonical deterministic verification. change_contract_cli itself revalidates
# repair governance, review attestations and all required profiles before it can
# write verification.json or the release-judge attestation.
verify_log = run_capture(str(py), "-B", "skillctl.py", "contract-verify", "--result", "CONVERGED")
(RUNNER_TEMP / "recovery-d2-contract-verify.log").write_text(verify_log, encoding="utf-8")

verified = load(active_path)
assert verified["change_id"] == SUCCESSOR, verified
assert verified["status"] == "verified", verified
assert verified["result"] == "CONVERGED", verified
assert verified["required_profiles"] == EXPECTED_PROFILES, verified
verification_ref = verified.get("verification")
assert isinstance(verification_ref, dict), verified
verification_path = ROOT / str(verification_ref["path"])
assert verification_path.is_file(), verification_ref
assert sha256(verification_path) == verification_ref["sha256"], verification_ref
verification = load(verification_path)
assert verification["change_id"] == SUCCESSOR, verification
assert verification["result"] == "CONVERGED", verification
assert verification["required_profiles"] == EXPECTED_PROFILES, verification
profile_results = verification.get("profile_results") or []
assert [row.get("requested_profile") for row in profile_results] == EXPECTED_PROFILES, profile_results
assert all(row.get("status") == "PASS" for row in profile_results), profile_results
repair = verification.get("repair_governance") or {}
assert repair.get("status") == "PASS", repair
assert repair.get("final_decision") == "CLOSED_VERIFIED", repair
assert repair.get("candidate_source_fingerprint") == closure["candidate_source_fingerprint"], repair

reviews = verified.get("review_attestations") or []
release_rows = [row for row in reviews if row.get("role") == "release-judge"]
assert len(release_rows) == 1, reviews
release = release_rows[0]
assert release["decision"] == "PASS", release
assert release["evidence"] == verification_ref["path"], release
assert release["evidence_sha256"] == verification_ref["sha256"], release

verified_source_fingerprint = verification_ref["source_fingerprint"]
verified_source_file_count = verification_ref["source_file_count"]

# Canonical close. This independently re-checks the verification hash, governed
# source fingerprint, result identity and repair-governance freshness before it
# can consume the repair-governance lifecycle and mark the Contract closed.
close_log = run_capture(str(py), "-B", "skillctl.py", "contract-close", "--result", "CONVERGED")
(RUNNER_TEMP / "recovery-d2-contract-close.log").write_text(close_log, encoding="utf-8")

closed = load(active_path)
assert closed["change_id"] == SUCCESSOR, closed
assert closed["status"] == "closed", closed
assert closed["result"] == "CONVERGED", closed
assert isinstance(closed.get("closed_at"), str) and closed["closed_at"], closed
assert isinstance(closed.get("repair_governance_consumed_at"), str) and closed["repair_governance_consumed_at"], closed
assert closed["verification"] == verification_ref, closed
assert closed["verification"]["source_fingerprint"] == verified_source_fingerprint, closed
assert closed["verification"]["source_file_count"] == verified_source_file_count, closed
closed_release = [row for row in closed.get("review_attestations") or [] if row.get("role") == "release-judge"]
assert len(closed_release) == 1 and closed_release[0]["decision"] == "PASS", closed_release
assert sha256(verification_path) == closed["verification"]["sha256"], closed

summary = {
    "schema_version": 1,
    "phase": "Closure-D2",
    "status": "PASS",
    "change_id": SUCCESSOR,
    "precondition_d1": "PASS",
    "required_profiles": EXPECTED_PROFILES,
    "profile_statuses": {row["requested_profile"]: row["status"] for row in profile_results},
    "repair_governance": {
        "status": repair["status"],
        "final_decision": repair["final_decision"],
        "candidate_source_fingerprint": repair["candidate_source_fingerprint"],
    },
    "verification": {
        "status": "verified",
        "result": verified["result"],
        "path": verification_ref["path"],
        "sha256": verification_ref["sha256"],
        "source_fingerprint": verified_source_fingerprint,
        "source_file_count": verified_source_file_count,
        "release_judge": "PASS",
    },
    "close": {
        "status": closed["status"],
        "result": closed["result"],
        "closed_at": closed["closed_at"],
        "repair_governance_consumed_at": closed["repair_governance_consumed_at"],
    },
    "main_mutated": False,
    "release_or_production_mutated": False,
}
(RUNNER_TEMP / "recovery-d2-summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
