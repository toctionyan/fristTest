from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import traceback
import zipfile
from pathlib import Path

REPO = "toctionyan/fristTest"
BASE = "e0e04d51e9da9790bef7bd0482584f60b8e975a9"
CONTROLLER = "4e3beb4a6a4a8f2f86b60b95eb8166045b411615"
CHANGE = "migration-v20.17-b38-context-reference-goal-coverage-r1"
PERMIT = "84266f85f4ffb8735fb3fe4dc941fefe050238513cece269c849f2b61b23dadd"
A7A7_ARTIFACT_ID = 9018795669
A7A7_ARTIFACT_SHA = "b1ab10011a5470a62392cd202a187993ab03df2f02c71ba533fc5709d128b8d7"
A7A7_CONTRACT_SHA = "87fd7a8aeea036f83c05d4c1d52e28150be06720f9cf90844e0edd14f246a9b5"
TARGET_SHA = "aff372809b92e43ff1292744969aa3a6f9c3b150eb7cedc4ace25caa48584801"
CLAIM_SHA = "33de1dee1dfd74f08a8cb89309a734217660ab890ace1b0a99643114c644580e"
POLICY_SHA = "ca5ce92e4f93d4c411da7d07abfdf5e49f8955aa3af117acfa2f9acdeb695914"
ORACLE_MANIFEST_SHA = "3eba94de3750b79d8bdec94b6d7342491082b5558eee650be6abf942afb9b4fa"
ORACLE_OVERLAY_SHA = "ac86e73820716bdf2a995ffe9fc2813b42cbbda741932a14e10597fe11d72da6"

ROOT = Path.cwd().resolve()
SOURCE = ROOT / "source"
BASELINE = ROOT / "baseline-source"
CANDIDATE = ROOT / "candidate-source"
CONTROL = ROOT / "control"
EVIDENCE = ROOT / "a7a8-evidence"
INPUT_REL = Path(".quality/stage5g4-c3h-a7-a4-successor-input")
PY = CONTROL / "services/agent-service/.venv/bin/python"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(argv: list[str], *, cwd: Path | None = None, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, env=os.environ.copy())
    if expect is not None and cp.returncode != expect:
        raise RuntimeError(
            f"command failed rc={cp.returncode}: {argv}\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}"
        )
    return cp


