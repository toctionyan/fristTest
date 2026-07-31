#!/usr/bin/env python3
"""Run the live real-model bundle as one production-certification component."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "services" / "agent-service"
for path in (AGENT_ROOT, AGENT_ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_core.model_calls import run_certification_bundle  # noqa: E402
from production_certification_contract import (  # noqa: E402
    ProductionCertificationError,
    production_session_evidence,
)


def main() -> int:
    try:
        session = production_session_evidence(component="real_model")
        bundle = run_certification_bundle(workspace_root=ROOT)
        status = str(bundle.get("status") or "FAIL")
        result = {
            "contract": "production-real-model-certification@1",
            "status": status,
            "production_session": session,
            "real_model_bundle": bundle,
        }
        if status != "PASS":
            result["reason"] = str(bundle.get("reason") or "real_model_certification_not_passed")
            result["error_code"] = str(bundle.get("error_code") or "real_model_certification_not_passed")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if status == "PASS" else (78 if status == "BLOCKED_BY_ENVIRONMENT" else 1)
    except ProductionCertificationError as exc:
        print(json.dumps({
            "contract": "production-real-model-certification@1",
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "reason": exc.code,
            "error": str(exc),
        }, ensure_ascii=False))
        return 78 if exc.environment_blocked else 1
    except Exception as exc:
        print(json.dumps({
            "contract": "production-real-model-certification@1",
            "status": "FAIL",
            "reason": "real_model_component_exception",
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
