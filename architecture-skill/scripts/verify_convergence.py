#!/usr/bin/env python3
"""One static acceptance gate for the converged workspace."""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _python_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _env_template_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in _read(path).splitlines():
        match = re.match(r"\s*([A-Z][A-Z0-9_]*)=", line)
        if match:
            keys.add(match.group(1))
    return keys


def _environment_variables(paths: list[Path]) -> set[str]:
    """Find direct runtime environment reads without importing application code."""
    names: set[str] = set()
    for base in paths:
        candidates = [base] if base.is_file() else _python_files(base)
        for path in candidates:
            try:
                tree = ast.parse(_read(path), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getenv"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    names.add(node.args[0].value)
                if (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "environ"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                ):
                    names.add(node.slice.value)
    return names


def _core_dependency_graph(core: Path) -> tuple[dict[str, set[str]], list[str]]:
    edges: dict[str, set[str]] = {}
    reverse_composition_imports: list[str] = []
    for path in _python_files(core):
        relative = path.relative_to(core)
        owner = relative.parts[0] if len(relative.parts) > 1 else "<root>"
        edges.setdefault(owner, set())
        try:
            tree = ast.parse(_read(path), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            imports: list[str] = []
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "agent_core":
                    imports.extend(f"agent_core.{alias.name}" for alias in node.names)
                else:
                    imports.append(node.module)
            for imported in imports:
                if not imported.startswith("agent_core."):
                    continue
                target = imported.split(".", 2)[1]
                if owner != target:
                    edges[owner].add(target)
                if target == "composition" and owner != "composition":
                    reverse_composition_imports.append(
                        f"{relative}:{getattr(node, 'lineno', 0)}"
                    )
    return edges, sorted(set(reverse_composition_imports))


def _dependency_cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(edges.get(node, set())):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == node:
                break
        if len(component) > 1 or node in edges.get(node, set()):
            cycles.append(sorted(component))

    for node in sorted(edges):
        if node not in indices:
            visit(node)
    return sorted(cycles)


def _normalized_cycle_components(raw: Any) -> list[dict[str, Any]]:
    """Validate and normalize the declared dependency-cycle debt baseline.

    A baseline component is intentionally a *superset allowance*: later
    revisions may split or shrink the component, but may never add a member or
    create a cycle that is not contained by one declared baseline component.
    This makes architecture debt a ratchet instead of a permanent exemption.
    """

    if raw in (None, {}):
        return []
    if not isinstance(raw, dict):
        raise ValueError("dependency_cycle_debt must be an object")
    if raw.get("mode") != "ratchet":
        raise ValueError("dependency_cycle_debt.mode must be ratchet")
    rows = raw.get("baseline_components")
    if not isinstance(rows, list):
        raise ValueError("dependency_cycle_debt.baseline_components must be an array")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_members: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("dependency cycle debt rows must be objects")
        cycle_id = str(row.get("id") or "").strip()
        owner = str(row.get("owner") or "").strip()
        target = str(row.get("target") or "").strip()
        members_raw = row.get("members")
        if not cycle_id or cycle_id in seen_ids:
            raise ValueError("dependency cycle debt ids must be non-empty and unique")
        if not owner or not target:
            raise ValueError(f"dependency cycle debt {cycle_id} must declare owner and target")
        if not isinstance(members_raw, list) or len(members_raw) < 2:
            raise ValueError(f"dependency cycle debt {cycle_id} must contain at least two members")
        members = sorted({str(member).strip() for member in members_raw if str(member).strip()})
        if len(members) != len(members_raw):
            raise ValueError(f"dependency cycle debt {cycle_id} members must be unique and non-empty")
        overlap = seen_members.intersection(members)
        if overlap:
            raise ValueError(
                f"dependency cycle debt components must be disjoint; overlap={sorted(overlap)}"
            )
        seen_ids.add(cycle_id)
        seen_members.update(members)
        normalized.append(
            {
                "id": cycle_id,
                "members": members,
                "owner": owner,
                "target": target,
                "review_after": row.get("review_after"),
            }
        )
    return normalized


def _assess_dependency_cycle_debt(
    cycles: list[list[str]],
    debt_policy: Any,
) -> dict[str, Any]:
    """Classify current cycles against a no-growth architecture debt baseline."""

    baseline = _normalized_cycle_components(debt_policy)
    baseline_sets = [(row, set(row["members"])) for row in baseline]
    current = [sorted(set(cycle)) for cycle in cycles]
    matches: list[dict[str, Any]] = []
    untracked: list[list[str]] = []
    matched_baseline_ids: set[str] = set()

    for cycle in current:
        cycle_set = set(cycle)
        candidates = [
            (row, members)
            for row, members in baseline_sets
            if cycle_set.issubset(members)
        ]
        if not candidates:
            untracked.append(cycle)
            continue
        row, members = min(candidates, key=lambda item: len(item[1]))
        matched_baseline_ids.add(str(row["id"]))
        matches.append(
            {
                "cycle": cycle,
                "baseline_id": row["id"],
                "baseline_members": row["members"],
                "classification": "UNCHANGED" if cycle_set == members else "REDUCED",
                "removed_members": sorted(members - cycle_set),
            }
        )

    resolved = [
        row for row in baseline if str(row["id"]) not in matched_baseline_ids
    ]
    current_member_count = sum(len(cycle) for cycle in current)
    baseline_member_count = sum(len(row["members"]) for row in baseline)

    if untracked:
        status = "VIOLATION"
    elif not current:
        status = "RESOLVED"
    elif any(row["classification"] == "REDUCED" for row in matches) or resolved:
        status = "REDUCED"
    else:
        status = "UNCHANGED"

    return {
        "mode": "ratchet",
        "status": status,
        "baseline_components": baseline,
        "current_cycles": current,
        "matches": matches,
        "untracked_or_expanded_cycles": untracked,
        "resolved_components": resolved,
        "baseline_member_count": baseline_member_count,
        "current_member_count": current_member_count,
        "member_delta": current_member_count - baseline_member_count,
    }


def verify(workspace: Path, policy: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    checks: dict[str, Any] = {}

    required = [workspace / item for item in policy["required_workspace_paths"]]
    missing = [str(path.relative_to(workspace)) for path in required if not path.exists()]
    checks["missing_required"] = missing
    if missing:
        errors.append("缺少当前工作区必需路径")

    forbidden = [workspace / item for item in policy["forbidden_paths"]]
    present_forbidden = [str(path.relative_to(workspace)) for path in forbidden if path.exists()]
    checks["forbidden_paths_present"] = present_forbidden
    if present_forbidden:
        errors.append("发现已退休的并行目录或兼容路径")

    core = workspace / policy["core_root"]
    allowed_core_dirs = set(policy["allowed_core_dirs"])
    actual_core_dirs = sorted(path.name for path in core.iterdir() if path.is_dir() and path.name != "__pycache__") if core.is_dir() else []
    unexpected_core_dirs = sorted(set(actual_core_dirs) - allowed_core_dirs)
    checks["unexpected_core_dirs"] = unexpected_core_dirs
    if unexpected_core_dirs:
        errors.append("agent_core 出现未声明职责目录")

    allowed_root_modules = set(policy["allowed_core_root_modules"])
    root_modules = sorted(path.name for path in core.glob("*.py")) if core.is_dir() else []
    unexpected_root_modules = sorted(set(root_modules) - allowed_root_modules)
    checks["unexpected_core_root_modules"] = unexpected_root_modules
    if unexpected_root_modules:
        errors.append("agent_core 根级出现职责不明实现文件")

    # Concrete modules may only be imported by composition. The textual check
    # is intentionally narrow and easy to audit.
    illegal_imports: list[str] = []
    for path in _python_files(core):
        relative = path.relative_to(core)
        if relative.parts and relative.parts[0] == policy["composition_dir"]:
            continue
        for line_no, line in enumerate(_read(path).splitlines(), 1):
            if re.match(r"\s*(from|import)\s+agent_modules(?:\.|\s|$)", line):
                illegal_imports.append(f"{relative}:{line_no}")
    checks["illegal_core_module_imports"] = illegal_imports
    if illegal_imports:
        errors.append("Core 在 composition 之外直接导入领域模块")

    dependency_graph, reverse_composition_imports = _core_dependency_graph(core)
    all_package_cycles = _dependency_cycles(dependency_graph)
    protected_package_cycles = [
        cycle for cycle in all_package_cycles if policy["composition_dir"] in cycle
    ]
    dependency_cycle_debt = _assess_dependency_cycle_debt(
        all_package_cycles,
        policy.get("dependency_cycle_debt"),
    )
    checks["reverse_composition_imports"] = reverse_composition_imports
    checks["package_dependency_cycles"] = protected_package_cycles
    checks["all_package_dependency_cycles"] = all_package_cycles
    checks["dependency_cycle_debt"] = dependency_cycle_debt
    if reverse_composition_imports:
        errors.append("Core 反向依赖 Composition Root")
    if dependency_cycle_debt["status"] == "VIOLATION":
        errors.append("Core 出现未登记或扩大的包依赖环")

    source_roots = [workspace / item for item in policy["source_roots"]]
    update_state_calls: list[str] = []
    for base in source_roots:
        for path in _python_files(base):
            if "graph.update_state(" in _read(path):
                update_state_calls.append(str(path.relative_to(workspace)))
    permitted_update_file = policy["single_graph_update_owner"]
    checks["graph_update_state_owners"] = update_state_calls
    if sorted(update_state_calls) != [permitted_update_file]:
        errors.append("graph.update_state 不是唯一由 LifecycleCommandRunner 持有")

    banned_tools = set(policy["banned_universal_tools"])
    tool_hits: list[str] = []
    modules_root = workspace / policy["modules_root"]
    for path in _python_files(modules_root):
        text = _read(path)
        for name in banned_tools:
            if name in text:
                tool_hits.append(f"{path.relative_to(workspace)}:{name}")
    checks["banned_universal_tool_hits"] = tool_hits
    if tool_hits:
        errors.append("领域模块重新暴露万能工具")

    missing_manifests = [item for item in policy["module_manifests"] if not (workspace / item).is_file()]
    checks["missing_module_manifests"] = missing_manifests
    if missing_manifests:
        errors.append("模块缺少权威 manifest")

    size_violations: list[str] = []
    for item in policy["line_limits"]:
        path = workspace / item["path"]
        if not path.is_file():
            size_violations.append(f"missing:{item['path']}")
            continue
        lines = len(_read(path).splitlines())
        if lines > int(item["max_lines"]):
            size_violations.append(f"{item['path']}={lines}>{item['max_lines']}")
    checks["line_limit_violations"] = size_violations
    if size_violations:
        errors.append("职责 Owner 超过约定边界，应拆分或合并")

    enforce_clean_artifacts = bool(policy.get("enforce_clean_artifacts", True))
    runtime_artifacts: list[str] = []
    for item in policy["runtime_roots"]:
        base = workspace / item
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.name != ".gitkeep":
                runtime_artifacts.append(str(path.relative_to(workspace)))
    checks["runtime_artifacts"] = runtime_artifacts
    if enforce_clean_artifacts and runtime_artifacts:
        errors.append("当前源码工作区包含运行态数据")

    generated_dependency_roots = {".venv", "node_modules"}
    caches = [
        str(p.relative_to(workspace))
        for p in workspace.rglob("__pycache__")
        if p.is_dir() and not generated_dependency_roots.intersection(p.parts)
    ]
    caches += [
        str(p.relative_to(workspace))
        for p in workspace.rglob("*.pyc")
        if not generated_dependency_roots.intersection(p.parts)
    ]
    checks["cache_artifacts"] = caches
    if enforce_clean_artifacts and caches:
        errors.append("当前工作区包含 Python 缓存")

    configuration = policy.get("configuration", {})
    configuration_errors: list[str] = []
    configuration_checks: dict[str, Any] = {}
    all_documented: set[str] = set()
    for template in configuration.get("templates", []):
        path = workspace / template["path"]
        required_vars = set(template.get("required_variables", []))
        keys = _env_template_keys(path)
        missing_vars = sorted(required_vars - keys)
        configuration_checks[template["path"]] = {
            "exists": path.is_file(),
            "missing_variables": missing_vars,
        }
        all_documented.update(keys)
        if not path.is_file() or missing_vars:
            configuration_errors.append(template["path"])

    source_paths = [workspace / item for item in configuration.get("source_paths", [])]
    used_env = _environment_variables(source_paths)
    ignored_env = set(configuration.get("ignored_environment_variables", []))
    undocumented_env = sorted(used_env - all_documented - ignored_env)
    configuration_checks["undocumented_runtime_environment_variables"] = undocumented_env
    if undocumented_env:
        configuration_errors.append("runtime_env_undocumented")

    model_config = configuration.get("model_config", {})
    if model_config:
        model_path = workspace / model_config["path"]
        model_source = _read(model_path)
        required_markers = set(model_config.get("required_markers", []))
        missing_markers = sorted(marker for marker in required_markers if marker not in model_source)
        forbidden_markers = [marker for marker in model_config.get("forbidden_markers", []) if marker in model_source]
        configuration_checks["model_config"] = {
            "path": model_config["path"],
            "missing_markers": missing_markers,
            "forbidden_markers": forbidden_markers,
        }
        if missing_markers or forbidden_markers:
            configuration_errors.append("model_settings_not_externalized")

    checks["configuration_templates"] = configuration_checks
    if configuration_errors:
        errors.append("运行配置模板不完整、变量未文档化或模型设置重新硬编码")

    matrix = workspace / policy["convergence_matrix"]
    matrix_text = _read(matrix)
    abstraction_rule = str(policy.get("new_abstraction_rule") or "")
    matrix_ok = (
        matrix.is_file()
        and "新增抽象" in matrix_text
        and "替换或删除" in matrix_text
        and "允许变更路径" in matrix_text
        and abstraction_rule == "target_snapshot_record"
    )
    checks["convergence_matrix"] = str(matrix.relative_to(workspace)) if matrix.exists() else "missing"
    checks["new_abstraction_rule"] = abstraction_rule
    if not matrix_ok:
        errors.append("缺少可执行的新抽象替换/删除与 target 范围矩阵")

    status = "PASS" if not errors else "FAIL"
    architecture_status = (
        "FAIL"
        if errors
        else "PASS_WITH_DEBT"
        if dependency_cycle_debt["current_cycles"]
        else "PASS"
    )
    return {
        "status": status,
        "architecture_status": architecture_status,
        "architecture_debt_status": dependency_cycle_debt["status"],
        "errors": errors,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--policy", default="governance/architecture-policy.json")
    args = parser.parse_args()
    workspace = Path(args.workspace_root).resolve()
    policy_path = workspace / args.policy
    controller = workspace / "skill-system" / "controller"
    if str(controller) not in sys.path:
        sys.path.insert(0, str(controller))
    from architecture_policy import load_effective_policy  # type: ignore

    try:
        policy, policy_meta = load_effective_policy(workspace, policy_path)
        result = verify(workspace, policy)
        result["architecture_policy"] = policy_meta
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {
            "status": "FAIL",
            "errors": [f"architecture policy resolution failed: {exc}"],
            "checks": {},
            "architecture_policy": {"mode": "invalid"},
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
