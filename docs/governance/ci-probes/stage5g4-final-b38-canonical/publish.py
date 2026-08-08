from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

BASE = "e0e04d51e9da9790bef7bd0482584f60b8e975a9"
BRANCH = "agent/b38-context-reference-goal-coverage-canonical-20260808"
ARTIFACT_ID = 9016338327
ARTIFACT_SHA256 = "ad12a58481c1705f6f79a7bafc765a6a307e29f6b91fd05b1dcd1b0f315039f8"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(source: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(source), *args], text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git failed").strip())
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    source = Path(args.source).resolve()
    artifact_root = Path(args.artifact_root).resolve()
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest["files"]
    assert manifest["base_sha"] == BASE
    assert manifest["canonical_branch"] == BRANCH
    assert len(rows) == 30 and len({row["path"] for row in rows}) == 30
    assert git(source, "rev-parse", "HEAD").stdout.strip() == BASE

    remote = git(source, "ls-remote", "--exit-code", "origin", f"refs/heads/{BRANCH}", check=False)
    if remote.returncode == 0:
        raise RuntimeError("canonical target branch already exists; refusing overwrite")
    if remote.returncode not in {2}:
        raise RuntimeError((remote.stderr or remote.stdout or "unexpected ls-remote result").strip())

    ops: list[dict[str, str]] = []
    expected_paths = sorted(str(row["path"]) for row in rows)
    for row in rows:
        rel = str(row["path"])
        expected = str(row["sha256"])
        src = artifact_root / rel
        dst = source / rel
        assert src.is_file(), rel
        assert sha256(src) == expected, (rel, sha256(src), expected)
        existed = git(source, "cat-file", "-e", f"{BASE}:{rel}", check=False).returncode == 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        assert sha256(dst) == expected
        ops.append({"path": rel, "operation": "REPLACE" if existed else "CREATE", "sha256": expected})

    assert sum(row["operation"] == "CREATE" for row in ops) == 9
    assert sum(row["operation"] == "REPLACE" for row in ops) == 21

    git(source, "add", "--", *expected_paths)
    staged = git(source, "diff", "--cached", "--name-status", "--no-renames", BASE, "--").stdout.splitlines()
    status_map = {line.split("\t", 1)[1]: line.split("\t", 1)[0] for line in staged}
    assert sorted(status_map) == expected_paths, {"actual": sorted(status_map), "expected": expected_paths}
    for row in ops:
        assert status_map[row["path"]] == ("A" if row["operation"] == "CREATE" else "M"), (row, status_map.get(row["path"]))
    assert not [line for line in git(source, "status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines() if line.strip() and line[3:] not in expected_paths]

    precommit = {
        "schema_version": 1,
        "status": "PRECOMMIT_EXACT_STAGED_SCOPE_PASS",
        "base_sha": BASE,
        "install_artifact_id": ARTIFACT_ID,
        "install_artifact_sha256": ARTIFACT_SHA256,
        "canonical_branch": BRANCH,
        "path_count": 30,
        "create_count": 9,
        "replace_count": 21,
        "delete_count": 0,
        "files": ops,
        "manifest_sha256": sha256(manifest_path),
    }
    Path("canonical-publication-precommit.json").write_text(json.dumps(precommit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    git(source, "config", "user.name", "Stage5G4 Canonical Publisher")
    git(source, "config", "user.email", "stage5g4-canonical@users.noreply.github.com")
    git(source, "switch", "-c", BRANCH)
    git(source, "commit", "-m", "B38: context reference goal coverage")
    head = git(source, "rev-parse", "HEAD").stdout.strip()
    parent = git(source, "rev-parse", "HEAD^").stdout.strip()
    assert parent == BASE
    changed = git(source, "diff", "--name-only", "--no-renames", BASE, head, "--").stdout.splitlines()
    assert sorted(changed) == expected_paths and len(changed) == 30
    for row in rows:
        blob = subprocess.check_output(["git", "-C", str(source), "show", f"{head}:{row['path']}"])
        assert hashlib.sha256(blob).hexdigest() == row["sha256"], row["path"]

    report = dict(precommit)
    report.update({"status": "CANONICAL_COMMIT_EXACT_SCOPE_PASS", "canonical_commit": head, "parent_sha": parent})
    Path("canonical-publication-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    git(source, "push", "origin", f"HEAD:refs/heads/{BRANCH}")
    remote_head = git(source, "ls-remote", "origin", f"refs/heads/{BRANCH}").stdout.split()[0]
    assert remote_head == head
    Path("canonical-b38-head.txt").write_text(head + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
