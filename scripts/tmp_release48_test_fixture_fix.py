#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

TEST_PATH = "services/agent-service/tests/runtime/test_release48_registered_output_exactness_adjudication.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    path = Path(args.workspace).resolve() / TEST_PATH
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '    assert "capability" not in json.dumps(third["payload"]["REGISTERED_OUTPUT_EXACTNESS_RISK"], ensure_ascii=False).casefold()\n',
        (
            '    risk_payload = third["payload"]["REGISTERED_OUTPUT_EXACTNESS_RISK"]\n'
            '    assert risk_payload["capability_registry_consulted"] is False\n'
            '    assert "tool_name" not in json.dumps(risk_payload, ensure_ascii=False).casefold()\n'
            '    assert "available" not in json.dumps(risk_payload, ensure_ascii=False).casefold()\n'
        ),
        "capability-isolation assertion",
    )
    text = replace_once(
        text,
        '    assert verdict.reason_code == "requested_output_exactness_confirmed"\n',
        '    assert verdict.reason_code == "goal_alignment_candidate_blind_dependency_reaudit_exact"\n',
        "canonical exact verdict reason",
    )

    path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
