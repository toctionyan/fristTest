from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skill_invocation import canonical_skill_path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._:-]+$")


class PluginRegistryError(ValueError):
    """Raised when an explicit Skill or Workflow cannot be resolved exactly."""


@dataclass(frozen=True)
class SkillPlugin:
    name: str
    path: str
    status: str


@dataclass(frozen=True)
class WorkflowPlugin:
    name: str
    path: str
    status: str


def _safe_name(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not text or not _SAFE_NAME.fullmatch(text):
        raise PluginRegistryError(f"{label} contains unsupported characters")
    return text


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PluginRegistryError(f"{label} registry is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PluginRegistryError(f"{label} registry is invalid JSON: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise PluginRegistryError(f"{label} registry schema_version must be 1")
    return payload


def load_skill_plugins(workspace: Path) -> dict[str, SkillPlugin]:
    workspace = workspace.resolve()
    payload = _load_json(
        workspace / "skill-system" / "registry" / "active-skills.json",
        label="Skill",
    )
    result: dict[str, SkillPlugin] = {}
    for raw in payload.get("skills") or []:
        if not isinstance(raw, dict):
            raise PluginRegistryError("Skill registry contains a non-object row")
        name = _safe_name(raw.get("name"), label="Skill name")
        status = str(raw.get("status") or "").strip()
        path = str(raw.get("path") or "").strip()
        if name in result:
            raise PluginRegistryError(f"duplicate Skill registry entry: {name}")
        expected = canonical_skill_path(name).as_posix()
        if path != expected:
            raise PluginRegistryError(
                f"Skill registry path is not canonical for {name}: expected={expected} actual={path}"
            )
        if not (workspace / path).is_file():
            raise PluginRegistryError(f"Skill registry path is missing: {path}")
        result[name] = SkillPlugin(name=name, path=path, status=status)
    return result


def resolve_skill_plugin(workspace: Path, name: str) -> SkillPlugin:
    exact = _safe_name(name, label="Skill name")
    plugin = load_skill_plugins(workspace).get(exact)
    if plugin is None or plugin.status != "active":
        raise PluginRegistryError(f"active Skill not found: {exact}")
    return plugin


def load_workflow_plugins(workspace: Path) -> dict[str, WorkflowPlugin]:
    workspace = workspace.resolve()
    payload = _load_json(
        workspace / "skill-system" / "registry" / "active-workflows.json",
        label="Workflow",
    )
    result: dict[str, WorkflowPlugin] = {}
    for raw in payload.get("workflows") or []:
        if not isinstance(raw, dict):
            raise PluginRegistryError("Workflow registry contains a non-object row")
        name = _safe_name(raw.get("name"), label="Workflow name")
        status = str(raw.get("status") or "").strip()
        path = str(raw.get("path") or "").strip()
        if name in result:
            raise PluginRegistryError(f"duplicate Workflow registry entry: {name}")
        expected = f"skill-system/workflows/{name}.json"
        if path != expected:
            raise PluginRegistryError(
                f"Workflow registry path is not canonical for {name}: expected={expected} actual={path}"
            )
        if not (workspace / path).is_file():
            raise PluginRegistryError(f"Workflow registry path is missing: {path}")
        result[name] = WorkflowPlugin(name=name, path=path, status=status)
    return result


def resolve_workflow_plugin(workspace: Path, name: str) -> WorkflowPlugin:
    exact = _safe_name(name, label="Workflow name")
    plugin = load_workflow_plugins(workspace).get(exact)
    if plugin is None or plugin.status != "active":
        raise PluginRegistryError(f"active Workflow not found: {exact}")
    return plugin
