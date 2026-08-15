#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PATH = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"

OLD = '''        outputs.append({
            "output_id": output_id,
            "subject_type": subject_type,
            "effect_kinds": list(dict.fromkeys(effect_kinds)),
            "description": description,
            "included_result_meanings": list(dict.fromkeys(included_result_meanings)),
            "excluded_result_meanings": list(dict.fromkeys(excluded_result_meanings)),
        })
'''

NEW = '''        projected_output = {
            "output_id": output_id,
            "subject_type": subject_type,
            "effect_kinds": list(dict.fromkeys(effect_kinds)),
            "description": description,
        }
        # Boundaries are optional vocabulary-v2 metadata. Omitting empty fields
        # preserves the legacy meaning-only projection shape for older snapshots
        # while carrying explicit boundaries whenever the module declares them.
        if included_result_meanings:
            projected_output["included_result_meanings"] = list(dict.fromkeys(included_result_meanings))
        if excluded_result_meanings:
            projected_output["excluded_result_meanings"] = list(dict.fromkeys(excluded_result_meanings))
        outputs.append(projected_output)
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    path = Path(args.workspace).resolve() / PATH
    text = path.read_text(encoding="utf-8")
    count = text.count(OLD)
    if count != 1:
        raise SystemExit(f"alignment boundary compatibility anchor: expected one, found {count}")
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
