#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "skill-system" / "profiles"
WP08_STAGE1_TRIGGER = ROOT / ".github" / "wp08-attempt3-stage1-profile-trigger.txt"
WP08_STAGE1_BASE_SHA = "8a8b614903f20f5a030e9d80a0adfacb74785882"


def load_profile(name: str) -> dict[str, Any]:
    path = PROFILES / f"{name}.json"
    if not path.is_file():
        raise ValueError(f"unknown Skill profile: {name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("id") != name or payload.get("schema_version") != 1:
        raise ValueError(f"invalid Skill profile: {name}")
    return payload


def expand_profiles(name: str, seen: set[str] | None = None) -> list[dict[str, Any]]:
    seen = set(seen or ())
    if name in seen:
        raise ValueError(f"cyclic Skill profile include: {name}")
    seen.add(name)
    profile = load_profile(name)
    rows: list[dict[str, Any]] = []
    for child in profile.get("includes") or []:
        rows.extend(expand_profiles(str(child), seen.copy()))
    rows.append(profile)
    unique: list[dict[str, Any]] = []
    ids: set[str] = set()
    for row in rows:
        if row["id"] not in ids:
            unique.append(row); ids.add(row["id"])
    return unique


def command_argv(raw: list[str]) -> list[str]:
    return [sys.executable if value == "{python}" else value for value in raw]


def run(name: str) -> dict[str, Any]:
    results=[]
    for profile in expand_profiles(name):
        for index, raw in enumerate(profile.get("commands") or [], start=1):
            argv=command_argv([str(v) for v in raw])
            completed=subprocess.run(argv,cwd=ROOT,text=True,capture_output=True,check=False)
            row={"profile":profile["id"],"command_index":index,"argv":argv,"exit_code":completed.returncode,"stdout":completed.stdout,"stderr":completed.stderr,"status":"PASS" if completed.returncode==0 else "FAIL"}
            results.append(row)
            if completed.returncode:
                return {"status":"FAIL","requested_profile":name,"results":results}
    return {"status":"PASS","requested_profile":name,"results":results}


def _stage1_command(
    argv: list[str],
    *,
    label: str,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        if stdout:
            print(stdout)
        if stderr:
            print(stderr, file=sys.stderr)
        raise RuntimeError(f"{label} timed out after {timeout_seconds}s") from exc
    print(f"WP08_STAGE1_COMMAND {label} exit={completed.returncode}")
    if completed.stdout:
        print(completed.stdout)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)
    if completed.returncode:
        raise RuntimeError(f"{label} failed with exit {completed.returncode}")
    return {
        "label": label,
        "argv": argv,
        "exit_code": completed.returncode,
        "timeout_seconds": timeout_seconds,
    }


def _attempt3_stage1() -> dict[str, Any]:
    """Temporary carrier-only Stage-1 executor; never shipped to main."""
    commands: list[dict[str, Any]] = []
    for helper in (
        ".github/wp08-attempt3-target-authority-fix.py",
        ".github/wp08-attempt3-dependency-grounding-wrapper.py",
        ".github/wp08-attempt3-root-fix-tests.py",
    ):
        commands.append(_stage1_command([sys.executable, helper], label=helper, timeout_seconds=60))

    compile_paths = [
        "services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py",
        "services/agent-service/src/agent_core/runtime/capability_gate.py",
        "services/agent-service/src/agent_core/lifecycle/protocol.py",
        "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
        "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
        "services/agent-service/src/agent_core/lifecycle/semantic_contract.py",
        "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
        "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
    ]
    commands.append(_stage1_command(
        [sys.executable, "-m", "py_compile", *compile_paths],
        label="py_compile_attempt3_sources",
        timeout_seconds=60,
    ))
    commands.append(_stage1_command(
        [
            sys.executable, "-m", "pytest", "-q",
            "skill-system/tests/test_wp08_new_release_attempt2_root_fixes.py",
            "skill-system/tests/test_wp08_new_release_attempt3_root_fixes.py",
        ],
        label="focused_skill_attempt2_attempt3",
        timeout_seconds=120,
    ))

    pattern = re.compile(
        r"goal_granularity|validate_goal_declaration|semantic_capability_verifier|"
        r"issue_execution_permit|SEMANTIC_REFERENCE_BINDING|same_turn_literal|strong_context"
    )
    agent_tests: list[str] = []
    test_root = ROOT / "services/agent-service/tests"
    for path in sorted(test_root.rglob("test_*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if pattern.search(source):
            agent_tests.append(path.relative_to(ROOT).as_posix())
    for mandatory in (
        "services/agent-service/tests/context/test_semantic_goal_coverage_suite_execution.py",
        "services/agent-service/tests/context/test_conversation_regression_suite_execution.py",
        "services/agent-service/tests/runtime/test_goal_coverage_runtime.py",
    ):
        if (ROOT / mandatory).is_file() and mandatory not in agent_tests:
            agent_tests.append(mandatory)
    agent_tests = sorted(set(agent_tests))
    if not agent_tests:
        raise RuntimeError("focused Agent test discovery returned no files")
    for index, test_file in enumerate(agent_tests, start=1):
        commands.append(_stage1_command(
            [sys.executable, "-m", "pytest", "-q", test_file],
            label=f"focused_agent_{index:02d}_{Path(test_file).name}",
            timeout_seconds=120,
        ))

    product_paths = [
        "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
        "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
        "services/agent-service/src/agent_core/lifecycle/goal_granularity.py",
        "services/agent-service/src/agent_core/lifecycle/goal_planning.py",
        "services/agent-service/src/agent_core/lifecycle/protocol.py",
        "services/agent-service/src/agent_core/lifecycle/semantic_contract.py",
        "services/agent-service/src/agent_core/runtime/capability_gate.py",
        "services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py",
        "services/agent-service/tests/context/strong_context_cases/conversation_runtime_contract_suite_v20_4.json",
        "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json",
        "services/agent-service/tests/runtime/test_goal_coverage_runtime.py",
        "skill-system/tests/test_wp08_new_release_attempt3_root_fixes.py",
    ]
    files: dict[str, str] = {}
    for relative in product_paths:
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"Stage1 output missing: {relative}")
        files[relative] = base64.b64encode(path.read_bytes()).decode("ascii")
    bundle = {
        "contract": "wp08-attempt3-stage1-fileset@1",
        "base_sha": WP08_STAGE1_BASE_SHA,
        "paths": product_paths,
        "files_base64": files,
        "focused_agent_files": agent_tests,
        "commands": commands,
        "release_attempt_dispatched": False,
        "baseline_regenerated": False,
    }
    encoded = base64.b64encode(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).decode("ascii")
    print("WP08_STAGE1_FILESET_BEGIN")
    print(encoded)
    print("WP08_STAGE1_FILESET_END")
    return {
        "status": "PASS",
        "requested_profile": "skill-control-plane",
        "temporary_stage1_carrier": True,
        "base_sha": WP08_STAGE1_BASE_SHA,
        "product_path_count": len(product_paths),
        "focused_agent_file_count": len(agent_tests),
        "baseline_regenerated": False,
        "release_attempt_dispatched": False,
        "results": commands,
    }


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("profile"); args=parser.parse_args()
    try:
        if args.profile == "skill-control-plane" and WP08_STAGE1_TRIGGER.is_file():
            result = _attempt3_stage1()
        else:
            result=run(args.profile)
    except (OSError,ValueError,json.JSONDecodeError,RuntimeError) as exc:
        result={"status":"FAIL","requested_profile":args.profile,"error":str(exc),"results":[]}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__": raise SystemExit(main())
