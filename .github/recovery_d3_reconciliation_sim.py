from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
MAIN_SHA = "419d2db37b1bac26a91994b1d2f32bd096f1d4f4"
OLD = "migration-v20.18-semantic-single-writer-output-coverage"
SUCCESSOR = OLD + "-r1"
D2B_ARTIFACT_ID = 9237447365
D2B_ARTIFACT_DIGEST = "sha256:ff9a09ae9fda019810e46212ef8753717ad08bc76a79f18ca5b687a22f0c0d07"
TARGET_FINGERPRINT = "66cfe571ae1ad96b2e0c12c6e4b83b7c456b327c539b9c09df09be6a78dba625"
CLAIM_FINGERPRINT = "e556ea97999459438e53f79a324f6c3a2725cf6c18ed07d054b053c2d84640fe"
ORIGINAL_PERMIT_DIGEST = "1acb65059e5da5e708ff35dafe48c55b6968e75cbeefd6c8bae00a2738eed76c"
EXPECTED_PROFILES = [
    "product-contract",
    "product-portable-conformance",
    "product-security",
    "product-quality-quick",
]
EXPECTED_REPAIR_FILES = [
    "baseline-manifest.json",
    "change-permit.json",
    "closure-matrix.json",
    "diff-review.json",
    "evidence/authority_boundary.json",
    "evidence/counterexamples.json",
    "evidence/focused_tests.json",
    "evidence/negative_paths.json",
    "evidence/runtime_trace.json",
    "failure-case.json",
    "plan-review.json",
    "repair-plan.json",
    "root-cause-proof.json",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), path
    return payload


def file_manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        result[rel] = sha256(path)
    return result


def copy_exact(src: Path, dst: Path) -> None:
    assert src.is_file(), src
    assert not dst.exists(), f"refusing to overwrite existing exact-main path: {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    assert sha256(dst) == sha256(src)


artifact_root_raw = os.environ.get("D2B_ARTIFACT_DIR", "").strip()
assert artifact_root_raw, "D2B_ARTIFACT_DIR is required"
ARTIFACT = Path(artifact_root_raw).resolve()
assert ARTIFACT.is_dir(), ARTIFACT

summary = load(ARTIFACT / "bound-export-summary.json")
assert summary["status"] == "PASS", summary
assert summary["successor_status"] == "closed", summary
assert summary["successor_result"] == "CONVERGED", summary
assert summary["replan_record_sha256"] == summary["closed_contract_replan_record_sha256"], summary

SIM = Path("/tmp/v2018-main-reconciliation-sim")
if SIM.exists():
    shutil.rmtree(SIM)
SIM.mkdir(parents=True)
subprocess.run(
    ["bash", "-lc", f"git archive {MAIN_SHA} | tar -x -C {SIM}"],
    cwd=REPO,
    check=True,
)

before_manifest = file_manifest(SIM)
active = SIM / "governance/active-change.json"
assert active.is_file(), "exact main no longer contains the expected stale active pointer"
exported_before = ARTIFACT / "predecessor-change-history/contract-before-replan.json"
assert active.read_bytes() == exported_before.read_bytes(), "main stale predecessor differs from D2b predecessor"
assert sha256(active) == summary["predecessor_contract_before_sha256"]
active_payload = load(active)
assert active_payload["change_id"] == OLD
assert active_payload["status"] == "implementing" and active_payload["result"] == "PENDING"
assert active_payload["repair_governance_permit_digest"] == ORIGINAL_PERMIT_DIGEST

history_dir = SIM / "governance/change-history" / OLD
closed_path = SIM / "governance/closed-changes" / f"{SUCCESSOR}.json"
pending_path = SIM / "governance/pending-replan.json"
target_path = SIM / "governance/targets" / f"{SUCCESSOR}.md"
claim_path = SIM / "governance/claims" / f"{SUCCESSOR}.json"
decision_path = SIM / "governance/decisions" / f"{SUCCESSOR}.json"
repair_dir = SIM / "governance/repair-cases" / SUCCESSOR
replan_evidence_path = SIM / "governance/replan-evidence/v20.18-invalid-frozen-target.json"
for path in (history_dir, closed_path, pending_path, target_path, claim_path, decision_path, repair_dir, replan_evidence_path):
    assert not path.exists(), f"exact main already contains recovery archive state: {path}"

# Install the immutable predecessor history exactly as bound by the already-closed successor.
for name in ("contract-before-replan.json", "contract-replanned.json", "replan.json"):
    copy_exact(ARTIFACT / "predecessor-change-history" / name, history_dir / name)
copy_exact(ARTIFACT / "predecessor-replan-evidence.json", replan_evidence_path)

