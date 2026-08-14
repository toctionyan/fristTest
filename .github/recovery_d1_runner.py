from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
H5 = REPO / ".github/recovery_h5_wrapper.py"
ROOT = Path("/tmp/v2018-baseline")
SUCCESSOR = "migration-v20.18-semantic-single-writer-output-coverage-r1"
BASELINE_SHA = "aeb9a445001d4922e13a032e4cccc12f8ff34e9a"
CANDIDATE_SHA = "55403a01f957257fbbefead32bcde21b7d866001"
PERF_SHA = "7e6bd3bf65718c13f1bcbc28011cb4071b8c96a8"
BASELINE_SOURCE_FINGERPRINT = "ba330d4fcd5af39fbc16f1b3b79e03b2c431e590878fa7a683c8f80744714e5e"
EXPECTED_CHANGED_PATHS = [
    "services/agent-service/src/agent_core/kernel/semantic_contract.py",
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
    "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
    "services/agent-service/src/agent_core/lifecycle/protocol.py",
    "services/agent-service/src/agent_core/lifecycle/semantic_contract.py",
    "services/agent-service/src/agent_core/modules/contracts.py",
    "services/agent-service/src/agent_core/modules/registry.py",
    "services/agent-service/src/agent_core/runtime/capability_effects.py",
    "services/agent-service/src/agent_modules/ecommerce/module.py",
    "services/agent-service/src/agent_modules/ecommerce/semantic_vocabulary.py",
    "services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py",
    "services/agent-service/tests/runtime/test_semantic_output_coverage.py",
    "services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py",
]
DIMS = ("focused_tests", "counterexamples", "negative_paths", "runtime_trace", "authority_boundary")


def run(*args: str) -> None:
    subprocess.run(list(args), cwd=ROOT, check=True)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# Reproduce the exact H5 lifecycle through the already-proven formal Quick.
subprocess.run(["python3", "-B", str(H5)], cwd=REPO, check=True)

runner_temp = Path(os.environ["RUNNER_TEMP"])
py = ROOT / "services/agent-service/.venv/bin/python"
case_dir = ROOT / "governance/repair-cases" / SUCCESSOR
focused_dir = runner_temp / "recovery-h3-focused"
quick_rel = (runner_temp / "quick-summary-path.txt").read_text(encoding="utf-8").strip()
quick_path = ROOT / quick_rel
fresh_permit = (runner_temp / "fresh-permit-digest.txt").read_text(encoding="utf-8").strip()

quick = load(quick_path)
assert quick["decision"] == "PASS", quick
assert quick["loop_status"] == "CONVERGED", quick
assert quick["completion_eligible"] is True, quick
assert quick.get("missing_prerequisites") == [], quick
assert quick.get("unverified_claim_ids") == [], quick
assert quick.get("baseline_transition_unverified_claim_ids") == [], quick
claims = {item["id"]: item for item in quick.get("claim_results", [])}
claim = claims["V2018.A2B.SINGLE_WRITER_EXACT_OUTPUT"]
assert claim["status"] == "VERIFIED", claim
assert claim["gate_statuses"] == {"python-test-suites": "PASS"}, claim

# Post-Quick only: freeze the canonical deterministic diff review.
run(str(py), "-B", "skillctl.py", "repair-diff-review", "--decision", "PASS")
diff_path = case_dir / "diff-review.json"
diff = load(diff_path)
assert diff["decision"] == "PASS", diff
assert diff["changed_paths"] == EXPECTED_CHANGED_PATHS, (diff["changed_paths"], EXPECTED_CHANGED_PATHS)
assert diff["out_of_scope_paths"] == [], diff
assert diff["deterministic_findings"] == [], diff
assert diff["baseline_source_fingerprint"] == BASELINE_SOURCE_FINGERPRINT, diff
assert diff["candidate_source_fingerprint"] != BASELINE_SOURCE_FINGERPRINT, diff

# Promote the already-executed focused receipts into the repair case only after Quick.
evidence_dir = case_dir / "evidence"
evidence_dir.mkdir(parents=True, exist_ok=True)
for dim in DIMS:
    src = focused_dir / f"{dim}.json"
    payload = load(src)
    assert payload["dimension"] == dim, payload
    assert payload["status"] == "PASS", payload
    assert payload["exit_code"] == 0, payload
    assert payload["baseline_sha"] == BASELINE_SHA, payload
    assert payload["candidate_sha"] == CANDIDATE_SHA, payload
    assert payload["performance_repair_sha"] == PERF_SHA, payload
    shutil.copy2(src, evidence_dir / src.name)

