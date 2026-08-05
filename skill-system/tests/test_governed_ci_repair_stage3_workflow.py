from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"
STAGE3 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage3.yml"


def test_stage3_is_event_driven_and_independently_validates_before_publication() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    assert "governed-ci-repair-stage2" in text
    assert "workflow_run:" in text
    assert "github_repair_stage3.py inspect" in text
    assert "github_repair_stage3.py prepare" in text
    assert "github_repair_stage3.py targeted" in text
    assert "--mode quick" in text
    assert "github_repair_stage3.py validate" in text
    assert "github_repair_stage3_complete.py" in text
    assert "gh pr create --draft" in text
    assert "gh workflow run quality.yml" in text


def test_stage3_has_no_model_or_production_secret_access() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    assert "environment: production-certification" not in text
    assert "PRODUCTION_MODEL_API_KEY" not in text
    assert "PRODUCTION_EMBEDDING_API_KEY" not in text
    assert "QUALITY_EVIDENCE_SIGNING_KEY" not in text
    assert "merge_pull_request" not in text
    assert "gh pr merge" not in text
    assert "production_closed: false" in text


def test_stage3_rejects_stale_base_and_never_force_pushes() -> None:
    text = STAGE3.read_text(encoding="utf-8")
    assert '"${base_sha}" != "${SOURCE_HEAD_SHA}"' in text
    assert "already points to different evidence" in text
    assert "--force" not in text
    assert "--force-with-lease" not in text


def test_stage2_binds_handoff_and_blocks_recursive_repair() -> None:
    text = STAGE2.read_text(encoding="utf-8")
    assert "github_stage2_handoff.py" in text
    assert 'startswith("governed-repair/")' in text
    assert "and not" in text
