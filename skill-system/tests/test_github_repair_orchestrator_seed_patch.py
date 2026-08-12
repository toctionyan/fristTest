from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTROL = ROOT / "skill-system" / "controller"
for entry in (str(SCRIPTS), str(CONTROL)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import github_repair_orchestrator as orchestrator  # noqa: E402
from task_run import TaskRunStore, stable_task_id  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def test_seed_patch_replays_prior_candidate_without_expanding_scope(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    path = repo / "services/agent-service/src/agent_core/example.py"
    path.parent.mkdir(parents=True)
    path.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    path.write_text("VALUE = 2\n", encoding="utf-8")
    patch = tmp_path / "seed.patch"
    patch.write_text(_git(repo, "diff", "--binary") + "\n", encoding="utf-8")
    _git(repo, "reset", "--hard", "HEAD")

    applied, digest = orchestrator._apply_seed_patch(
        workspace=repo,
        seed_patch_path=patch,
        allowed_paths=("services/agent-service/src/agent_core/example.py",),
    )
    assert applied is True
    assert digest
    assert path.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert orchestrator._changed_paths(repo) == (
        "services/agent-service/src/agent_core/example.py",
    )


def test_repair_loop_metadata_keeps_verification_attempt_and_advances_only_requested_round(
    tmp_path: Path,
) -> None:
    binding = {
        "repository": "acme/repo",
        "workflow_name": "quality",
        "workflow_run_id": "123",
        "workflow_run_attempt": "1",
        "head_sha": "a" * 40,
        "failure_signature": "b" * 64,
    }
    task = TaskRunStore.open_or_create(
        tmp_path / "task-run.json",
        task_id=stable_task_id("github-repair", binding),
        task_kind="github-governed-repair",
        binding=binding,
        required_conditions=("failure_ingested",),
    )
    task.set_metadata(
        repair_loop={
            "schema": "github-governed-repair-loop@1",
            "repair_round": 1,
            "verification_attempt": 5,
            "failure_class": "PRODUCT_SOURCE_FAILURE",
        }
    )
    updated = orchestrator._update_repair_loop_metadata(
        task,
        repair_round_number=2,
        max_repair_rounds=8,
    )
    assert updated["repair_round"] == 2
    assert updated["verification_attempt"] == 5
    assert updated["max_repair_rounds"] == 8
    assert updated["phase"] == "STAGE2_REPAIRING"
