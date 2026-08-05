from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "deployment" / "ci" / "release-toolchain-lock.json"
QUALITY_SCRIPT = ROOT / "scripts" / "quality_toolchain_contract.py"
RELEASE_SCRIPT = ROOT / "scripts" / "release_toolchain_contract.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


QUALITY = _load("quality_toolchain_contract_separation", QUALITY_SCRIPT)
RELEASE = _load("release_toolchain_contract_separation", RELEASE_SCRIPT)


def test_download_artifact_is_quality_only_not_release_authority() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    release_actions = lock["github_actions"]
    quality_only_actions = lock["quality_github_actions"]

    assert "actions/download-artifact" not in release_actions
    assert quality_only_actions["actions/download-artifact"] == {
        "sha": "37930b1c2abaa49bbe596cd826c3c89aef350131",
        "version": "v7.0.0",
    }


def test_quality_contract_combines_shared_and_quality_only_actions() -> None:
    result = QUALITY.validate_static(ROOT)
    assert result["status"] == "PASS"
    assert result["shared_action_count"] == 4
    assert result["quality_only_action_count"] == 1
    assert result["action_count"] >= 5


def test_release_contract_remains_release_only() -> None:
    result = RELEASE.validate_static_contract(ROOT)
    assert result["status"] == "PASS"
    assert "actions/download-artifact" not in result["action_pins"]


def test_quality_only_lock_cannot_override_shared_action() -> None:
    lock = {
        "github_actions": {
            "actions/checkout": {"sha": "a" * 40, "version": "v1"},
        },
        "quality_github_actions": {
            "actions/checkout": {"sha": "b" * 40, "version": "v2"},
        },
    }
    with pytest.raises(QUALITY.QualityToolchainError) as exc_info:
        QUALITY._quality_action_lock(lock)
    assert exc_info.value.code == "quality_action_lock_overlap"


def test_quality_only_lock_rejects_non_mapping_specs() -> None:
    lock = {
        "github_actions": {
            "actions/checkout": {"sha": "a" * 40, "version": "v1"},
        },
        "quality_github_actions": {
            "actions/download-artifact": "unlocked",
        },
    }
    with pytest.raises(QUALITY.QualityToolchainError) as exc_info:
        QUALITY._quality_action_lock(lock)
    assert exc_info.value.code == "quality_action_lock_invalid"
