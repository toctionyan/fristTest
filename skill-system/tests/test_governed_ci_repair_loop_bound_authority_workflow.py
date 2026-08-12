from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COORDINATOR = ROOT / ".github" / "workflows" / "governed-ci-repair-loop-coordinator.yml"


def test_outer_loop_prefers_exact_stage2_bound_source_authority() -> None:
    text = COORDINATOR.read_text(encoding="utf-8")

    assert '(.source_failure_authority | type) == "object"' in text
    assert 'original_failure="incoming/stage2-source-authority.json"' in text
    assert "jq -S '.source_failure_authority'" in text
    assert "The Python controller validates its digest and all" in text
    assert '--original-failure-case "${{ steps.evidence.outputs.original_failure }}"' in text


def test_legacy_stage2_keeps_fail_closed_stage1_artifact_lookup() -> None:
    text = COORDINATOR.read_text(encoding="utf-8")

    assert "Legacy Stage-2 artifacts did not carry a bound authority snapshot" in text
    assert 'governed-ci-quality-stage1-${source_run_id}' in text
    assert '[[ "${stage1_artifact_id}" =~ ^[1-9][0-9]*$ ]]' in text
    assert '[[ -n "${original_failure}" && -s "${original_failure}" ]]' in text


def test_bound_authority_path_does_not_expand_write_or_release_authority() -> None:
    text = COORDINATOR.read_text(encoding="utf-8")

    assert "contents: write" not in text
    assert "PRODUCTION_MODEL_API_KEY" not in text
    assert "production_closed: false" in text
