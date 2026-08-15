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
ACTIVE_BLOB = "a39534b47bd2776abf17359e6efbf21b264111a6"


def run(*args: str, cwd: Path = REPO, capture: bool = False, env: dict[str, str] | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(
        list(args), cwd=cwd, check=True, text=True, env=merged,
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
assert summary["pointer_state"] == {"active_change_present": False, "pending_replan_present": False, "live_writer_count": 0}
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

# Bind publication to the exact simulated main and a fresh, absent target branch.
remote_main = run("git", "ls-remote", "origin", "refs/heads/main", capture=True)
parts = remote_main.split()
assert len(parts) == 2 and parts[1] == "refs/heads/main", remote_main
assert parts[0] == MAIN_SHA, {"expected_main": MAIN_SHA, "remote_main": parts[0]}
existing = run("git", "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}", capture=True)
assert existing == "", f"refusing to overwrite existing branch {BRANCH}: {existing}"

# Prove the exact-main tree contains the stale tracked pointer with the expected blob.
active_tree_line = run("git", "ls-tree", MAIN_SHA, "--", ACTIVE, capture=True)
assert active_tree_line, "exact main no longer tracks active-change"
fields = active_tree_line.split()
assert fields[0] == "100644" and fields[1] == "blob" and fields[2] == ACTIVE_BLOB, active_tree_line

# Construct a new tree from exact main using a dedicated temporary index. This avoids
# the runner's anomalous cached-diff reporting while still relying on Git's tree writer.
index = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "v2018-d4f-index"
index.unlink(missing_ok=True)
idx_env = {"GIT_INDEX_FILE": str(index)}
run("git", "read-tree", MAIN_SHA, env=idx_env)

blob_shas: dict[str, str] = {}
for destination in expected_added:
    src = source_for(destination, ARTIFACT)
    assert src.is_file(), src
    got = sha256(src)
    assert got == hashes["files"][destination], (destination, got, hashes["files"][destination])
    blob_sha = run("git", "hash-object", "-w", str(src), capture=True)
    assert len(blob_sha) == 40
    blob_shas[destination] = blob_sha
    run("git", "update-index", "--add", "--cacheinfo", f"100644,{blob_sha},{destination}", env=idx_env)

run("git", "update-index", "--force-remove", "--", ACTIVE, env=idx_env)
new_tree = run("git", "write-tree", capture=True, env=idx_env)
assert len(new_tree) == 40

# Validate the final tree directly; no porcelain cached diff participates in authority.
assert run("git", "ls-tree", new_tree, "--", ACTIVE, capture=True) == "", "new tree still contains stale active pointer"
for destination, blob_sha in blob_shas.items():
    line = run("git", "ls-tree", new_tree, "--", destination, capture=True)
    f = line.split()
    assert f[0] == "100644" and f[1] == "blob" and f[2] == blob_sha, (destination, line, blob_sha)
assert run("git", "ls-tree", new_tree, "--", "governance/pending-replan.json", capture=True) == ""

# Create a commit object only after the tree has been fully proven. Creating the object
# mutates no ref and therefore cannot affect main.
commit_env = {
    "GIT_AUTHOR_NAME": "github-actions[bot]",
    "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "github-actions[bot]",
    "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
}
commit_sha = run(
    "git", "commit-tree", new_tree, "-p", MAIN_SHA,
    "-m", "Archive V20.18 successor closure and release stale writer authority",
    capture=True, env=commit_env,
)
assert len(commit_sha) == 40
assert run("git", "rev-parse", f"{commit_sha}^", capture=True) == MAIN_SHA

# Verify the commit tree delta, including the deletion, independently of the temporary index.
name_status = run("git", "diff-tree", "--no-commit-id", "--name-status", "-r", MAIN_SHA, commit_sha, capture=True)
rows = [line.split("\t", 1) for line in name_status.splitlines() if line]
actual_paths = sorted(path for status, path in rows)
assert actual_paths == expected_changed, {"actual": actual_paths, "expected": expected_changed, "rows": rows}
status_by_path = {path: status for status, path in rows}
assert status_by_path[ACTIVE] == "D", status_by_path
assert all(status_by_path[path] == "A" for path in expected_added), status_by_path
assert all(path.startswith("governance/") for path in actual_paths)
assert not any(path.startswith(("services/", "deployment/", "contracts/", "web/")) for path in actual_paths)

# Recheck remote state immediately before the only ref mutation.
remote_main_before_push = run("git", "ls-remote", "origin", "refs/heads/main", capture=True).split()[0]
assert remote_main_before_push == MAIN_SHA
existing_before_push = run("git", "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}", capture=True)
assert existing_before_push == ""

# Push exactly one new branch; never update or force main.
run("git", "push", "origin", f"{commit_sha}:refs/heads/{BRANCH}")
remote_branch = run("git", "ls-remote", "--heads", "origin", f"refs/heads/{BRANCH}", capture=True)
rparts = remote_branch.split()
assert len(rparts) == 2 and rparts[0] == commit_sha and rparts[1] == f"refs/heads/{BRANCH}"
remote_main_after = run("git", "ls-remote", "origin", "refs/heads/main", capture=True).split()[0]
assert remote_main_after == MAIN_SHA, {"main_mutated": remote_main_after}

result = {
    "schema_version": 1,
    "phase": "D4f-governance-reconciliation-tree-plumbing-publish",
    "status": "PASS",
    "base_main_sha": MAIN_SHA,
    "main_sha_after_publish": remote_main_after,
    "branch": BRANCH,
    "commit_sha": commit_sha,
    "parent_sha": MAIN_SHA,
    "base_active_blob": ACTIVE_BLOB,
    "tree_sha": new_tree,
    "changed_paths": actual_paths,
    "added_paths": expected_added,
    "deleted_paths": [ACTIVE],
    "modified_paths": [],
    "governance_only": True,
    "main_mutated": False,
    "release_or_production_mutated": False,
    "d3_summary_sha256": sha256(D3 / "d3-reconciliation-simulation-summary.json"),
    "d3_archive_hashes_sha256": sha256(D3 / "d3-archive-hashes.json"),
}
(OUT / "d4-reconciliation-publish-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
