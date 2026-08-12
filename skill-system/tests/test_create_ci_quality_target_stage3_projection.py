from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "create_ci_quality_target.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("create_ci_quality_target_stage3_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_stage3_target_projects_bound_judge_and_records_minimal_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_module()
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    claims_source = workspace / "claims.json"
    claims_source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_id": "source-quick",
                "claims": [
                    {
                        "id": "Q-1",
                        "required_mode": "quick",
                        "closure_requirement": "current-pass",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = workspace / ".quality" / "targets" / "stage3.md"
    calls: list[tuple[Path, Path, Path]] = []

    def fake_project(*, candidate_root: Path, judge_root: Path, output_path: Path):
        calls.append((candidate_root, judge_root, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{}\n", encoding="utf-8")
        return {
            "schema": "github-stage3-trusted-judge-projection@1",
            "judge_manifest_sha256": "a" * 64,
            "projected_file_count": 17,
        }

    monkeypatch.setattr(module, "project_stage3_trusted_judge", fake_project)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path / "workflow"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--output",
            str(output),
            "--ref",
            "b" * 40,
            "--workflow",
            "governed-ci-repair-stage3",
            "--claims-source",
            "claims.json",
        ],
    )

    assert module.main() == 0
    assert len(calls) == 1
    candidate_root, judge_root, projection_path = calls[0]
    assert candidate_root == workspace.resolve()
    assert judge_root == module.CONTROL_ROOT
    assert projection_path == (tmp_path / "workflow" / "stage3-evidence" / "trusted-judge-projection.json").resolve()

    generated = json.loads(output.with_suffix(".claims.json").read_text(encoding="utf-8"))
    metadata = generated["trusted_judge_projection"]
    assert metadata == {
        "schema": "github-stage3-trusted-judge-projection@1",
        "judge_manifest_sha256": "a" * 64,
        "projected_file_count": 17,
        "repair_patch_changed": False,
        "candidate_commit_changed": False,
        "publication_authority_changed": False,
        "production_closed": False,
    }


def test_non_stage3_target_does_not_project_trusted_judge(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    workspace = tmp_path / "candidate"
    workspace.mkdir()
    output = workspace / "quick.md"

    def forbidden_project(**_kwargs):
        raise AssertionError("ordinary Quality target must not project Stage3 Judge inputs")

    monkeypatch.setattr(module, "project_stage3_trusted_judge", forbidden_project)
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--output",
            str(output),
            "--ref",
            "c" * 40,
            "--workflow",
            "quality-quick",
        ],
    )

    assert module.main() == 0
    generated = json.loads(output.with_suffix(".claims.json").read_text(encoding="utf-8"))
    assert "trusted_judge_projection" not in generated
