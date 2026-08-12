from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "skill-system" / "controller"
SCRIPTS = ROOT / "scripts"
for entry in (str(CONTROL), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from task_run import TaskRunStore, stable_task_id

SCRIPT = ROOT / "scripts" / "github_repair_orchestrator.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_repair_orchestrator", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=path, text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def _fixture(tmp_path: Path):
    workspace = tmp_path / "candidate"
    source = workspace / "services" / "a.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 0\n", encoding="utf-8")
    _git(workspace, "init")
    _git(workspace, "config", "user.name", "test")
    _git(workspace, "config", "user.email", "test@example.invalid")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "baseline")
    head_sha = _git(workspace, "rev-parse", "HEAD")
    failure = {
        "schema": "github-failure-ingest@1",
        "status": "INGESTED",
        "repository": "owner/repo",
        "workflow_name": "quality",
        "workflow_run_id": "123",
        "workflow_run_attempt": "1",
        "head_sha": head_sha,
        "failure_signature": "f" * 64,
        "classification": "code_or_contract",
        "same_repository": True,
        "repair_allowed": True,
        "candidate_paths": ["services/a.py"],
        "failure_summary": "services/a.py failed",
    }
    failure_path = tmp_path / "failure-case.json"
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    binding = {
        "repository": failure["repository"],
        "workflow_name": failure["workflow_name"],
        "workflow_run_id": failure["workflow_run_id"],
        "workflow_run_attempt": failure["workflow_run_attempt"],
        "head_sha": failure["head_sha"],
        "failure_signature": failure["failure_signature"],
    }
    task_path = tmp_path / "task-run.json"
    task = TaskRunStore.open_or_create(
        task_path,
        task_id=stable_task_id("github-repair", binding),
        task_kind="github-governed-repair",
        binding=binding,
        required_conditions=(
            "failure_ingested",
            "classification_complete",
            "source_changed",
            "validation_passed",
            "draft_pr_published",
        ),
    )
    task.checkpoint(status="RUNNING", phase="FAILURE_INGESTED", workspace_fingerprint=None, evidence_refs=[str(failure_path)])
    task.mark_condition("failure_ingested", evidence_refs=[str(failure_path)])
    task.mark_condition("classification_complete", evidence_refs=["classification:code_or_contract"])
    task.checkpoint(status="WAITING_EXTERNAL_RESULT", phase="REPAIR_READY", workspace_fingerprint=None, evidence_refs=[str(failure_path)])
    return workspace, source, failure_path, task_path


def _config():
    return MODULE.ModelConfig("openai", "model", "https://api.openai.com/v1", "not-used")


def test_changed_paths_preserves_first_character_for_unstaged_tracked_change(tmp_path: Path) -> None:
    workspace, source, _failure_path, _task_path = _fixture(tmp_path)
    source.write_text("x = 1\n", encoding="utf-8")

    assert MODULE._changed_paths(workspace) == ("services/a.py",)


def test_stage2_creates_local_patch_but_never_completes_full_task(tmp_path: Path, monkeypatch) -> None:
    workspace, source, failure_path, task_path = _fixture(tmp_path)

    def fake_round(**_kwargs):
        source.write_text("x = 1\n", encoding="utf-8")
        return {
            "cycle": 1,
            "summary": "fixed",
            "changed_paths": ["services/a.py"],
            "verification_passed": True,
            "verification": [{"path": "services/a.py", "passed": True, "diagnostic": "ok"}],
            "result_fingerprint": "r1",
        }

    monkeypatch.setattr(MODULE, "repair_round", fake_round)
    evidence = tmp_path / "evidence"
    result = MODULE.run_stage2(
        workspace=workspace,
        failure_case_path=failure_path,
        task_run_path=task_path,
        evidence_root=evidence,
        max_cycles=8,
        config=_config(),
    )
    assert result == 0
    payload = json.loads((evidence / "repair-result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "REPAIR_CANDIDATE_READY"
    assert payload["full_validation_passed"] is False
    assert payload["draft_pr_published"] is False
    assert (evidence / "repair.patch").is_file()
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert task["status"] == "WAITING_EXTERNAL_RESULT"
    assert task["phase"] == "STAGE3_VALIDATION_REQUIRED"
    assert task["conditions"]["source_changed"]["satisfied"] is True
    assert task["conditions"]["validation_passed"]["satisfied"] is False


def test_stage2_blocks_any_out_of_scope_change(tmp_path: Path, monkeypatch) -> None:
    workspace, source, failure_path, task_path = _fixture(tmp_path)

    def fake_round(**_kwargs):
        source.write_text("x = 1\n", encoding="utf-8")
        (workspace / "unexpected.py").write_text("bad = 1\n", encoding="utf-8")
        return {
            "cycle": 1,
            "summary": "bad scope",
            "changed_paths": ["services/a.py"],
            "verification_passed": True,
            "verification": [],
            "result_fingerprint": "r2",
        }

    monkeypatch.setattr(MODULE, "repair_round", fake_round)
    evidence = tmp_path / "evidence"
    result = MODULE.run_stage2(
        workspace=workspace,
        failure_case_path=failure_path,
        task_run_path=task_path,
        evidence_root=evidence,
        max_cycles=8,
        config=_config(),
    )
    assert result == 2
    payload = json.loads((evidence / "repair-result.json").read_text(encoding="utf-8"))
    assert payload["code"] == "STAGE2_SCOPE_VIOLATION"
    assert payload["draft_pr_published"] is False


def test_repeated_deterministic_failure_stops_before_cycle_budget(tmp_path: Path, monkeypatch) -> None:
    workspace, source, failure_path, task_path = _fixture(tmp_path)
    counter = {"value": 0}

    def fake_round(**_kwargs):
        counter["value"] += 1
        source.write_text(f"x = {counter['value']}\n", encoding="utf-8")
        return {
            "cycle": counter["value"],
            "summary": "still failing",
            "changed_paths": ["services/a.py"],
            "verification_passed": False,
            "verification": [{"path": "services/a.py", "passed": False, "diagnostic": "same failure"}],
            "result_fingerprint": f"r{counter['value']}",
        }

    monkeypatch.setattr(MODULE, "repair_round", fake_round)
    evidence = tmp_path / "evidence"
    result = MODULE.run_stage2(
        workspace=workspace,
        failure_case_path=failure_path,
        task_run_path=task_path,
        evidence_root=evidence,
        max_cycles=8,
        config=_config(),
    )
    assert result == 2
    assert counter["value"] == 2
    payload = json.loads((evidence / "repair-result.json").read_text(encoding="utf-8"))
    assert payload["code"] == "STAGE2_REPEATED_FAILURE"
