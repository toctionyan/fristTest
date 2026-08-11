from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest


class V2018SemanticCapabilityInventoryTest(unittest.TestCase):
    def test_dump_ecommerce_semantic_inventory(self) -> None:
        root = Path(__file__).resolve().parents[2]
        src = root / "services" / "agent-service" / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from agent_modules.ecommerce.capabilities import CAPABILITIES

        rows = []
        for definition in CAPABILITIES:
            contract = definition.contract
            planning = contract.planning_contract
            rows.append({
                "key": contract.key,
                "tool_name": contract.tool_name,
                "execution_kind": contract.execution_kind,
                "completion_effects": list(contract.completion_effects),
                "support_effects": list(contract.support_effects),
                "produces": [item.as_dict() for item in planning.produces] if planning else [],
                "completion": planning.completion.as_dict() if planning else None,
            })
        print("V2018_SEMANTIC_CAPABILITY_INVENTORY=" + json.dumps(rows, ensure_ascii=False, sort_keys=True), flush=True)
        self.assertGreater(len(rows), 10)


if __name__ == "__main__":
    unittest.main()
