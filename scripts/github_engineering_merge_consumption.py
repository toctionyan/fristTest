#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "skill-system" / "controller"
if str(CONTROL) not in sys.path:
    sys.path.insert(0, str(CONTROL))

from engineering_merge_consumption import (  # type: ignore  # noqa: E402
    EngineeringMergeConsumptionError,
    classify_consumption,
)
from engineering_merge_grant import EngineeringMergeGrantError  # type: ignore  # noqa: E402


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EngineeringMergeConsumptionError(f"JSON object required: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grant", required=True)
    parser.add_argument("--combined-status", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()
    try:
        result = classify_consumption(
            _load(args.grant),
            combined_status=_load(args.combined_status),
        )
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.github_output:
            with Path(args.github_output).open("a", encoding="utf-8") as handle:
                handle.write(f"status={result['status']}\n")
                handle.write(f"reservation_allowed={str(result['reservation_allowed']).lower()}\n")
                handle.write(f"anchor_sha={result['anchor_sha']}\n")
                handle.write(f"context={result['context']}\n")
                handle.write(f"consumption_state_sha256={result['consumption_state_sha256']}\n")
        return 0
    except (OSError, json.JSONDecodeError, ValueError, EngineeringMergeGrantError, EngineeringMergeConsumptionError) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "error": str(exc),
                    "reservation_allowed": False,
                    "merge_allowed": False,
                    "deploy_allowed": False,
                    "production_closed": False,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
