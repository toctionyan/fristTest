from __future__ import annotations

import json
import stat
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


def test_projection_exports_runtime_bundle_and_preserves_product_source(
    tmp_path: Path, monkeypatch
) -> None:
    judge = _judge(tmp_path / "judge")
    bundle = tmp_path / "runtime-judge"
    monkeypatch.setenv(projection.BUNDLE_ENV, str(bundle))
    candidate = tmp_path / "candidate"
    product = _write(candidate, "services/agent-service/src/product.py", "PRODUCT = 'candidate'\n")
    _write(candidate, "scripts/quality_loop.py", "print('historical judge')\n", mode=0o755)
    _write(candidate, "governance/quality-loop-policy.json", '{"version": 0}\n')
    _write(candidate, "skill-system/controller/contract.py", "BOUND = False\n")
    output = tmp_path / "evidence" / "projection.json"

    payload = projection.project(candidate_root=candidate, judge_root=judge, output_path=output)

    assert payload["status"] == "PROJECTED"
    assert payload["manifest_source"] == "runtime-export-from-bound-control"
    assert payload["control_root"] == str(judge.resolve())
    assert payload["judge_root"] == str(bundle.resolve())
    assert payload["projected_file_count"] == 3
    assert payload["repair_patch_changed"] is False
    assert payload["candidate_commit_changed"] is False
    assert payload["publication_authority_changed"] is False
    assert payload["production_closed"] is False
    assert trusted_judge.verify_root(bundle) == []
    assert trusted_judge.verify_candidate(candidate, bundle) == []
    assert product.read_text(encoding="utf-8") == "PRODUCT = 'candidate'\n"
    assert json.loads(output.read_text(encoding="utf-8"))["judge_manifest_sha256"] == payload["judge_manifest_sha256"]
    for row in payload["projected_files"]:
        mode = stat.S_IMODE((candidate / row["path"]).stat().st_mode)
        assert mode & 0o222 == 0
        bundle_mode = stat.S_IMODE((bundle / row["path"]).stat().st_mode)
        assert bundle_mode & 0o222 == 0


def test_projection_rebuilds_runtime_manifest_when_checked_in_manifest_is_stale(
    tmp_path: Path, monkeypatch
) -> None:
    judge = _judge(tmp_path / "judge")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    bundle = tmp_path / "runtime-judge"
    monkeypatch.setenv(projection.BUNDLE_ENV, str(bundle))
    current = judge / "scripts/quality_loop.py"
    current.chmod(0o755)
    current.write_text("print('new bound control')\n", encoding="utf-8")
    current.chmod(0o755)

    assert "fingerprint_mismatch:scripts/quality_loop.py" in trusted_judge.verify_root(judge)
    payload = projection.project(
        candidate_root=candidate,
        judge_root=judge,
        output_path=tmp_path / "projection.json",
    )

    assert payload["status"] == "PROJECTED"
    assert trusted_judge.verify_root(bundle) == []
    assert trusted_judge.verify_candidate(candidate, bundle) == []
    assert (candidate / "scripts/quality_loop.py").read_text(encoding="utf-8") == "print('new bound control')\n"


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


def test_projection_rejects_bundle_inside_bound_control(tmp_path: Path, monkeypatch) -> None:
    judge = _judge(tmp_path / "judge")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    monkeypatch.setenv(projection.BUNDLE_ENV, str(judge / "runtime-bundle"))

    with pytest.raises(projection.ProjectionError, match="independent workspaces"):
        projection.project(
            candidate_root=candidate,
            judge_root=judge,
            output_path=tmp_path / "projection.json",
        )
