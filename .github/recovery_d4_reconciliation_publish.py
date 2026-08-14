from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
MAIN_SHA = "419d2db37b1bac26a91994b1d2f32bd096f1d4f4"
OLD = "migration-v20.18-semantic-single-writer-output-coverage"
SUCCESSOR = OLD + "-r1"
BRANCH = "governance/v20.18-reconcile-closed-successor"
ACTIVE = "governance/active-change.json"


def run(*args: str, cwd: Path = REPO, capture: bool = False) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    return (result.stdout or "").strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), path
    return payload


def source_for(destination: str, artifact: Path) -> Path:
    history_prefix = f"governance/change-history/{OLD}/"
    repair_prefix = f"governance/repair-cases/{SUCCESSOR}/"
    fixed = {
        "governance/replan-evidence/v20.18-invalid-frozen-target.json": artifact / "predecessor-replan-evidence.json",
        f"governance/targets/{SUCCESSOR}.md": artifact / "successor-target.md",
        f"governance/claims/{SUCCESSOR}.json": artifact / "successor-claim.json",
        f"governance/decisions/{SUCCESSOR}.json": artifact / "successor-decision.json",
        f"governance/closed-changes/{SUCCESSOR}.json": artifact / "successor-closed-contract.json",
    }
    if destination in fixed:
        return fixed[destination]
    if destination.startswith(history_prefix):
        return artifact / "predecessor-change-history" / destination.removeprefix(history_prefix)
    if destination.startswith(repair_prefix):
        return artifact / "successor-repair-case" / destination.removeprefix(repair_prefix)
    raise AssertionError(f"unmapped D3 archive path: {destination}")


artifact_raw = os.environ.get("D2B_ARTIFACT_DIR", "").strip()
d3_raw = os.environ.get("D3_OUT_DIR", "").strip()
out_raw = os.environ.get("D4_OUT_DIR", "").strip()
assert artifact_raw and d3_raw and out_raw
ARTIFACT = Path(artifact_raw).resolve()
D3 = Path(d3_raw).resolve()
OUT = Path(out_raw).resolve()
assert ARTIFACT.is_dir() and D3.is_dir()

# Make failure evidence uploadable before any publisher mutation/assertion occurs.
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
for name in ("d3-reconciliation-simulation-summary.json", "d3-changed-paths.json", "d3-archive-hashes.json"):
    shutil.copyfile(D3 / name, OUT / name)

summary = load(D3 / "d3-reconciliation-simulation-summary.json")
changed = load(D3 / "d3-changed-paths.json")
hashes = load(D3 / "d3-archive-hashes.json")
assert summary["status"] == "PASS"
assert summary["main_sha"] == MAIN_SHA
assert summary["governance_only"] is True
assert summary["main_mutated"] is False
assert summary["release_or_production_mutated"] is False
assert summary["pointer_state"] == {
    "active_change_present": False,
    "pending_replan_present": False,
    "live_writer_count": 0,
}
assert summary["successor"]["status"] == "closed"
assert summary["successor"]["result"] == "CONVERGED"
expected_changed = changed["changed_paths"]
assert changed["status"] == "PASS"
assert expected_changed == summary["simulated_main_changed_paths"]
assert expected_changed == sorted(expected_changed)
assert ACTIVE in expected_changed
assert all(path.startswith("governance/") for path in expected_changed)
expected_added = [path for path in expected_changed if path != ACTIVE]
assert set(expected_added) == set(hashes["files"])

# Fail closed if main moved after D3. This publisher is valid only for the exact simulated main.
remote_main = run("git", "ls-remote", "origin", "refs/heads/main", capture=True)
parts = remote_main.split()
assert len(parts) == 2 and parts[1] == "refs/heads/main", remote_main
assert parts[0] == MAIN_SHA, {"expected_main": MAIN_SHA, "remote_main": parts[0]}

# Never overwrite or force-update a pre-existing reconciliation branch.
existing = run("git", "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}", capture=True)
assert existing == "", f"refusing to overwrite existing branch {BRANCH}: {existing}"

