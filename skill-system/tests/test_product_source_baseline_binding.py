from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "skill-system/registry/product-source-baseline.json"
IGNORED_PARTS = {".venv", ".pytest_cache", "node_modules", "__pycache__"}


class ProductSourceBaselineBindingTests(unittest.TestCase):
    def test_baseline_matches_current_protected_snapshot(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        generated_from = str(baseline.get("generated_from") or "")
        self.assertRegex(generated_from, r"^git:[0-9a-f]{40}$")
        protected_roots = tuple(str(value) for value in baseline.get("protected_roots") or ())
        self.assertTrue(protected_roots)

        current: dict[str, str] = {}
        for name in protected_roots:
            base = ROOT / name
            if not base.exists():
                continue
            for path in sorted(item for item in base.rglob("*") if item.is_file()):
                if any(part in IGNORED_PARTS for part in path.parts):
                    continue
                relative = path.relative_to(ROOT).as_posix()
                current[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

        recorded = {str(key): str(value) for key, value in (baseline.get("files") or {}).items()}
        self.assertEqual(int(baseline.get("file_count") or 0), len(current))
        self.assertEqual(set(recorded), set(current))
        for relative, actual_sha in current.items():
            self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", recorded[relative]), relative)
            self.assertEqual(recorded[relative], actual_sha, relative)

    def test_baseline_does_not_claim_production_closure(self) -> None:
        task_ledger = json.loads((ROOT / "governance/task-ledger.json").read_text(encoding="utf-8"))
        serialized = json.dumps(task_ledger, ensure_ascii=False)
        self.assertNotIn('"production_closed": true', serialized)


if __name__ == "__main__":
    unittest.main()
