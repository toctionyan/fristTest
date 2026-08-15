#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PATH = "services/agent-service/tests/architecture/test_wp08_attempt6_semantic_presentation_repair.py"

OLD = '''    for row in snapshot["outputs"]:\n        assert set(row) == {"output_id", "subject_type", "effect_kinds", "description"}\n'''

NEW = '''    for row in snapshot["outputs"]:\n        assert set(row) == {\n            "output_id",\n            "subject_type",\n            "effect_kinds",\n            "description",\n            "included_result_meanings",\n            "excluded_result_meanings",\n        }\n        assert isinstance(row["included_result_meanings"], list)\n        assert isinstance(row["excluded_result_meanings"], list)\n        assert "tool_name" not in row\n        assert "available" not in row\n        assert "legacy_effect_aliases" not in row\n'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    path = Path(args.workspace).resolve() / PATH
    text = path.read_text(encoding="utf-8")
    count = text.count(OLD)
    if count != 1:
        raise SystemExit(f"semantic vocabulary v2 architecture contract anchor: expected one, found {count}")
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
