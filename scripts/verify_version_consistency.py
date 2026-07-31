#!/usr/bin/env python3
"""Verify that release, service, policy and skill versions do not drift."""
from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from typing import Any


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pyproject_version(path: Path) -> str | None:
    if not path.is_file():
        return None
    return tomllib.loads(path.read_text(encoding="utf-8")).get("project", {}).get("version")


def _skill_version(skill_md: Path) -> str | None:
    m = re.search(r"^Skill 版本：\s*([^\s]+)\s*$", _read(skill_md), re.MULTILINE)
    return m.group(1) if m else None


def verify(workspace: Path) -> dict[str, Any]:
    errors: list[str] = []
    workspace_version = _read(workspace / "VERSION").strip()
    skill_version = _skill_version(workspace / "architecture-skill/SKILL.md")
    collected: dict[str, str | None] = {
        "VERSION": workspace_version,
        "governance/architecture-policy.json": _json(workspace / "governance/architecture-policy.json").get("version"),
        "governance/quality-loop-policy.json": _json(workspace / "governance/quality-loop-policy.json").get("version"),
        "release/MANIFEST.json": _json(workspace / "release/MANIFEST.json").get("version") if (workspace / "release/MANIFEST.json").is_file() else None,
        "services/agent-service/pyproject.toml": _pyproject_version(workspace / "services/agent-service/pyproject.toml"),
        "services/business-service/pyproject.toml": _pyproject_version(workspace / "services/business-service/pyproject.toml"),
    }
    for label, version in collected.items():
        if version != workspace_version:
            errors.append(f"workspace_version_mismatch:{label}:{version}!={workspace_version}")

    text_checks = {
        "services/agent-service/app/main.py": f'version="{workspace_version}"',
        "services/business-service/business_service/api.py": f'version="{workspace_version}"',
        "services/agent-service/src/agent_modules/ecommerce/module.py": f'version = "{workspace_version}"',
        "services/agent-service/src/agent_modules/ecommerce/manifest.py": f'"version": "{workspace_version}"',
    }
    for path, marker in text_checks.items():
        if marker not in _read(workspace / path):
            errors.append(f"missing_version_marker:{path}:{marker}")

    ecommerce_manifest = _json(workspace / "services/agent-service/src/agent_modules/ecommerce/module_manifest.json")
    if ecommerce_manifest.get("version") != workspace_version:
        errors.append("ecommerce_module_manifest_version_mismatch")

    manifest = _json(workspace / "architecture-skill/manifest.json")
    release_manifest = _json(workspace / "release/MANIFEST.json") if (workspace / "release/MANIFEST.json").is_file() else {}
    skill_versions = {
        "architecture-skill/SKILL.md": skill_version,
        "architecture-skill/manifest.json": manifest.get("version"),
        "release/MANIFEST.json.skill.version": (release_manifest.get("skill") or {}).get("version"),
    }
    for label, version in skill_versions.items():
        if version != skill_version:
            errors.append(f"skill_version_mismatch:{label}:{version}!={skill_version}")
    if workspace_version not in _read(workspace / "CHANGELOG.md"):
        errors.append("workspace_version_not_mentioned:CHANGELOG.md")
    validation_report = _read(workspace / "release/VALIDATION_REPORT.md")
    if workspace_version not in validation_report:
        errors.append("workspace_version_not_mentioned:release/VALIDATION_REPORT.md")
    if skill_version and skill_version not in validation_report:
        errors.append("skill_version_not_mentioned:release/VALIDATION_REPORT.md")

    exact_text_markers = {
        "README.md": f"V{workspace_version.rsplit('.', 1)[0]} / Skill V{skill_version.rsplit('.', 1)[0]}",
        "docs/architecture/TARGET_ARCHITECTURE.md": f"V{workspace_version.rsplit('.', 1)[0]}",
        "services/agent-service/tests/context/test_conversation_regression_suite_execution.py": "conversation_runtime_contract_suite_v20_4.json",
        "services/agent-service/tests/context/test_semantic_goal_coverage_suite_execution.py": "semantic_goal_coverage_suite_v20_4.json",
        "scripts/verify_strong_context_cases.py": 'RUNTIME_SUITE_NAME = "conversation_runtime_contract_suite_v20_4"',
        "services/agent-service/scripts/verify_preprod_conversation_smoke.py": "semantic_goal_coverage_suite_v20_4.json",
    }
    for path, marker in exact_text_markers.items():
        if marker not in _read(workspace / path):
            errors.append(f"missing_current_release_marker:{path}:{marker}")

    release_profile = release_manifest.get("profile")
    if release_profile not in {"development-workspace", "clean-release"}:
        errors.append(f"invalid_release_profile:{release_profile}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "workspace_version": workspace_version,
        "skill_version": skill_version,
        "checked_versions": collected,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    args = parser.parse_args()
    result = verify(Path(args.workspace_root).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
