from __future__ import annotations

import json
from pathlib import Path

import yaml


def _workspace() -> Path:
    return Path(__file__).resolve().parents[4]


def test_production_request_dispatcher_is_protected_main_only() -> None:
    root = _workspace()
    workflow_path = root / ".github/workflows/production-certification-request.yml"
    payload = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert payload["on"] == {
        "push": {
            "branches": ["main"],
            "paths": [".github/production-certification-request.json"],
        }
    }
    assert payload["permissions"] == {
        "actions": "write",
        "contents": "read",
        "issues": "write",
    }
    assert payload["concurrency"] == {
        "group": "protected-production-certification-request-main",
        "cancel-in-progress": False,
    }
    job = payload["jobs"]["dispatch"]
    assert job["if"] == "github.ref == 'refs/heads/main' && github.ref_protected == true"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 10

    text = workflow_path.read_text(encoding="utf-8")
    assert "actions/workflows/release.yml/dispatches" in text
    assert '"ref": "main"' in text
    assert 'current_main != event_sha' in text
    assert 'provider not in {"openai", "deepseek"}' in text
    assert "pull_request_target" not in text
    assert "secrets." not in text


def test_production_request_ledger_is_explicit_and_bounded() -> None:
    request_path = _workspace() / ".github/production-certification-request.json"
    payload = json.loads(request_path.read_text(encoding="utf-8"))

    assert set(payload) == {
        "schema_version",
        "request_id",
        "provider",
        "model",
        "embedding_model",
        "embedding_dimension",
        "comment_issue",
    }
    assert payload["schema_version"] == 1
    assert payload["request_id"] == "production-certification-20260801-001"
    assert payload["provider"] == "deepseek"
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["embedding_model"] == "text-embedding-v4"
    assert payload["embedding_dimension"] == "1024"
    assert payload["comment_issue"] == 7
