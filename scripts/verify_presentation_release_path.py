#!/usr/bin/env python3
"""Static checks for the RuntimeOutcome -> Presentation release boundary."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def verify(workspace: Path) -> dict[str, Any]:
    errors: list[str] = []
    checks: dict[str, Any] = {}
    api_hits: list[str] = []
    for path in (workspace / "services/agent-service/app/api").rglob("*.py"):
        text = _read(path)
        if "current_final_answer" in text or "runtime_outcome" in text:
            api_hits.append(str(path.relative_to(workspace)))
    checks["api_direct_release_hits"] = api_hits
    if api_hits:
        errors.append("api_layer_contains_direct_final_answer_or_runtime_outcome_writes")

    required_markers = {
        "services/agent-service/app/services/response_projector.py": ["fail closed", "runtime_outcome", "Presentation"],
        "services/agent-service/src/agent_core/lifecycle/tool_execution_runtime.py": ["from_tool_result", "runtime_outcome"],
        "services/agent-service/src/agent_core/presentation/outcome.py": ["presentation_from_outcome", "coerce_runtime_outcome"],
        "services/agent-service/src/agent_core/lifecycle/publish_runtime.py": ["runtime_outcome", "public projection"],
    }
    marker_report: dict[str, list[str]] = {}
    for rel, markers in required_markers.items():
        text = _read(workspace / rel)
        missing = [m for m in markers if m not in text]
        marker_report[rel] = missing
        if missing:
            errors.append(f"release_boundary_marker_missing:{rel}:{','.join(missing)}")
    checks["required_markers_missing"] = marker_report

    # Final answer may exist for legacy state compatibility, but user-visible
    # release must flow through the projector/finalizer and not be authored by API routes.
    direct_return_patterns: list[str] = []
    for path in (workspace / "services/agent-service/app").rglob("*.py"):
        if "api" in path.parts:
            continue
        text = _read(path)
        if re.search(r"return\s+\{[^\n]*['\"]answer['\"]\s*:", text):
            direct_return_patterns.append(str(path.relative_to(workspace)))
    checks["direct_answer_dict_returns"] = direct_return_patterns
    if direct_return_patterns:
        errors.append("application_layer_direct_answer_return_detected")

    return {"status": "PASS" if not errors else "FAIL", "errors": errors, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    args = parser.parse_args()
    result = verify(Path(args.workspace_root).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
