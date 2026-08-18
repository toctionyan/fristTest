from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QUALITY = ROOT / ".github" / "workflows" / "quality.yml"


def test_required_quality_status_is_bound_to_stable_pr_head_after_merge_snapshot_passes():
    text = QUALITY.read_text(encoding="utf-8")

    assert "  quality-quick-execution:\n" in text
    assert "  quality-quick-required-status:\n" in text
    assert "  quality-quick:\n" not in text
    assert "statuses: write" in text
    assert 'PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}' in text
    assert 'PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}' in text
    assert 'TEST_MERGE_SHA: ${{ github.sha }}' in text
    assert 'repos/${GITHUB_REPOSITORY}/git/commits/${TEST_MERGE_SHA}' in text
    assert '.parents[0].sha == $base' in text
    assert '.parents[1].sha == $head' in text
    assert 'repos/${GITHUB_REPOSITORY}/statuses/${PR_HEAD_SHA}' in text
    assert '--arg context "quality-quick"' in text
    assert 'STATIC_RESULT: ${{ needs.quality-static.result }}' in text
    assert 'QUICK_RESULT: ${{ needs.quality-quick-execution.result }}' in text
    assert '[[ "${state}" == "success" ]]' in text


def test_required_quality_status_does_not_create_merge_or_release_authority():
    text = QUALITY.read_text(encoding="utf-8")
    status_block = text.split("  quality-quick-required-status:\n", 1)[1].split(
        "\n  quality-integration:\n", 1
    )[0]

    assert "statuses: write" in status_block
    assert "contents: write" not in status_block
    assert "actions: write" not in status_block
    assert "/pulls/" not in status_block
    assert "/merge" not in status_block
    assert "release.yml" not in status_block
