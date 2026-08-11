from __future__ import annotations

import sys
from pathlib import Path
import unittest


class V2018SemanticVocabularyBatchTest(unittest.TestCase):
    def test_capability_independent_vocabulary_contract(self) -> None:
        root = Path(__file__).resolve().parents[2]
        src = root / "services" / "agent-service" / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

        from agent_core.modules.registry import ModuleRegistry
        from agent_modules.ecommerce.module import EcommerceModule

        registry = ModuleRegistry([EcommerceModule()])
        snapshot = registry.semantic_vocabulary_snapshot()
        outputs = {row["output_id"]: row for row in snapshot["outputs"]}
        self.assertFalse(snapshot["availability_exposed"])
        self.assertFalse(snapshot["tool_names_exposed"])
        self.assertIn("shipment.current_status", outputs)
        self.assertIn("shipment.eta", outputs)
        self.assertIn("courier.contact.phone", outputs)
        self.assertEqual(outputs["courier.contact.phone"]["subject_type"], "courier")
        self.assertNotIn("legacy_effect_aliases", outputs["shipment.current_status"])
        rendered = repr(snapshot)
        self.assertNotIn("get_order_logistics", rendered)
        self.assertNotIn("report_unsupported_request", rendered)

        aliases = registry.legacy_semantic_output_aliases()
        self.assertEqual(
            aliases["order.query_logistics:order"],
            ("shipment.current_status", "shipment.eta", "shipment.tracking"),
        )
        self.assertFalse(any("courier.contact.phone" in values for values in aliases.values()))


if __name__ == "__main__":
    unittest.main()
