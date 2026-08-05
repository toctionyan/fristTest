from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "github_agent_fixer.py"


def _load():
    spec = importlib.util.spec_from_file_location("github_agent_fixer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def test_parse_fenced_model_json() -> None:
    payload = MODULE.parse_model_json('```json\n{"root_cause":"x","patch":"diff --git a/a.py b/a.py"}\n```')
    assert payload["root_cause"] == "x"


def test_minimal_allowed_patch_passes() -> None:
    patch = "\n".join(
        [
            "diff --git a/services/app.py b/services/app.py",
            "--- a/services/app.py",
            "+++ b/services/app.py",
            "@@ -1 +1 @@",
            "-value = 1",
            "+value = 2",
        ]
    )
    assert MODULE.validate_patch(patch, ["services/app.py"], max_files=2, max_lines=20) == ["services/app.py"]


def test_scope_expansion_is_rejected() -> None:
    patch = "diff --git a/scripts/other.py b/scripts/other.py\n--- a/scripts/other.py\n+++ b/scripts/other.py\n@@ -1 +1 @@\n-a=1\n+a=2\n"
    with pytest.raises(ValueError, match="outside the frozen repair scope"):
        MODULE.validate_patch(patch, ["services/app.py"], max_files=2, max_lines=20)


def test_test_skip_weakening_is_rejected() -> None:
    patch = "diff --git a/services/test_app.py b/services/test_app.py\n--- a/services/test_app.py\n+++ b/services/test_app.py\n@@ -1 +1,2 @@\n import pytest\n+pytest.skip('ignore', allow_module_level=True)\n"
    with pytest.raises(ValueError, match="weakening"):
        MODULE.validate_patch(patch, ["services/test_app.py"], max_files=2, max_lines=20)


def test_official_provider_host_is_required() -> None:
    with pytest.raises(ValueError, match="official host"):
        MODULE._validate_provider("deepseek", "https://proxy.invalid/v1")
