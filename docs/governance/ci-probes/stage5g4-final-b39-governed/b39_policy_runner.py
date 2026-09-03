from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


def _junit_summary(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    failures = 0
    errors = 0
    skipped = 0
    for case in cases:
        tags = {child.tag.rsplit("}", 1)[-1] for child in list(case)}
        failures += int("failure" in tags)
        errors += int("error" in tags)
        skipped += int("skipped" in tags)
    return {
        "tests": len(cases),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: b39_policy_runner.py <focused|native|b38-invariants|negative|agent-standard>")
    step = sys.argv[1]
    workspace = Path.cwd().resolve()
    evidence = Path(os.environ["QUALITY_EVIDENCE_DIR"]).resolve()
    junit = evidence / "junit"
    junit.mkdir(parents=True, exist_ok=True)

    # B39 governance replays the same local-profile test boundary as the
    # authoritative repair probe.  Keep it explicit inside the gate runner so
    # an omitted shell env field cannot turn product tests into environment RED.
    os.environ["APP_PROFILE"] = "local"

    sys.path.insert(0, str(workspace / "services/agent-service/src"))
    sys.path.insert(0, str(workspace / "services/agent-service"))
    oracle = Path(__file__).with_name("test_plan_run_runtime_field_isolation.py").resolve()

    common = ["-q", "-p", "no:cacheprovider"]
    selections: dict[str, list[str]] = {
        "focused": [str(oracle)],
        "native": [
            "services/agent-service/tests/context/test_conversation_regression_suite_execution.py",
            "services/agent-service/tests/context/test_semantic_goal_coverage_suite_execution.py",
            "services/agent-service/tests/runtime/test_unsupported_capability_surface_binding.py",
        ],
        "b38-invariants": [
            "services/agent-service/tests/runtime/test_stage4_goal_output_refs.py::test_dependency_goal_output_is_not_reused_across_different_explicit_targets",
            "services/agent-service/tests/runtime/test_stage4_goal_output_refs.py::test_completed_dependency_reuses_verified_typed_goal_output",
        ],
        "negative": [str(oracle)],
        "agent-standard": [
            "-ra",
            "-m",
            "not integration and not preprod",
            "services/agent-service/tests",
        ],
    }
    if step not in selections:
        raise SystemExit(f"unknown B39 policy step: {step}")
    output_name = {
        "focused": "b39-focused-proof.xml",
        "native": "b39-native-regression.xml",
        "b38-invariants": "b39-b38-invariants.xml",
        "negative": "b39-negative-path.xml",
        "agent-standard": "b39-agent-standard.xml",
    }[step]
    output_path = junit / output_name
    args = common + selections[step] + [f"--junitxml={output_path}"]
    result = int(pytest.main(args))

    summary = _junit_summary(output_path)
    diagnostics = evidence / "b39-policy-runner-diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / f"{step}.json").write_text(
        json.dumps(
            {
                "step": step,
                "pytest_exit_code": result,
                "junit_path": output_path.relative_to(evidence).as_posix(),
                "junit": summary,
                "oracle": str(oracle),
                "app_profile": os.environ["APP_PROFILE"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # The baseline driver requires a semantic RED marker. Emit it only after
    # the immutable focused oracle actually produced exactly one pytest failure
    # (not an import/collection error), so textual output cannot manufacture a
    # false baseline transition.
    if step == "focused" and result != 0:
        expected_red = {"tests": 1, "failures": 1, "errors": 0, "skipped": 0}
        if summary != expected_red:
            print(json.dumps({"status": "UNEXPECTED_FOCUSED_FAILURE", "junit": summary}, ensure_ascii=False))
            return 3
        print("B39_FOCUSED_ORACLE_EXPECTED_RED: 1 failed")

    return result


if __name__ == "__main__":
    raise SystemExit(main())
