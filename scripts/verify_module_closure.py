#!/usr/bin/env python3
"""Validate that each installed module capability is vertically closed.

This is intentionally static and dependency-free: it validates the module
manifest, executor file, single-source definition markers, presentation
contract ownership, and module-level unsupported behavior without importing
runtime code.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any


TEST_CONTRACT_DIMENSIONS = {
    "schema",
    "permit",
    "execution",
    "presentation",
    "negative_substitution",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _contract_file_name(contract_id: str) -> str:
    # commerce.order_list@1 -> ecommerce_order_list_v1.json is module-specific,
    # runtime.resource_list@1 -> runtime_resource_list_v1.json is core-specific.
    base = contract_id.replace(".", "_").replace("@", "_v") + ".json"
    if base.startswith("commerce_"):
        return "e" + base  # commerce_* contracts are stored as ecommerce_*
    return base


def _presentation_contract_exists(workspace: Path, module_root: Path, contract_id: str) -> bool:
    candidates = [
        module_root / "presentation" / _contract_file_name(contract_id),
        workspace / "services/agent-service/src/agent_core/presentation/contracts" / _contract_file_name(contract_id),
    ]
    return any(path.is_file() for path in candidates)


def _executor_markers(text: str, module_id: str) -> list[str]:
    markers: list[str] = []
    if module_id == "ecommerce":
        markers = ["DEFINITION", "EcommerceCapabilityDefinition", "execute"]
    else:
        markers = ["CONTRACT", "SCHEMA", "execute", "PRESENTATION_CONTRACT"]
    return [marker for marker in markers if marker not in text]


def _selector_exists(workspace: Path, raw_selector: str) -> bool:
    path_text, separator, selector = raw_selector.partition("::")
    path = Path(path_text)
    if (
        not separator
        or not selector
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".py"
    ):
        return False
    resolved = (workspace / path).resolve()
    try:
        resolved.relative_to(workspace.resolve())
        tree = ast.parse(resolved.read_text(encoding="utf-8"), filename=str(resolved))
    except (ValueError, OSError, SyntaxError, UnicodeDecodeError):
        return False
    selector_name = selector.split("[", 1)[0]
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == selector_name
        for node in tree.body
    )


def verify(workspace: Path, manifest_paths: list[str] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    module_reports: list[dict[str, Any]] = []
    manifests = [workspace / p for p in manifest_paths] if manifest_paths else sorted((workspace / "services/agent-service/src/agent_modules").glob("*/module_manifest.json"))

    for manifest_path in manifests:
        if not manifest_path.is_file():
            errors.append(f"missing_manifest:{manifest_path.relative_to(workspace)}")
            continue
        manifest = _load_json(manifest_path)
        module_root = manifest_path.parent
        module_id = str(manifest.get("module_id") or module_root.name)
        capabilities = manifest.get("capabilities") or []
        module_errors: list[str] = []
        seen_keys: set[str] = set()
        seen_tools: set[str] = set()
        owned_contracts = set((manifest.get("ownership") or {}).get("presentation_contracts") or [])

        if not capabilities:
            module_errors.append("manifest_has_no_capabilities")
        unsupported = str(manifest.get("unsupported_behavior") or "").lower()
        if "substitute" not in unsupported and "替代" not in unsupported:
            module_errors.append("unsupported_behavior_does_not_forbid_substitution")

        test_paths = manifest.get("tests") or []
        missing_tests = [p for p in test_paths if not (workspace / "services/agent-service" / p).exists() and not (workspace / p).exists()]
        if missing_tests:
            module_errors.append("missing_declared_tests:" + ",".join(missing_tests))

        for cap in capabilities:
            key = str(cap.get("key") or "")
            tool_name = str(cap.get("tool_name") or "")
            executor = str(cap.get("executor") or "")
            presentation_contract = str(cap.get("presentation_contract") or "")
            test_contract = cap.get("test_contract")
            prefix = f"capability:{key or '<missing-key>'}"
            if not key or key in seen_keys:
                module_errors.append(f"{prefix}:missing_or_duplicate_key")
            seen_keys.add(key)
            if not tool_name or tool_name in seen_tools:
                module_errors.append(f"{prefix}:missing_or_duplicate_tool_name")
            seen_tools.add(tool_name)
            if not isinstance(test_contract, dict):
                module_errors.append(f"{prefix}:missing_test_contract")
            else:
                dimensions = {str(value) for value in test_contract}
                if dimensions != TEST_CONTRACT_DIMENSIONS:
                    module_errors.append(
                        f"{prefix}:test_contract_dimensions_mismatch:"
                        f"missing={sorted(TEST_CONTRACT_DIMENSIONS - dimensions)},"
                        f"extra={sorted(dimensions - TEST_CONTRACT_DIMENSIONS)}"
                    )
                for dimension in sorted(TEST_CONTRACT_DIMENSIONS & dimensions):
                    selector = str(test_contract.get(dimension) or "")
                    if not _selector_exists(workspace, selector):
                        module_errors.append(
                            f"{prefix}:test_contract_selector_missing:{dimension}:{selector}"
                        )
            if not executor:
                module_errors.append(f"{prefix}:missing_executor")
                continue
            executor_path = module_root / executor
            if not executor_path.is_file():
                module_errors.append(f"{prefix}:executor_missing:{executor}")
                continue
            text = _read(executor_path)
            missing_markers = _executor_markers(text, module_id)
            if missing_markers:
                module_errors.append(f"{prefix}:executor_missing_markers:{','.join(missing_markers)}")
            if key and key not in text:
                module_errors.append(f"{prefix}:executor_does_not_declare_key")
            if tool_name and tool_name not in text:
                module_errors.append(f"{prefix}:executor_does_not_declare_tool_name")
            if not presentation_contract:
                module_errors.append(f"{prefix}:missing_presentation_contract")
            elif presentation_contract not in owned_contracts:
                module_errors.append(f"{prefix}:presentation_contract_not_owned:{presentation_contract}")
            elif not _presentation_contract_exists(workspace, module_root, presentation_contract):
                module_errors.append(f"{prefix}:presentation_contract_file_missing:{presentation_contract}")

        module_reports.append({
            "module_id": module_id,
            "manifest": str(manifest_path.relative_to(workspace)),
            "capability_count": len(capabilities),
            "errors": module_errors,
        })
        errors.extend(f"{module_id}:{e}" for e in module_errors)

    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "modules": module_reports}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--manifest", action="append", default=[])
    args = parser.parse_args()
    result = verify(Path(args.workspace_root).resolve(), args.manifest or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
