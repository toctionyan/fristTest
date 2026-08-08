from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "skill-system/registry/product-source-baseline.json"
CANDIDATE_HEAD = "2208e958c9813dd8b608dd83b3a059b7decad87a"
EXPECTED = {
    "services/agent-service/src/agent_core/config.py": "aee9c165ba13d7b7bb47866cb41fc7817fd181d11df5404d8f55a8417e669aaa",
    "services/agent-service/src/agent_core/lifecycle/protocol.py": "28a774c2e01c189a39255ed1bb3e858b574a35cc90e55167345bbbac60e34599",
    "services/agent-service/scripts/verify_preprod_conversation_smoke.py": "fda048e86f446728f5cdafd18b06ce586e9fd921df474ae6ae6790246619d8bf",
    "services/agent-service/tests/runtime/test_wp08_attempt4_graph_semantic_repair.py": "72d3713979979e3517cd160c0fabffd1836ac56158f418e83e7c2265c50a56d2",
}


class Attempt4ProtectedBaselineBindingTests(unittest.TestCase):
    def test_baseline_is_bound_to_exact_tested_protected_snapshot(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(baseline.get("generated_from"), f"git:{CANDIDATE_HEAD}")
        self.assertEqual(baseline.get("file_count"), 557)
        files = baseline.get("files") or {}
        for relative, expected_sha in EXPECTED.items():
            path = ROOT / relative
            actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual_sha, expected_sha, relative)
            self.assertEqual(files.get(relative), expected_sha, relative)

    def test_baseline_refresh_did_not_claim_production_closure(self) -> None:
        task_ledger = json.loads((ROOT / "governance/task-ledger.json").read_text(encoding="utf-8"))
        serialized = json.dumps(task_ledger, ensure_ascii=False)
        self.assertNotIn('"production_closed": true', serialized)


if __name__ == "__main__":
    unittest.main()