worktree = Path("/tmp/v2018-main-reconciliation-publish")
if worktree.exists():
    shutil.rmtree(worktree)
run("git", "worktree", "prune")
run("git", "worktree", "add", "--detach", str(worktree), MAIN_SHA)

active = worktree / ACTIVE
before = ARTIFACT / "predecessor-change-history/contract-before-replan.json"
assert active.is_file()
assert active.read_bytes() == before.read_bytes(), "stale main active pointer is not the D2b-bound predecessor"

for destination in expected_added:
    src = source_for(destination, ARTIFACT)
    dst = worktree / destination
    assert src.is_file(), src
    assert not dst.exists(), f"refusing to overwrite exact-main archive path: {destination}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    got = sha256(dst)
    assert got == hashes["files"][destination], (destination, got, hashes["files"][destination])

# Remove the stale live pointer only after every immutable archive record is present and hash-verified.
active.unlink()
assert not active.exists()
assert not (worktree / "governance/pending-replan.json").exists()

# Stage exactly the D3-proven set. Archive files are added explicitly. The one stale
# writer pointer is removed directly from the index with plumbing so linked-worktree
# porcelain/index flags cannot silently suppress the tracked deletion.
run("git", "add", "--", *expected_added, cwd=worktree)
run("git", "update-index", "--force-remove", "--", ACTIVE, cwd=worktree)
staged = run("git", "diff", "--cached", "--name-only", cwd=worktree, capture=True).splitlines()
staged = sorted(path for path in staged if path)
assert staged == expected_changed, {"staged": staged, "expected": expected_changed}
assert all(path.startswith("governance/") for path in staged)
assert not any(path.startswith(("services/", "deployment/", "contracts/", "web/")) for path in staged)

added = run("git", "diff", "--cached", "--diff-filter=A", "--name-only", cwd=worktree, capture=True).splitlines()
deleted = run("git", "diff", "--cached", "--diff-filter=D", "--name-only", cwd=worktree, capture=True).splitlines()
modified = run("git", "diff", "--cached", "--diff-filter=M", "--name-only", cwd=worktree, capture=True).splitlines()
assert sorted(added) == expected_added
assert deleted == [ACTIVE]
assert modified == []

run("git", "config", "user.name", "github-actions[bot]", cwd=worktree)
run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=worktree)
run(
    "git", "commit", "-m",
    "Archive V20.18 successor closure and release stale writer authority",
    cwd=worktree,
)
commit_sha = run("git", "rev-parse", "HEAD", cwd=worktree, capture=True)
parent_sha = run("git", "rev-parse", "HEAD^", cwd=worktree, capture=True)
assert parent_sha == MAIN_SHA

# Push exactly one new branch. No force and no main ref update are allowed.
run("git", "push", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=worktree)
remote_branch = run("git", "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}", capture=True)
rparts = remote_branch.split()
assert len(rparts) == 2 and rparts[0] == commit_sha and rparts[1] == f"refs/heads/{BRANCH}"
remote_main_after = run("git", "ls-remote", "origin", "refs/heads/main", capture=True).split()[0]
assert remote_main_after == MAIN_SHA, {"main_mutated": remote_main_after}

result = {
    "schema_version": 1,
    "phase": "D4-governance-reconciliation-branch-publish",
    "status": "PASS",
    "base_main_sha": MAIN_SHA,
    "main_sha_after_publish": remote_main_after,
    "branch": BRANCH,
    "commit_sha": commit_sha,
    "parent_sha": parent_sha,
    "changed_paths": staged,
    "added_paths": sorted(added),
    "deleted_paths": deleted,
    "modified_paths": modified,
    "governance_only": True,
    "main_mutated": False,
    "release_or_production_mutated": False,
    "d3_summary_sha256": sha256(D3 / "d3-reconciliation-simulation-summary.json"),
    "d3_archive_hashes_sha256": sha256(D3 / "d3-archive-hashes.json"),
}
(OUT / "d4-reconciliation-publish-summary.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=False, indent=2))
