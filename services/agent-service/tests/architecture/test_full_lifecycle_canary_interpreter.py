from __future__ import annotations

import importlib.util
from pathlib import Path

from tests.support.paths import workspace_root


def _load_canary_module():
    root = workspace_root(__file__)
    path = root / "scripts" / "verify_full_lifecycle_canary.py"
    spec = importlib.util.spec_from_file_location("verify_full_lifecycle_canary", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_python_preserves_virtualenv_launcher_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_canary_module()
    real_python = tmp_path / "python-real"
    real_python.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher = tmp_path / ".venv" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(real_python)

    monkeypatch.delenv("QUALITY_TEST_PYTHON", raising=False)
    selected = module._resolve_python("QUALITY_TEST_PYTHON", launcher)

    assert selected == launcher.absolute()
    assert selected != real_python.resolve()