# Install the exact successor governance inputs and immutable repair case.
copy_exact(ARTIFACT / "successor-target.md", target_path)
copy_exact(ARTIFACT / "successor-claim.json", claim_path)
copy_exact(ARTIFACT / "successor-decision.json", decision_path)
for rel in EXPECTED_REPAIR_FILES:
    copy_exact(ARTIFACT / "successor-repair-case" / rel, repair_dir / rel)
extra_repair_files = sorted(
    p.relative_to(ARTIFACT / "successor-repair-case").as_posix()
    for p in (ARTIFACT / "successor-repair-case").rglob("*") if p.is_file()
    if p.relative_to(ARTIFACT / "successor-repair-case").as_posix() not in EXPECTED_REPAIR_FILES
)
assert extra_repair_files == [], extra_repair_files

# A completed successor is archival state, not a second live writer. Preserve the exact closed snapshot.
copy_exact(ARTIFACT / "successor-closed-contract.json", closed_path)

# Only after all immutable history/archive records are present may the stale live pointer be removed.
active.unlink()
assert not active.exists()
assert not pending_path.exists(), "a completed successor must not leave a pending successor pointer"

# Validate predecessor history hash chain.
before_record = history_dir / "contract-before-replan.json"
replanned_record = history_dir / "contract-replanned.json"
replan_record = history_dir / "replan.json"
replan = load(replan_record)
replanned = load(replanned_record)
assert sha256(before_record) == summary["predecessor_contract_before_sha256"]
assert sha256(replan_record) == summary["replan_record_sha256"]
assert replan["predecessor_change_id"] == OLD
assert replan["successor_change_id"] == SUCCESSOR
assert replan["result"] == "ARCHITECTURE_REPLAN_REQUIRED"
assert replan["contract_before"] == f"governance/change-history/{OLD}/contract-before-replan.json"
assert replan["contract_before_sha256"] == sha256(before_record)
assert replan["contract_replanned"] == f"governance/change-history/{OLD}/contract-replanned.json"
assert replan["contract_replanned_sha256"] == sha256(replanned_record)
assert replan["evidence"] == "governance/replan-evidence/v20.18-invalid-frozen-target.json"
assert replan["evidence_sha256"] == sha256(replan_evidence_path)
assert replan["repair_governance_permit_digest"] == ORIGINAL_PERMIT_DIGEST
assert replanned["change_id"] == OLD
assert replanned["status"] == "rejected"
assert replanned["result"] == "ARCHITECTURE_REPLAN_REQUIRED"
assert replanned["replan"]["successor_change_id"] == SUCCESSOR

# Validate the archived successor identity and its immutable binding back to predecessor history.
closed = load(closed_path)
assert closed["change_id"] == SUCCESSOR
assert closed["predecessor_change_id"] == OLD
assert closed["status"] == "closed" and closed["result"] == "CONVERGED"
assert closed["replan_record"] == f"governance/change-history/{OLD}/replan.json"
assert closed["replan_record_sha256"] == sha256(replan_record)
assert closed["repair_governance"] == f"governance/repair-cases/{SUCCESSOR}"
assert isinstance(closed.get("repair_governance_consumed_at"), str) and closed["repair_governance_consumed_at"]
assert isinstance(closed.get("closed_at"), str) and closed["closed_at"]
assert closed["required_profiles"] == EXPECTED_PROFILES
reviews = {(row["role"], row["decision"]) for row in closed["review_attestations"]}
assert ("scope-planner", "PASS") in reviews
assert ("adversarial-reviewer", "PASS") in reviews
assert ("release-judge", "PASS") in reviews

# Validate exact successor Target/Claim identity using the current-main parser.
sys.path.insert(0, str(SIM))
from scripts.quality_control.contracts import _parse_target  # type: ignore  # noqa: E402
parsed_target = _parse_target(target_path, workspace=SIM)
assert parsed_target["fingerprint"] == TARGET_FINGERPRINT, parsed_target
assert parsed_target["claim_manifest_fingerprint"] == CLAIM_FINGERPRINT, parsed_target

# Validate contract schemas using current-main governance controller code.
sys.path.insert(0, str(SIM / "skill-system/controller"))
from contract import validate_contract_payload  # type: ignore  # noqa: E402
assert validate_contract_payload(replanned) == [], validate_contract_payload(replanned)
assert validate_contract_payload(closed) == [], validate_contract_payload(closed)

claim = load(claim_path)
assert claim["target_id"] == SUCCESSOR
claim_rows = claim.get("claims") or []
assert len(claim_rows) == 1 and claim_rows[0]["id"] == "V2018.A2B.SINGLE_WRITER_EXACT_OUTPUT"
decision = load(decision_path)
assert decision["change_id"] == SUCCESSOR
assert decision["predecessor_change_id"] == OLD
assert decision["replan_record"] == f"governance/change-history/{OLD}/replan.json"

