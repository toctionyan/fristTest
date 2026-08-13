from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
STAGE2 = ROOT / ".github" / "workflows" / "governed-ci-repair-stage2.yml"
LOCAL_LOOP = ROOT / "scripts" / "local_first_loop.py"
LOCAL_GOVERNANCE = ROOT / "skill-system" / "controller" / "local_first_governance.py"
RETIRED_AUTO_WORKFLOW = ROOT / ".github" / "workflows" / "governed-ci-stage2-auto-handoff.yml"
RETIRED_AUTO_CONTROLLER = ROOT / "scripts" / "github_stage2_auto_handoff.py"


def test_governance_declares_single_authority_cutover_as_hard_rule() -> None:
    text = AGENTS.read_text(encoding="utf-8")
    required = (
        "## Single-authority cutover rule",
        "exactly one authoritative owner and one live writer",
        "A replacement is a **cutover**, never a permanent old/new coexistence",
        "Historical data compatibility is allowed; historical **decision authority** is not.",
        "No old/new dual writers, dual repair controllers, parallel completion authorities",
        "local_first_governance.py` is the default writable repair-lane authority",
        "GitHub CI is a clean-room verifier",
        "must never be automatically entered by a normal CI failure",
    )
    missing = [fragment for fragment in required if fragment not in text]
    assert not missing, f"single-authority governance is incomplete: {missing}"


def test_retired_automatic_remote_repair_authority_is_deleted() -> None:
    assert not RETIRED_AUTO_WORKFLOW.exists()
    assert not RETIRED_AUTO_CONTROLLER.exists()


def test_local_controller_has_no_second_remote_approval_writer() -> None:
    cli = LOCAL_LOOP.read_text(encoding="utf-8")
    governance = LOCAL_GOVERNANCE.read_text(encoding="utf-8")
    assert "approve-remote-repair" not in cli
    assert "approve_remote_repair" not in cli
    assert "approve_remote_repair" not in governance
    assert "REMOTE_REPAIR_APPROVAL_VALUES" not in governance
    assert '"remote_repair"' not in governance
    assert '"remote_fallback_activation": "manual-workflow-dispatch-only"' in governance


def test_remote_stage2_has_no_automatic_initial_trigger() -> None:
    text = STAGE2.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "workflow_run:" not in text
    assert "schedule:" not in text
    assert "push:" not in text
    assert "remote_repair_approval:" in text
    assert '"${REMOTE_REPAIR_APPROVAL}" != "explicitly-approved"' in text


def test_remote_fallback_cannot_become_a_peer_default_writer() -> None:
    text = STAGE2.read_text(encoding="utf-8")
    assert "Normal CI code failures must return to the local Patch Owner instead." in text
    assert "This workflow is a manually entered fallback, not the default CI repair lane." in text
    assert "production_closed: true" not in text
    assert "gh pr create" not in text
    assert "git push" not in text
