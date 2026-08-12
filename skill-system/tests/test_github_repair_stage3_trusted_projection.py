from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (str(SCRIPTS), str(CONTROL)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import github_repair_stage3_trusted_projection as projection  # noqa: E402
import trusted_judge  # noqa: E402


def _write(root: Path, rel: str, text: str, *, mode: int = 0o644) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def _judge(root: Path) -> Path:
    _write(root, "scripts/quality_loop.py", "print('current judge')\n", mode=0o755)
    _write(root, "governance/quality-loop-policy.json", '{"version": 1}\n')
    _write(root, "skill-system/controller/contract.py", "BOUND = True\n")
    trusted_judge.write_manifest(root)
    return root


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _init_git(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "stage3-test@example.invalid")
    _git(root, "config", "user.name", "Stage3 Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial judge")


def test_projection_replaces_only_manifest_owned_inputs_and_preserves_product_source(tmp_path: Path) -> None:
    judge = _judge(tmp_path / "judge")
    candidate = tmp_path / "candidate"
    product = _write(candidate, "services/agent-service/src/product.py", "PRODUCT = 'candidate'\n")
    _write(candidate, "scripts/quality_loop.py", "print('historical judge')\n", mode=0o755)
    _write(candidate, "governance/quality-loop-policy.json", '{"version": 0}\n')
    _write(candidate, "skill-system/controller/contract.py", "BOUND = False\n")
    output = tmp_path / "evidence" / "projection.json"

    payload = projection.project(candidate_root=candidate, judge_root=judge, output_path=output)

    assert payload["status"] == "PROJECTED"
    assert payload["projected_file_count"] == 3
    assert payload["judge_manifest_refreshed_from_clean_bound_sha"] is None
    assert payload["repair_patch_changed"] is False
    assert payload["candidate_commit_changed"] is False
    assert payload["publication_authority_changed"] is False
    assert payload["production_closed"] is False
    assert trusted_judge.verify_candidate(candidate, judge) == []
    assert product.read_text(encoding="utf-8") == "PRODUCT = 'candidate'\n"
    assert json.loads(output.read_text(encoding="utf-8"))["judge_manifest_sha256"] == payload["judge_manifest_sha256"]
    for row in payload["projected_files"]:
        mode = stat.S_IMODE((candidate / row["path"]).stat().st_mode)
        assert mode & 0o222 == 0


def test_projection_fails_closed_if_bound_judge_changes_after_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = _judge(tmp_path / "judge")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (judge / "scripts/quality_loop.py").write_text("tampered\n", encoding="utf-8")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    with pytest.raises(projection.ProjectionError, match="checked-in manifest is stale"):
        projection.project(
            candidate_root=candidate,
            judge_root=judge,
            output_path=tmp_path / "projection.json",
        )


def test_ci_projection_refreshes_stale_manifest_only_from_clean_exact_git_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = _judge(tmp_path / "judge")
    _init_git(judge)
    # Simulate a later trusted-control commit whose derived manifest was not regenerated.
    quality_loop = judge / "scripts/quality_loop.py"
    quality_loop.write_text("print('new bound judge')\n", encoding="utf-8")
    _git(judge, "add", "scripts/quality_loop.py")
    _git(judge, "commit", "-m", "move trusted judge source")
    head_sha = _git(judge, "rev-parse", "HEAD")
    assert trusted_judge.verify_root(judge)
    assert _git(judge, "status", "--porcelain=v1", "--untracked-files=all") == ""

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    payload = projection.project(
        candidate_root=candidate,
        judge_root=judge,
        output_path=tmp_path / "projection.json",
    )

    assert payload["judge_manifest_refreshed_from_clean_bound_sha"] == head_sha
    assert trusted_judge.verify_root(judge) == []
    assert trusted_judge.verify_candidate(candidate, judge) == []
    assert stat.S_IMODE((judge / trusted_judge.MANIFEST_REL).stat().st_mode) & 0o222 == 0
    for row in payload["projected_files"]:
        assert stat.S_IMODE((judge / row["path"]).stat().st_mode) & 0o222 == 0


def test_ci_projection_refuses_to_refresh_dirty_bound_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = _judge(tmp_path / "judge")
    _init_git(judge)
    (judge / "scripts/quality_loop.py").write_text("dirty trusted source\n", encoding="utf-8")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    with pytest.raises(projection.ProjectionError, match="checkout is not clean"):
        projection.project(
            candidate_root=candidate,
            judge_root=judge,
            output_path=tmp_path / "projection.json",
        )


def test_projection_requires_independent_candidate_and_judge_roots(tmp_path: Path) -> None:
    judge = _judge(tmp_path / "judge")
    candidate = judge / "nested-candidate"
    candidate.mkdir()

    with pytest.raises(projection.ProjectionError, match="independent workspaces"):
        projection.project(
            candidate_root=candidate,
            judge_root=judge,
            output_path=tmp_path / "projection.json",
        )
