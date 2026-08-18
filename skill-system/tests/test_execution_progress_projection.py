from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "skill-system" / "controller" / "execution_progress.py"
SPEC = importlib.util.spec_from_file_location("execution_progress", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
execution_progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(execution_progress)


def _task() -> dict:
    return {
        "task_id": "issue167-pack-a",
        "status": "WAITING_EXTERNAL_RESULT",
        "phase": "CI_CERTIFICATION",
        "metadata": {
            "local_first": {
                "counters": {
                    "local_repair_rounds": 2,
                    "local_verification_rounds": 3,
                },
                "budgets": {
                    "local_repair_rounds": 8,
                    "local_verification_rounds": 8,
                },
            }
        },
    }


def test_product_green_is_not_hidden_by_status_publication_failure() -> None:
    progress = execution_progress.build_execution_progress(
        task=_task(),
        quality_results=[
            {"id": "python-test-suites", "name": "Python tests", "status": "PASS"},
            {"id": "frontend-tests", "name": "Frontend", "status": "PASS"},
        ],
        github_jobs=[
            {
                "id": 101,
                "name": "quality-quick-execution",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 102,
                "name": "quality-quick-required-status",
                "status": "completed",
                "conclusion": "failure",
            },
        ],
        run_id=32147681365,
        workflow="quality",
        head_sha="9b651c4",
    )

    assert progress["schema"] == "execution-progress@1"
    assert progress["authority_effect"] is False
    assert progress["product_verdict"] == "PASS"
    assert progress["transport_verdict"] == "FAIL"
    assert progress["overall"] == "FAILED"
    assert progress["first_failure"]["label"] == "quality-quick-required-status"
    assert progress["loop"] == {
        "repair_round": 2,
        "max_repair_rounds": 8,
        "verification_round": 3,
        "max_verification_rounds": 8,
    }


def test_running_step_is_current_stage_and_missing_evidence_is_never_pass() -> None:
    progress = execution_progress.build_execution_progress(
        github_jobs=[
            {
                "id": 201,
                "name": "quality-quick-execution",
                "status": "in_progress",
                "conclusion": None,
            }
        ],
        github_steps=[
            {
                "number": 8,
                "name": "Create quick quality target",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "number": 9,
                "name": "Run unit, contract and frontend gates",
                "status": "in_progress",
                "conclusion": None,
            },
            {
                "number": 10,
                "name": "Upload quick evidence",
                "status": "queued",
                "conclusion": None,
            },
        ],
        github_step_job_name="quality-quick-execution",
    )

    assert progress["overall"] == "RUNNING"
    assert progress["transport_verdict"] == "RUNNING"
    assert progress["product_verdict"] == "UNKNOWN"
    assert "quality-quick-execution" in progress["current_stage"]
    statuses = {row["label"]: row["status"] for row in progress["stages"]}
    assert statuses["Run unit, contract and frontend gates"] == "RUNNING"
    assert statuses["Upload quick evidence"] == "PENDING"


def test_failed_quality_gate_is_the_product_failure_authority() -> None:
    progress = execution_progress.build_execution_progress(
        quality_results=[
            {"id": "static", "name": "Static", "status": "PASS"},
            {
                "id": "authority",
                "name": "Transaction Authority",
                "status": "FAIL",
                "stderr": "wrong-thread replay accepted",
            },
        ],
        github_jobs=[
            {
                "id": 301,
                "name": "quality-quick-execution",
                "status": "completed",
                "conclusion": "failure",
            }
        ],
    )

    assert progress["product_verdict"] == "FAIL"
    assert progress["transport_verdict"] == "FAIL"
    assert progress["first_failure"]["label"] == "Transaction Authority"
    assert progress["first_failure"]["source"] == "quality-gate"
    rendered = execution_progress.render_progress_text(progress)
    assert "✅ Static" in rendered
    assert "❌ Transaction Authority" in rendered
    assert "产品判定：FAIL" in rendered
    assert "执行/传输判定：FAIL" in rendered


def test_skipped_jobs_remain_skipped_not_pass() -> None:
    progress = execution_progress.build_execution_progress(
        github_jobs=[
            {
                "id": 401,
                "name": "quality-integration",
                "status": "completed",
                "conclusion": "skipped",
            }
        ]
    )

    assert progress["stages"][0]["status"] == "SKIPPED"
    assert "⏭️ quality-integration" in execution_progress.render_progress_text(progress)


def test_projection_is_read_only_and_does_not_claim_completion_from_task_state() -> None:
    task = {
        "task_id": "t1",
        "status": "COMPLETED",
        "phase": "COMPLETED",
        "metadata": {},
    }
    progress = execution_progress.build_execution_progress(task=task)

    assert progress["task"]["status"] == "COMPLETED"
    assert progress["overall"] == "UNKNOWN"
    assert progress["product_verdict"] == "UNKNOWN"
    assert progress["transport_verdict"] == "UNKNOWN"
    assert progress["authority_effect"] is False
