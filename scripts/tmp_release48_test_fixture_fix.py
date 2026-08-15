#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

TEST_PATH = "services/agent-service/tests/runtime/test_release48_registered_output_exactness_adjudication.py"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    path = Path(args.workspace).resolve() / TEST_PATH
    text = path.read_text(encoding="utf-8")
    old = '    assert "capability" not in json.dumps(third["payload"]["REGISTERED_OUTPUT_EXACTNESS_RISK"], ensure_ascii=False).casefold()\n'
    new = (
        '    risk_payload = third["payload"]["REGISTERED_OUTPUT_EXACTNESS_RISK"]\n'
        '    assert risk_payload["capability_registry_consulted"] is False\n'
        '    assert "tool_name" not in json.dumps(risk_payload, ensure_ascii=False).casefold()\n'
        '    assert "available" not in json.dumps(risk_payload, ensure_ascii=False).casefold()\n'
    )
    if text.count(old) != 1:
        raise SystemExit("expected one Release 48 regression assertion anchor")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