# Validate repair-case terminal identities without pretending ephemeral .quality files are committed source.
permit = load(repair_dir / "change-permit.json")
diff = load(repair_dir / "diff-review.json")
closure = load(repair_dir / "closure-matrix.json")
assert permit["permit_digest"] == closed["repair_governance_permit_digest"]
assert diff["permit_digest"] == permit["permit_digest"]
assert diff["decision"] == "PASS"
assert diff["out_of_scope_paths"] == []
assert diff["deterministic_findings"] == []
assert len(diff["changed_paths"]) == 14
assert closure["permit_digest"] == permit["permit_digest"]
assert closure["result"] == "CONVERGED"
assert closure["final_decision"] == "CLOSED_VERIFIED"
assert closure["loop_outcome"] == "CONVERGED"
assert closure["candidate_source_fingerprint"] == diff["candidate_source_fingerprint"]
closure_dims = sorted(row["dimension"] for row in closure["evidence"])
assert closure_dims == sorted([
    "original_failure", "focused_tests", "counterexamples", "regression",
    "negative_paths", "runtime_trace", "authority_boundary", "diff_review",
])

# The simulation must be governance-archive-only. No product/deployment/runtime path may change.
after_manifest = file_manifest(SIM)
changed_paths = sorted(
    path for path in set(before_manifest) | set(after_manifest)
    if before_manifest.get(path) != after_manifest.get(path)
)
expected_added = {
    f"governance/change-history/{OLD}/contract-before-replan.json",
    f"governance/change-history/{OLD}/contract-replanned.json",
    f"governance/change-history/{OLD}/replan.json",
    "governance/replan-evidence/v20.18-invalid-frozen-target.json",
    f"governance/targets/{SUCCESSOR}.md",
    f"governance/claims/{SUCCESSOR}.json",
    f"governance/decisions/{SUCCESSOR}.json",
    f"governance/closed-changes/{SUCCESSOR}.json",
}
expected_added.update(f"governance/repair-cases/{SUCCESSOR}/{rel}" for rel in EXPECTED_REPAIR_FILES)
expected_changed = sorted(expected_added | {"governance/active-change.json"})
assert changed_paths == expected_changed, {"changed": changed_paths, "expected": expected_changed}
assert all(path.startswith("governance/") for path in changed_paths)
assert all(not path.startswith(("services/", "deployment/", "contracts/", "web/")) for path in changed_paths)
assert set(before_manifest).difference(after_manifest) == {"governance/active-change.json"}
assert set(after_manifest).difference(before_manifest) == expected_added

out_dir = Path(os.environ.get("D3_OUT_DIR", REPO / "recovery-d3-simulation-artifact")).resolve()
if out_dir.exists():
    shutil.rmtree(out_dir)
out_dir.mkdir(parents=True)

archive_hashes = {
    path: after_manifest[path]
    for path in expected_changed
    if path != "governance/active-change.json"
}
result = {
    "schema_version": 1,
    "phase": "D3-exact-main-reconciliation-simulation",
    "status": "PASS",
    "main_sha": MAIN_SHA,
    "d2b_artifact_id": D2B_ARTIFACT_ID,
    "d2b_artifact_digest": D2B_ARTIFACT_DIGEST,
    "predecessor": {
        "change_id": OLD,
        "original_active_sha256": summary["predecessor_contract_before_sha256"],
        "replan_record_sha256": sha256(replan_record),
        "final_status": replanned["status"],
        "final_result": replanned["result"],
    },
    "successor": {
        "change_id": SUCCESSOR,
        "closed_contract_sha256": sha256(closed_path),
        "replan_record_sha256": closed["replan_record_sha256"],
        "status": closed["status"],
        "result": closed["result"],
        "release_judge_pass": ("release-judge", "PASS") in reviews,
    },
    "pointer_state": {
        "active_change_present": active.exists(),
        "pending_replan_present": pending_path.exists(),
        "live_writer_count": 0,
    },
    "target_identity": {
        "fingerprint": parsed_target["fingerprint"],
        "claim_manifest_fingerprint": parsed_target["claim_manifest_fingerprint"],
    },
    "repair_closure": {
        "diff_review": diff["decision"],
        "changed_path_count": len(diff["changed_paths"]),
        "out_of_scope_paths": diff["out_of_scope_paths"],
        "closure_result": closure["result"],
        "closure_final_decision": closure["final_decision"],
        "closure_dimensions": closure_dims,
    },
    "simulated_main_changed_paths": changed_paths,
    "governance_only": True,
    "main_mutated": False,
    "release_or_production_mutated": False,
}
(out_dir / "d3-reconciliation-simulation-summary.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
(out_dir / "d3-changed-paths.json").write_text(
    json.dumps({"status": "PASS", "changed_paths": changed_paths}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
(out_dir / "d3-archive-hashes.json").write_text(
    json.dumps({"status": "PASS", "files": archive_hashes}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(result, ensure_ascii=False, indent=2))
