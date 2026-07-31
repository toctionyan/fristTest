from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from tests.support.paths import workspace_root


def _verifier():
    root = workspace_root(__file__)
    path = root / "scripts" / "verify_coverage_baseline.py"
    spec = importlib.util.spec_from_file_location("coverage_baseline_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _baseline(path: Path, *, frontend: float = 0.25) -> None:
    path.write_text(
        json.dumps({"python_minimum_line_rate": 0.5, "frontend_minimum_line_rate": frontend}),
        encoding="utf-8",
    )


def test_coverage_baseline_requires_and_compares_both_test_surfaces(tmp_path: Path) -> None:
    verifier = _verifier()
    coverage_dir = tmp_path / "coverage"
    coverage_dir.mkdir()
    (coverage_dir / "agent-service-standard.xml").write_text('<coverage line-rate="0.6" />', encoding="utf-8")
    frontend_dir = coverage_dir / "frontend"
    frontend_dir.mkdir()
    (frontend_dir / "coverage-summary.json").write_text(
        json.dumps({"total": {"lines": {"pct": 30.0}}}), encoding="utf-8"
    )
    baseline = tmp_path / "baseline.json"
    _baseline(baseline)

    report = verifier.verify(coverage_dir, baseline)

    assert report["status"] == "PASS"
    assert report["python"]["measured_minimum_line_rate"] == 0.6
    assert report["frontend"]["measured_line_rate"] == 0.3


def test_coverage_baseline_rejects_missing_frontend_evidence(tmp_path: Path) -> None:
    verifier = _verifier()
    coverage_dir = tmp_path / "coverage"
    coverage_dir.mkdir()
    (coverage_dir / "agent-service-standard.xml").write_text('<coverage line-rate="0.6" />', encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    _baseline(baseline)

    report = verifier.verify(coverage_dir, baseline)

    assert report["status"] == "FAIL"
    assert any("frontend coverage summary" in error for error in report["errors"])
