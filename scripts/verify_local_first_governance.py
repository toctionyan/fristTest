#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "skill-system/controller/local_first_governance.py"
CLI = ROOT / "scripts/local_first_loop.py"
SCHEMA = ROOT / "skill-system/schemas/local-first-task.schema.json"
DOC = ROOT / "docs/governance/LOCAL_FIRST_ENGINEERING_LOOP.md"
MAKEFILE = ROOT / "Makefile"
AGENTS = ROOT / "AGENTS.md"
PROFILE = ROOT / "skill-system/profiles/skill-static.json"
STAGE2 = ROOT / ".github/workflows/governed-ci-repair-stage2.yml"
RETIRED_AUTO_WORKFLOW = ROOT / ".github/workflows/governed-ci-stage2-auto-handoff.yml"
RETIRED_AUTO_CONTROLLER = ROOT / "scripts/github_stage2_auto_handoff.py"
TESTS = (
    ROOT / "skill-system/tests/test_local_first_governance.py",
    ROOT / "skill-system/tests/test_local_first_loop.py",
    ROOT / "skill-system/tests/test_repair_lane_single_authority.py",
)

FORBIDDEN_WRITABLE_AUTHORITY = (
    "merge_pull_request",
    "production_closed: true",
    "git push",
    "gh pr create",
    "contents: write",
)
REQUIRED_CORE_SYMBOLS = {
    "create_local_first_task",
    "begin_local_repair_round",
    "record_local_gate",
    "upload_admission",
    "bind_ci_run",
    "classify_ci_failure",
    "record_ci_result",
    "export_status",
}
RETIRED_REMOTE_APPROVAL_SYMBOLS = {
    "approve_remote_repair",
    "_remote_repair_is_approved",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def verify(root: Path = ROOT) -> dict[str, Any]:
    errors: list[str] = []
    required = (CORE, CLI, SCHEMA, DOC, MAKEFILE, AGENTS, PROFILE, STAGE2, *TESTS)
    for path in required:
        if not path.is_file():
            errors.append(f"missing required local-first file: {path.relative_to(root)}")

    if RETIRED_AUTO_WORKFLOW.exists():
        errors.append("retired automatic Stage-2 handoff workflow still exists")
    if RETIRED_AUTO_CONTROLLER.exists():
        errors.append("retired automatic Stage-2 handoff controller still exists")

    if errors:
        return {"status": "FAIL", "errors": errors, "production_closed": False}

    core_text = CORE.read_text(encoding="utf-8")
    cli_text = CLI.read_text(encoding="utf-8")
    for label, text in (("core", core_text), ("cli", cli_text)):
        for fragment in FORBIDDEN_WRITABLE_AUTHORITY:
            if fragment in text:
                errors.append(f"{label} gained forbidden authority: {fragment}")

    for path in (CORE, CLI, *TESTS):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"syntax error in {path.relative_to(root)}: {exc}")

    tree = ast.parse(core_text, filename=str(CORE))
    symbols = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    missing_symbols = sorted(REQUIRED_CORE_SYMBOLS - symbols)
    if missing_symbols:
        errors.append(f"local-first core symbols missing: {missing_symbols}")
    retired_symbols = sorted(RETIRED_REMOTE_APPROVAL_SYMBOLS & symbols)
    if retired_symbols:
        errors.append(f"retired local remote-approval symbols remain live: {retired_symbols}")
    if "approve-remote-repair" in cli_text:
        errors.append("retired approve-remote-repair CLI still exists")
    if '"remote_repair"' in core_text:
        errors.append("retired local remote_repair state still exists")
    if '"remote_fallback_activation": "manual-workflow-dispatch-only"' not in core_text:
        errors.append("CI feedback does not bind remote fallback activation to manual workflow dispatch")

    schema = _load_json(SCHEMA)
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("local-first task schema is not Draft 2020-12")
    required_schema_fields = set(schema.get("required") or [])
    expected_fields = {"task_id", "change_id", "base_sha", "branch", "patch_owner", "allowed_paths", "gates"}
    if not expected_fields.issubset(required_schema_fields):
        errors.append("local-first schema does not require immutable task identity and scope")

    profile = _load_json(PROFILE)
    commands = profile.get("commands") if isinstance(profile.get("commands"), list) else []
    if not any("scripts/verify_local_first_governance.py" in row for command in commands for row in command if isinstance(command, list)):
        errors.append("skill-static profile does not run local-first verifier")

    make_text = MAKEFILE.read_text(encoding="utf-8")
    for target in ("local-first-init:", "local-first-run:", "local-first-status:"):
        if target not in make_text:
            errors.append(f"Makefile target missing: {target}")

    agents_text = AGENTS.read_text(encoding="utf-8")
    for fragment in (
        "## Single-authority cutover rule",
        "exactly one authoritative owner and one live writer",
        "## Local-first Patch Owner lifecycle",
        "One TaskRun has one Patch Owner",
        "GitHub Actions is the clean-room certification layer",
        "explicitly approved fallback",
        "must never be automatically entered by a normal CI failure",
    ):
        if fragment not in agents_text:
            errors.append(f"AGENTS local-first authority text missing: {fragment}")

    doc_text = DOC.read_text(encoding="utf-8")
    for fragment in (
        "targeted gate",
        "Quick gate",
        "exact-scope upload admission",
        "original Patch Owner",
        "Remote Stage-2 repair is opt-in fallback only",
        "remote_repair_approval=explicitly-approved",
        "Single-authority cutover",
    ):
        if fragment not in doc_text:
            errors.append(f"local-first engineering document missing: {fragment}")

    stage2_text = STAGE2.read_text(encoding="utf-8")
    for fragment in (
        "workflow_dispatch:",
        "remote_repair_approval:",
        '"${REMOTE_REPAIR_APPROVAL}" != "explicitly-approved"',
        "Normal CI code failures must return to the local Patch Owner instead.",
    ):
        if fragment not in stage2_text:
            errors.append(f"explicit remote Stage-2 fallback binding missing: {fragment}")
    for fragment in ("workflow_run:", "schedule:", "push:"):
        if fragment in stage2_text:
            errors.append(f"remote Stage 2 regained automatic initial trigger: {fragment}")

    tests_text = "\n".join(path.read_text(encoding="utf-8") for path in TESTS)
    for scenario in (
        "test_scope_never_expands_from_ci_logs",
        "test_new_local_round_invalidates_prior_green_evidence",
        "test_environment_and_secret_failures_cannot_edit_product_code",
        "test_cli_rejects_task_spec_drift_after_init",
        "test_cli_init_rejects_non_git_workspace",
        "test_cli_init_rejects_base_sha_mismatch",
        "test_cli_init_rejects_branch_mismatch",
        "test_cli_admit_rejects_dirty_candidate",
        "test_retired_automatic_remote_repair_authority_is_deleted",
        "test_local_controller_has_no_second_remote_approval_writer",
    ):
        if scenario not in tests_text:
            errors.append(f"local-first adversarial test missing: {scenario}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "required_file_count": len(required),
        "core_symbol_count": len(REQUIRED_CORE_SYMBOLS),
        "production_closed": False,
    }


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
