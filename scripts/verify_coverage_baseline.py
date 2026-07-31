#!/usr/bin/env python3
"""Enforce committed non-decreasing Python and frontend coverage baselines.

The initial baseline is deliberately 0.0 until the first green CI run publishes
measured values.  Updating it is an explicit reviewed source change, never a
side effect of a test run.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def _line_rate(path: Path) -> float:
    root = ET.parse(path).getroot()
    return float(root.attrib.get("line-rate", "0"))


def _frontend_line_rate(path: Path) -> float:
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        percent = float(payload["total"]["lines"]["pct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid frontend coverage summary: {path}") from exc
    rate = percent / 100.0
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"frontend line coverage must be between 0 and 100 percent: {path}")
    return rate


def _baseline_rate(baseline: dict[str, object], key: str) -> float:
    try:
        rate = float(baseline[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"coverage baseline must declare {key}") from exc
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"coverage baseline {key} must be between 0 and 1")
    return rate


def verify(coverage_dir: Path, baseline_path: Path) -> dict[str, object]:
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if not isinstance(baseline, dict):
            raise ValueError("coverage baseline must be a JSON object")
        python_minimum = _baseline_rate(baseline, "python_minimum_line_rate")
        frontend_minimum = _baseline_rate(baseline, "frontend_minimum_line_rate")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"status": "FAIL", "errors": [str(exc)]}

    reports = sorted(coverage_dir.glob("*-standard.xml"))
    frontend_report = coverage_dir / "frontend" / "coverage-summary.json"
    errors: list[str] = []
    if not reports:
        errors.append("no standard Python coverage reports found")
    if not frontend_report.is_file():
        errors.append(f"no frontend coverage summary found: {frontend_report}")
    if errors:
        return {"status": "FAIL", "errors": errors}

    try:
        python_measured = min(_line_rate(path) for path in reports)
        frontend_measured = _frontend_line_rate(frontend_report)
    except (ET.ParseError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "errors": [str(exc)]}

    passed = python_measured >= python_minimum and frontend_measured >= frontend_minimum
    result = {
        "status": "PASS" if passed else "FAIL",
        "python": {
            "measured_minimum_line_rate": python_measured,
            "baseline_minimum_line_rate": python_minimum,
            "reports": [str(path) for path in reports],
        },
        "frontend": {
            "measured_line_rate": frontend_measured,
            "baseline_minimum_line_rate": frontend_minimum,
            "report": str(frontend_report),
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-dir", required=True)
    parser.add_argument("--baseline", default="governance/quality-coverage-baseline.json")
    args = parser.parse_args()
    result = verify(Path(args.coverage_dir), Path(args.baseline))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
