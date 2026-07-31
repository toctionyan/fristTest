from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tests.support.paths import workspace_root


def _guard():
    root = workspace_root(__file__)
    path = root / "architecture-skill" / "scripts" / "verify_convergence.py"
    spec = importlib.util.spec_from_file_location("convergence_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_workspace_has_one_converged_architecture() -> None:
    root = workspace_root(__file__)
    policy = json.loads((root / "governance" / "architecture-policy.json").read_text(encoding="utf-8"))
    policy["enforce_clean_artifacts"] = False
    report = _guard().verify(root, policy)
    assert report["status"] == "PASS", report


def test_convergence_matrix_requires_replacement_for_new_abstractions() -> None:
    root = workspace_root(__file__)
    matrix = (root / "docs" / "architecture" / "CONVERGENCE_MATRIX.md").read_text(encoding="utf-8")
    assert "新增抽象" in matrix
    assert "替换或删除" in matrix