def copytree_contents(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def git_head(path: Path) -> str:
    return run(["git", "-C", str(path), "rev-parse", "HEAD"]).stdout.strip()


def prepare_authoritative_source() -> tuple[dict, dict]:
    assert git_head(SOURCE) == BASE
    assert git_head(BASELINE) == BASE
    assert git_head(CANDIDATE) == BASE
    assert git_head(CONTROL) == CONTROLLER

    a7a7_zip = ROOT / "a7a7.zip"
    with a7a7_zip.open("wb") as handle:
        cp = subprocess.run(
            ["gh", "api", f"/repos/{REPO}/actions/artifacts/{A7A7_ARTIFACT_ID}/zip"],
            stdout=handle,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.decode("utf-8", "replace"))
    assert sha(a7a7_zip) == A7A7_ARTIFACT_SHA

    download = ROOT / "a7a7-download"
    if download.exists():
        shutil.rmtree(download)
    download.mkdir()
    with zipfile.ZipFile(a7a7_zip) as zf:
        zf.extractall(download)
    copytree_contents(download / "root", SOURCE)

    active_path = SOURCE / "governance/active-change.json"
    assert sha(active_path) == A7A7_CONTRACT_SHA
    active = load(active_path)
    assert active["change_id"] == CHANGE
    assert active["status"] == "review"
    assert active["result"] == "PENDING"
    assert active["verification"] is None
    assert active["repair_governance_permit_digest"] == PERMIT
    assert active.get("repair_governance_consumed_at") is None
    roles = {row["role"]: row for row in active["review_attestations"]}
    assert roles["scope-planner"]["decision"] == "PASS"
    assert roles["adversarial-reviewer"]["decision"] == "PASS"
    assert "release-judge" not in roles

    manifest = load(download / "review-download/b38-download/FROZEN_B38_CANDIDATE30_MANIFEST.json")
    assert len(manifest["files"]) == 30
    mismatches = []
    for row in manifest["files"]:
        p = SOURCE / row["path"]
        actual = sha(p) if p.is_file() else "MISSING"
        if actual != row["sha256"]:
            mismatches.append({"path": row["path"], "actual": actual, "expected": row["sha256"]})
    assert not mismatches, mismatches

    inp = SOURCE / INPUT_REL
    assert sha(inp / "target.md") == TARGET_SHA
    assert sha(inp / "claim.json") == CLAIM_SHA
    assert sha(inp / "focused-policy.json") == POLICY_SHA
    assert sha(inp / "oracle/manifest.json") == ORACLE_MANIFEST_SHA
    assert sha(inp / "oracle/overlay.zip") == ORACLE_OVERLAY_SHA

    dump(EVIDENCE / "g0/pre-current-quick.json", {
        "status": "PASS",
        "source_head": BASE,
        "controller_head": CONTROLLER,
        "active_contract_sha256": sha(active_path),
        "contract_status": active["status"],
        "permit_digest": active["repair_governance_permit_digest"],
        "candidate_path_count": 30,
        "candidate_sha_mismatches": mismatches,
    })
    return manifest, active


def establish_current_quick(manifest: dict) -> dict:
    os.environ["QUALITY_EVIDENCE_SIGNING_KEY"] = secrets.token_hex(32)

    source_input = SOURCE / INPUT_REL
    for workspace in (BASELINE, CANDIDATE):
        target = workspace / INPUT_REL
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source_input, target)

    for row in manifest["files"]:
        src = SOURCE / row["path"]
        dst = CANDIDATE / row["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        assert sha(dst) == row["sha256"]

    baseline_evidence = BASELINE / f".quality/product-code/{CHANGE}/baseline-a7a8"
    baseline_state = BASELINE / f".quality/product-code/{CHANGE}/state-a7a8"
    baseline_cmd = [
        sys.executable, "-B", str(CONTROL / "scripts/quality_loop.py"),
        "--workspace-root", str(BASELINE),
        "--mode", "quick",
        "--target", str(BASELINE / INPUT_REL / "target.md"),
        "--evidence-dir", str(baseline_evidence),
        "--state-dir", str(baseline_state),
        "--baseline",
        "--policy", str(BASELINE / INPUT_REL / "focused-policy.json"),
        "--baseline-oracle-manifest", str(BASELINE / INPUT_REL / "oracle/manifest.json"),
        "--baseline-oracle-artifact", str(BASELINE / INPUT_REL / "oracle/overlay.zip"),
    ]
    baseline_cp = run(baseline_cmd, expect=None)
    baseline_summary = load(baseline_evidence / "run-summary.json")
    assert baseline_cp.returncode != 0
    assert baseline_summary["run_kind"] == "baseline"
    assert baseline_summary["decision"] == "FAIL"
    assert baseline_summary["loop_status"] == "BASELINE_RECORDED"
    assert baseline_summary["target_identity"]["id"] == CHANGE
    assert baseline_summary["claim_results"][0]["id"] == "B1.PREFERRED.RED"
    assert baseline_summary["claim_results"][0]["status"] == "FAILED"
    assert (baseline_evidence / "evidence-attestation.json").is_file()
    assert not (BASELINE / ".quality/quality-evidence.key").exists()

    candidate_baseline = CANDIDATE / f".quality/product-code/{CHANGE}/baseline-a7a8"
    candidate_baseline.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(baseline_evidence, candidate_baseline)

    verification_evidence = CANDIDATE / f".quality/product-code/{CHANGE}/verification-quick-a7a8"
    verification_state = CANDIDATE / f".quality/product-code/{CHANGE}/state-a7a8"
    verify_cmd = [
        sys.executable, "-B", str(CONTROL / "scripts/quality_loop.py"),
        "--workspace-root", str(CANDIDATE),
        "--mode", "quick",
        "--target", str(CANDIDATE / INPUT_REL / "target.md"),
        "--evidence-dir", str(verification_evidence),
        "--state-dir", str(verification_state),
        "--baseline-evidence", str(candidate_baseline),
        "--policy", str(CANDIDATE / INPUT_REL / "focused-policy.json"),
    ]
    verify_cp = run(verify_cmd, expect=None)
    verification_summary = load(verification_evidence / "run-summary.json")
    if verify_cp.returncode != 0:
        raise RuntimeError(
            "current quick verification failed\n"
            + verify_cp.stdout + "\n" + verify_cp.stderr
            + "\nsummary=" + json.dumps(verification_summary, indent=2)
        )
    assert verification_summary["run_kind"] == "verification"
    assert verification_summary["decision"] == "PASS"
    assert verification_summary["loop_status"] == "CONVERGED"
    assert verification_summary["completion_eligible"] is True
    assert verification_summary["mode"] == "quick"
    assert verification_summary["target_identity"]["id"] == CHANGE
    assert verification_summary["claim_results"][0]["id"] == "B1.PREFERRED.RED"
    assert verification_summary["claim_results"][0]["status"] == "PASSED"
    assert (verification_evidence / "evidence-attestation.json").is_file()
    assert not (CANDIDATE / ".quality/quality-evidence.key").exists()

    formal_evidence = SOURCE / f".quality/product-code/{CHANGE}/verification-quick-a7a8"
    if formal_evidence.exists():
        shutil.rmtree(formal_evidence)
    formal_evidence.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(verification_evidence, formal_evidence)

    record_cp = run([
        str(PY), "-B", str(CONTROL / "skill-system/controller/product_quality_bridge.py"),
        "record",
        "--workspace-root", str(SOURCE),
        "--evidence", formal_evidence.relative_to(SOURCE).as_posix(),
        "--mode", "quick",
    ])
    record_result = json.loads(record_cp.stdout)
    assert record_result["status"] == "PASS"
    assert record_result["workspace_root"] == str(SOURCE)

    current_cp = run([
        str(PY), "-B", str(CONTROL / "skill-system/controller/product_quality_bridge.py"),
        "current",
        "--workspace-root", str(SOURCE),
        "--mode", "quick",
    ])
    current_result = json.loads(current_cp.stdout)
    assert current_result["status"] == "PASS"
    assert current_result["workspace_root"] == str(SOURCE)
    assert current_result["reused_current_evidence"] is True

    active = load(SOURCE / "governance/active-change.json")
    assert active["status"] == "review"
    assert active["result"] == "PENDING"
    assert active["verification"] is None
    assert active.get("repair_governance_consumed_at") is None
    validation = active["product_validation"]
    assert validation["verification"] == formal_evidence.relative_to(SOURCE).as_posix()
    assert validation["verification_mode"] == "quick"
    assert validation["verification_source_fingerprint"] == current_result["source_fingerprint"]

    dump(EVIDENCE / "g1/current-quick.json", {
        "status": "PASS",
        "baseline_process_exit_code": baseline_cp.returncode,
        "baseline_decision": baseline_summary["decision"],
        "verification_process_exit_code": verify_cp.returncode,
        "verification_decision": verification_summary["decision"],
        "verification_loop_status": verification_summary["loop_status"],
        "completion_eligible": verification_summary["completion_eligible"],
        "record_result": record_result,
        "current_result": current_result,
        "formal_evidence": formal_evidence.relative_to(SOURCE).as_posix(),
        "formal_evidence_attestation_sha256": sha(formal_evidence / "evidence-attestation.json"),
    })
    return current_result


def verify_profile_workspace_authority() -> None:
    results = {}
    for profile in ("product-contract", "product-quality-quick"):
        cp = run(
            [str(PY), "-B", str(CONTROL / "skill-system/controller/profile_runner.py"), profile],
            cwd=SOURCE,
        )
        payload = json.loads(cp.stdout)
        assert payload["status"] == "PASS"
        assert payload["workspace_root"] == str(SOURCE)
        for row in payload["results"]:
            if row["profile"] in {"product-contract", "product-quality-quick"}:
                assert row["workspace_root"] == str(SOURCE)
                assert "--workspace-root" in row["argv"]
                idx = row["argv"].index("--workspace-root")
                assert row["argv"][idx + 1] == str(SOURCE)
                assert row["controller_cwd"] == str(CONTROL)
        results[profile] = payload
    dump(EVIDENCE / "g2/profile-workspace-authority.json", {
        "status": "PASS",
        "profiles": results,
    })


def contract_verify_and_close(manifest: dict, current_result: dict) -> None:
    active_path = SOURCE / "governance/active-change.json"
    before_verify = load(active_path)
    dump(EVIDENCE / "g3/contract-before-verify.json", before_verify)
    assert before_verify["status"] == "review"
    assert before_verify["verification"] is None

    verify_cp = run(
        [
            str(PY), "-B", str(CONTROL / "skill-system/controller/change_contract_cli.py"),
            "verify", "--result", "CONVERGED",
        ],
        cwd=SOURCE,
    )
    (EVIDENCE / "g3/contract-verify.stdout.txt").write_text(verify_cp.stdout, encoding="utf-8")
    (EVIDENCE / "g3/contract-verify.stderr.txt").write_text(verify_cp.stderr, encoding="utf-8")

    verified = load(active_path)
    dump(EVIDENCE / "g3/contract-after-verify.json", verified)
    assert verified["status"] == "verified"
    assert verified["result"] == "CONVERGED"
    assert isinstance(verified["verification"], dict)
    assert verified.get("repair_governance_consumed_at") is None
    roles = {row["role"]: row for row in verified["review_attestations"]}
    assert roles["scope-planner"]["decision"] == "PASS"
    assert roles["adversarial-reviewer"]["decision"] == "PASS"
    assert roles["release-judge"]["decision"] == "PASS"

    verification_path = SOURCE / verified["verification"]["path"]
    assert verification_path.is_file()
    assert sha(verification_path) == verified["verification"]["sha256"]
    verification = load(verification_path)
    assert verification["result"] == "CONVERGED"
    assert verification["source_fingerprint"] == current_result["source_fingerprint"]
    assert len(verification["profile_results"]) == 4
    assert all(row["status"] == "PASS" for row in verification["profile_results"])
    by_profile = {row["requested_profile"]: row for row in verification["profile_results"]}
    assert set(by_profile) == {
        "product-contract",
        "product-portable-conformance",
        "product-security",
        "product-quality-quick",
    }
    for name in ("product-contract", "product-quality-quick"):
        row = by_profile[name]
        assert row["workspace_root"] == str(SOURCE)
        assert row["status"] == "PASS"

    release = roles["release-judge"]
    assert release["evidence"] == verified["verification"]["path"]
    assert release["evidence_sha256"] == verified["verification"]["sha256"]

    mismatches = []
    for row in manifest["files"]:
        actual = sha(SOURCE / row["path"])
        if actual != row["sha256"]:
            mismatches.append({"path": row["path"], "actual": actual, "expected": row["sha256"]})
    assert not mismatches, mismatches

    close_cp = run(
        [
            str(PY), "-B", str(CONTROL / "skill-system/controller/change_contract_cli.py"),
            "close", "--result", "CONVERGED",
        ],
        cwd=SOURCE,
    )
    (EVIDENCE / "g4/contract-close.stdout.txt").write_text(close_cp.stdout, encoding="utf-8")
    (EVIDENCE / "g4/contract-close.stderr.txt").write_text(close_cp.stderr, encoding="utf-8")
    closed = load(active_path)
    dump(EVIDENCE / "g4/contract-closed.json", closed)
    assert closed["status"] == "closed"
    assert closed["result"] == "CONVERGED"
    assert closed["verification"] == verified["verification"]
    assert closed.get("closed_at")
    assert closed.get("repair_governance_consumed_at")
    roles_closed = {row["role"]: row for row in closed["review_attestations"]}
    assert roles_closed["release-judge"] == roles["release-judge"]

    permit_path = SOURCE / f"governance/repair-cases/{CHANGE}/change-permit.json"
    permit = load(permit_path)
    assert permit["status"] == "ACTIVE"
    assert permit["expires_after"] == "single-verification"
    assert permit["permit_digest"] == PERMIT

    post_mismatches = []
    for row in manifest["files"]:
        actual = sha(SOURCE / row["path"])
        if actual != row["sha256"]:
            post_mismatches.append({"path": row["path"], "actual": actual, "expected": row["sha256"]})
    assert not post_mismatches

    dump(EVIDENCE / "g4/close-integrity.json", {
        "status": "PASS",
        "contract_status": closed["status"],
        "contract_result": closed["result"],
        "verification_sha256": closed["verification"]["sha256"],
        "release_judge": roles_closed["release-judge"],
        "repair_governance_consumed_at": closed["repair_governance_consumed_at"],
        "permit_file_status": permit["status"],
        "permit_file_digest": permit["permit_digest"],
        "candidate_path_count": 30,
        "candidate_sha_mismatches": post_mismatches,
        "canonical_b38_pushed": False,
        "production_closed": False,
    })

    final_report = {
        "schema_version": 1,
        "stage": "5G4-C3H-A7-A8",
        "verdict": "SUCCESSOR_CURRENT_QUICK_CONTRACT_VERIFY_AND_CLOSE_PASS",
        "source_head": BASE,
        "controller_head": CONTROLLER,
        "change_id": CHANGE,
        "current_product_quick": "PASS",
        "current_product_quick_source_fingerprint": current_result["source_fingerprint"],
        "profile_workspace_authority": "PASS",
        "required_profiles": {
            row["requested_profile"]: row["status"]
            for row in verification["profile_results"]
        },
        "contract_verify": "PASS",
        "release_judge": "PASS",
        "contract_close": "PASS",
        "contract_status": "closed",
        "contract_result": "CONVERGED",
        "repair_governance_consumed": True,
        "candidate_path_count": 30,
        "candidate_sha_mismatches": post_mismatches,
        "canonical_b38_pushed": False,
        "production_closed": False,
    }
    dump(ROOT / "AUTHORITATIVE_A7_A8_REPORT.json", final_report)

    artifact_root = ROOT / "root"
    if artifact_root.exists():
        shutil.rmtree(artifact_root)
    shutil.copytree(SOURCE, artifact_root, ignore=shutil.ignore_patterns(".git"))


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    manifest, _active = prepare_authoritative_source()
    current = establish_current_quick(manifest)
    verify_profile_workspace_authority()
    contract_verify_and_close(manifest, current)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        dump(ROOT / "A7_A8_FAILURE.json", {
            "status": "FAIL",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })
        raise
