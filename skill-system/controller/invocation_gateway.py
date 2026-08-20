from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_DIR = ROOT / "skill-system" / "registry"
OPEN_MODE = "OPEN"
SKILL_MODE = "SKILL_BOUND"
WORKFLOW_MODE = "WORKFLOW_BOUND"
INVOCATION_ROUTE_SCHEMA = "plugin-invocation-route@1"


class InvocationGatewayError(ValueError):
    """Raised when an explicit plugin/workflow request cannot be resolved exactly."""


def _load_registry(name: str, collection: str, key: str) -> dict[str, dict[str, Any]]:
    path = REGISTRY_DIR / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InvocationGatewayError(f"registry is missing: {name}") from exc
    except json.JSONDecodeError as exc:
        raise InvocationGatewayError(f"registry is invalid JSON: {name}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise InvocationGatewayError(f"registry has unsupported schema: {name}")
    rows = payload.get(collection)
    if not isinstance(rows, list):
        raise InvocationGatewayError(f"registry collection is invalid: {name}:{collection}")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = str(row.get(key) or "").strip()
        if identity and row.get("status") == "active":
            indexed[identity] = dict(row)
    return indexed


def active_skills() -> dict[str, dict[str, Any]]:
    return _load_registry("active-skills.json", "skills", "name")


def active_workflows() -> dict[str, dict[str, Any]]:
    return _load_registry("active-workflows.json", "workflows", "name")


def build_route(
    *,
    request: str,
    target: str | None = None,
    skill: str | None = None,
    workflow: str | None = None,
) -> dict[str, Any]:
    """Resolve only explicit plugin choices; never infer a Skill from open language.

    With neither ``skill`` nor ``workflow`` supplied the request remains OPEN.
    With an explicit name, resolution is exact and fail-closed.  The gateway does
    not inspect request words to select or substitute another plugin.
    """

    request_text = str(request or "").strip()
    if not request_text:
        raise InvocationGatewayError("request must not be empty")
    skill_name = str(skill or "").strip()
    workflow_name = str(workflow or "").strip()
    target_name = str(target or "").strip() or None
    if skill_name and workflow_name:
        raise InvocationGatewayError("choose exactly one of explicit Skill or Workflow")

    route: dict[str, Any] = {
        "schema": INVOCATION_ROUTE_SCHEMA,
        "request": request_text,
        "target": target_name,
        "policy": {
            "natural_language_skill_inference_allowed": False,
            "fuzzy_plugin_substitution_allowed": False,
            "open_mode_when_unspecified": True,
        },
    }
    if skill_name:
        skills = active_skills()
        if skill_name not in skills:
            raise InvocationGatewayError(f"unknown active Skill: {skill_name}")
        route.update(
            {
                "mode": SKILL_MODE,
                "skill": skill_name,
                "skill_path": str(skills[skill_name].get("path") or ""),
            }
        )
        return route

    if workflow_name:
        workflows = active_workflows()
        if workflow_name not in workflows:
            raise InvocationGatewayError(f"unknown active Workflow: {workflow_name}")
        route.update(
            {
                "mode": WORKFLOW_MODE,
                "workflow": workflow_name,
                "workflow_path": str(workflows[workflow_name].get("path") or ""),
            }
        )
        return route

    route.update({"mode": OPEN_MODE, "skill": None, "workflow": None})
    return route


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve OPEN, explicit Skill, or explicit Workflow mode.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--target")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--skill")
    group.add_argument("--workflow")
    args = parser.parse_args()
    try:
        route = build_route(
            request=args.request,
            target=args.target,
            skill=args.skill,
            workflow=args.workflow,
        )
    except InvocationGatewayError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"status": "PASS", "route": route}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
