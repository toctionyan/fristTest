from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


RELATIVE = "services/agent-service/tests/runtime/test_goal_coverage_runtime.py"
EXPECTED_PREIMAGE_SHA256 = "96398f17db9855e86eb11f4d381b17f2e838ae1eca0edc4f41c5a03004c6fab3"


def replace_once(source: str, old: str, new: str, *, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one preimage, found {count}")
    return source.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: b28_quick_gate_repair_v2.py WORKSPACE")
    root = Path(sys.argv[1]).resolve()
    path = root / RELATIVE
    source = path.read_text(encoding="utf-8")
    actual = hashlib.sha256(source.encode()).hexdigest()
    if actual != EXPECTED_PREIMAGE_SHA256:
        raise SystemExit(
            f"history recall fixture preimage changed: expected={EXPECTED_PREIMAGE_SHA256} actual={actual}"
        )

    source = replace_once(
        source,
        '''
        "grounded_execution_plan": {
            "status": "RUNNING",
            "goals": [{
                "goal_id": "recall",
                "goal_type": "query",
                "required": True,
                "coverage_status": "PENDING",
                "covered_by_step_ids": [],
                "covered_by_terminal_tools": [],
            }],
            "steps": [],
        },
''',
        "\n",
        label="remove retired history recall plan dictionary",
    )
    source = replace_once(
        source,
        '''                    "raw_description": "确认刚才开票的订单",
                },
                "expected_tools": [],
            }],
        })
    model = ScriptedChatModel([{
''',
        '''                    "raw_description": "确认刚才开票的订单",
                },
                "expected_tools": [],
            }],
        })
    install_test_plan_authority(
        state,
        goals=[{
            "goal_id": "recall",
            "goal_type": "query",
            "required": True,
        }],
    )
    model = ScriptedChatModel([{
''',
        label="install authoritative history recall plan fixture",
    )
    path.write_text(source, encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "path": RELATIVE,
        "before_sha256": actual,
        "after_sha256": hashlib.sha256(source.encode()).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