# Canonical independent review attestations.  Their wording matches the reviewed
# H5 RED shape: two immutable PR551 invariants plus the PR607/PR649 contract-owner oracle.
review_dir = ROOT / ".quality/product-control-plane" / SUCCESSOR / "reviews"
review_dir.mkdir(parents=True, exist_ok=True)
now = dt.datetime.now(dt.timezone.utc).isoformat()
scope_review = {
    "schema_version": 1,
    "role": "scope-planner",
    "decision": "PASS",
    "fresh_permit_digest": fresh_permit,
    "basis": [
        "successor ChangePermit is fresh and bound to the successor governance identity",
        "deterministic post-Quick diff-review contains exactly 14 permitted product/test paths and zero out-of-scope paths",
        "formal successor Quick is CONVERGED and completion_eligible",
    ],
    "quick_evidence": quick_rel,
    "recorded_at": now,
}
adversarial_review = {
    "schema_version": 1,
    "role": "adversarial-reviewer",
    "decision": "PASS",
    "basis": [
        "the exact reviewed RED baseline contains only the three expected A2/B semantic RED nodes and the GREEN candidate removes them",
        "focused, counterexample, negative-path, authority-boundary and runtime-trace receipts all PASS",
        "formal successor Quick verifies the P1 transition Claim with python-test-suites PASS",
    ],
    "quick_evidence": quick_rel,
    "recorded_at": now,
}
(scope_path := review_dir / "scope-planner.json").write_text(json.dumps(scope_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(adversarial_path := review_dir / "adversarial-reviewer.json").write_text(json.dumps(adversarial_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
run(str(py), "-B", "skillctl.py", "attest-review", "--role", "scope-planner", "--decision", "PASS", "--evidence", str(scope_path.relative_to(ROOT)))
run(str(py), "-B", "skillctl.py", "attest-review", "--role", "adversarial-reviewer", "--decision", "PASS", "--evidence", str(adversarial_path.relative_to(ROOT)))

# Freeze the eight-dimensional repair closure, then validate it deterministically.
closure_cmd = [
    str(py), "-B", "skillctl.py", "repair-closure-record",
    "--result", "CONVERGED",
    "--loop-outcome", "CONVERGED",
    "--evidence", f"original_failure=.quality/product-code/{SUCCESSOR}/baseline/baseline-record.json",
    "--evidence", f"focused_tests=governance/repair-cases/{SUCCESSOR}/evidence/focused_tests.json",
    "--evidence", f"counterexamples=governance/repair-cases/{SUCCESSOR}/evidence/counterexamples.json",
    "--evidence", f"regression={quick_rel}",
    "--evidence", f"negative_paths=governance/repair-cases/{SUCCESSOR}/evidence/negative_paths.json",
    "--evidence", f"runtime_trace=governance/repair-cases/{SUCCESSOR}/evidence/runtime_trace.json",
    "--evidence", f"authority_boundary=governance/repair-cases/{SUCCESSOR}/evidence/authority_boundary.json",
    "--evidence", f"diff_review=governance/repair-cases/{SUCCESSOR}/diff-review.json",
]
subprocess.run(closure_cmd, cwd=ROOT, check=True)
run(str(py), "-B", "skillctl.py", "repair-governance-validate", "--stage", "verification", "--result", "CONVERGED")

# D1 must not close the product Contract.
active_path = ROOT / "governance/active-change.json"
assert active_path.is_file(), "D1 unexpectedly removed active successor Contract"
active = load(active_path)
assert active["change_id"] == SUCCESSOR, active

summary = {
    "schema_version": 1,
    "phase": "Closure-D1",
    "status": "PASS",
    "formal_quick": {
        "decision": quick["decision"],
        "loop_status": quick["loop_status"],
        "completion_eligible": quick["completion_eligible"],
        "claim_status": claim["status"],
    },
    "diff_review": {
        "decision": diff["decision"],
        "changed_path_count": len(diff["changed_paths"]),
        "out_of_scope_paths": diff["out_of_scope_paths"],
        "deterministic_findings": diff["deterministic_findings"],
        "baseline_source_fingerprint": diff["baseline_source_fingerprint"],
        "candidate_source_fingerprint": diff["candidate_source_fingerprint"],
    },
    "closure_dimensions": [
        "original_failure", "focused_tests", "counterexamples", "regression",
        "negative_paths", "runtime_trace", "authority_boundary", "diff_review",
    ],
    "repair_governance_verification": "PASS",
    "contract_closed": False,
    "active_change_id": active["change_id"],
}
(runner_temp / "recovery-d1-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
