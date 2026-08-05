from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_repair_validation.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_repair_validation", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def test_deterministic_overlay_excludes_only_real_model_browser_gates(tmp_path: Path) -> None:
    source = tmp_path / "policy.json"
    output = tmp_path / "overlay.json"
    source.write_text(
        json.dumps(
            {
                "steps": [
                    {"id": "base", "modes": ["static"], "depends_on": []},
                    {
                        "id": "configured-model-browser-conversation",
                        "modes": ["integration"],
                        "depends_on": ["base"],
                    },
                    {
                        "id": "configured-model-browser-campaign",
                        "modes": ["integration"],
                        "depends_on": ["base"],
                    },
                    {"id": "integration", "modes": ["integration"], "depends_on": ["base"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    result = MODULE.create_deterministic_policy(source, output)
    ids = {row["id"] for row in result["steps"]}
    assert ids == {"base", "integration"}
    assert output.is_file()


def test_overlay_fails_closed_when_protected_gate_contract_drifted(tmp_path: Path) -> None:
    source = tmp_path / "policy.json"
    source.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "id": "configured-model-browser-conversation",
                        "modes": ["release"],
                        "depends_on": [],
                    },
                    {
                        "id": "configured-model-browser-campaign",
                        "modes": ["integration"],
                        "depends_on": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no longer an integration-only"):
        MODULE.create_deterministic_policy(source, tmp_path / "overlay.json")


def test_validation_environment_removes_production_and_repair_credentials() -> None:
    cleaned = MODULE._clean_environment(
        {
            "PATH": "/bin",
            "GOVERNED_REPAIR_MODEL_API_KEY": "secret",
            "PRODUCTION_MODEL_API_KEY": "secret",
            "PRODUCTION_EMBEDDING_API_KEY": "secret",
            "QUALITY_EVIDENCE_SIGNING_KEY": "secret",
        }
    )
    assert cleaned == {"PATH": "/bin"}
