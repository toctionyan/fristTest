#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    service = workspace / "services" / "agent-service"
    sys.path[:0] = [str(service), str(service / "src")]

    from agent_core.composition import get_runtime_registry
    from quality.stage3_campaign_verifier import verify_campaign_case

    campaign_dir = service / "quality" / "stage3_campaigns"
    manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    all_errors: list[dict[str, Any]] = []
    total = 0
    for spec in manifest["campaigns"]:
        payload = json.loads((campaign_dir / spec["file"]).read_text(encoding="utf-8"))
        failures: list[dict[str, Any]] = []
        for case in payload["cases"]:
            errors = verify_campaign_case(case, registry=get_runtime_registry().capabilities)
            if errors:
                failures.append({"case_id": case.get("case_id"), "errors": errors})
        count = len(payload["cases"])
        passed = count - len(failures)
        total += count
        all_errors.extend(failures)
        results.append({
            "name": payload["name"],
            "locked": bool(payload["locked"]),
            "case_count": count,
            "passed": passed,
            "failed": len(failures),
            "accuracy": passed / count if count else 0.0,
        })
    report = {
        "report_version": "stage3-semantic-context-evidence@1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace),
        "method": "deterministic_production_projection_against_locked_expectations",
        "real_model_claimed": False,
        "campaign_count": len(results),
        "case_count": total,
        "passed": total - len(all_errors),
        "failed": len(all_errors),
        "accuracy": (total - len(all_errors)) / total if total else 0.0,
        "zero_p0_p1_failures": not all_errors,
        "campaigns": results,
        "failures": all_errors[:100],
        "status": "PASS" if not all_errors else "FAIL",
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not all_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
