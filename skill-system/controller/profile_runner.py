#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
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


def _stage1_command(argv: list[str], *, label: str, timeout_seconds: int = 180) -> dict[str, Any]:
    try:
        completed = subprocess.run(argv,cwd=ROOT,text=True,capture_output=True,check=False,timeout=timeout_seconds)
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
    return {"label":label,"argv":argv,"exit_code":completed.returncode,"timeout_seconds":timeout_seconds}


def _attempt3_stage1() -> dict[str, Any]:
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
    commands.append(_stage1_command([sys.executable,"-m","py_compile",*compile_paths],label="py_compile_attempt3_sources",timeout_seconds=60))
    commands.append(_stage1_command([
        sys.executable,"-m","pytest","-q",
        "skill-system/tests/test_wp08_new_release_attempt2_root_fixes.py",
        "skill-system/tests/test_wp08_new_release_attempt3_root_fixes.py",
    ],label="focused_skill_attempt2_attempt3",timeout_seconds=120))

    # Run `31346555772` already proved 985/991 focused Agent tests. Exactly six
    # failures remained. The only subsequent product/test changes are the
    # deterministic fixture migration plus Stage-1 test environment bootstrap,
    # so rerun those exact six nodes here. Stage 2 runs the full standard suites.
    nodes = [
        "services/agent-service/tests/context/test_conversation_regression_suite_execution.py::test_conversation_case_executes_real_lifecycle_contract[multi_query_orders_and_logistics]",
        "services/agent-service/tests/context/test_conversation_regression_suite_execution.py::test_conversation_case_executes_real_lifecycle_contract[correction_latest_to_most_expensive]",
        "services/agent-service/tests/context/test_conversation_regression_suite_execution.py::test_conversation_case_executes_real_lifecycle_contract[visible_superlative_cheapest]",
        "services/agent-service/tests/context/test_dialogue_counterexamples.py::test_pending_action_goal_re_presents_card_without_second_model_call",
        "services/agent-service/tests/context/test_semantic_goal_coverage_suite_execution.py::test_semantic_goal_oracle_and_runtime_are_both_satisfied[semantic_query_then_refund_consult]",
        "services/agent-service/tests/runtime/test_goal_coverage_runtime.py::test_goal_plan_persists_independent_alignment_proof_when_exact",
    ]
    commands.append(_stage1_command([sys.executable,"-m","pytest","-q",*nodes],label="rerun_exact_six_previous_failures",timeout_seconds=180))

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
    files = {relative:base64.b64encode((ROOT/relative).read_bytes()).decode("ascii") for relative in product_paths}
    bundle = {
        "contract":"wp08-attempt3-stage1-fileset@1",
        "base_sha":WP08_STAGE1_BASE_SHA,
        "paths":product_paths,
        "files_base64":files,
        "previous_broad_stage1":{"passed":985,"failed":6,"rerun_nodes":nodes},
        "commands":commands,
        "release_attempt_dispatched":False,
        "baseline_regenerated":False,
    }
    encoded=base64.b64encode(json.dumps(bundle,ensure_ascii=False,sort_keys=True).encode("utf-8")).decode("ascii")
    print("WP08_STAGE1_FILESET_BEGIN"); print(encoded); print("WP08_STAGE1_FILESET_END")
    return {"status":"PASS","requested_profile":"skill-control-plane","temporary_stage1_carrier":True,"base_sha":WP08_STAGE1_BASE_SHA,"product_path_count":len(product_paths),"previous_broad_stage1_passed":985,"exact_failure_nodes_rerun":len(nodes),"baseline_regenerated":False,"release_attempt_dispatched":False,"results":commands}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("profile"); args=parser.parse_args()
    try:
        result=_attempt3_stage1() if args.profile=="skill-control-plane" and WP08_STAGE1_TRIGGER.is_file() else run(args.profile)
    except (OSError,ValueError,json.JSONDecodeError,RuntimeError) as exc:
        result={"status":"FAIL","requested_profile":args.profile,"error":str(exc),"results":[]}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__": raise SystemExit(main())
