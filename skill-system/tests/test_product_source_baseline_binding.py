from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "skill-system/registry/product-source-baseline.json"
PROJECT_COMPAT_PATH = ROOT / "skill-system/controller/project_compatibility.py"


def _load_project_compatibility():
    spec = importlib.util.spec_from_file_location(
        "product_source_project_compatibility", PROJECT_COMPAT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProductSourceBaselineBindingTests(unittest.TestCase):
    def test_baseline_matches_current_git_tracked_protected_snapshot(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        generated_from = str(baseline.get("generated_from") or "")
        self.assertRegex(generated_from, r"^git:[0-9a-f]{40}$")
        protected_roots = tuple(
            str(value) for value in baseline.get("protected_roots") or ()
        )
        self.assertTrue(protected_roots)

        raw = subprocess.check_output(
            ["git", "ls-files", "-z", "--", *protected_roots], cwd=ROOT
        )
        tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
        current = {
            relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            for relative in tracked
        }
        recorded = {
            str(key): str(value)
            for key, value in (baseline.get("files") or {}).items()
        }
        self.assertEqual(int(baseline.get("file_count") or 0), len(current))
        self.assertEqual(set(recorded), set(current))
        for relative, actual_sha in current.items():
            self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", recorded[relative]), relative)
            self.assertEqual(recorded[relative], actual_sha, relative)

    def test_machine_local_runtime_state_is_not_source_authority(self) -> None:
        machine_local = {
            "services/agent-service/runtime/sqlite/document_index_jobs.db",
            "services/agent-service/runtime/vector-store/vector_store.db",
        }
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        recorded = set((baseline.get("files") or {}).keys())
        self.assertTrue(machine_local.isdisjoint(recorded))
        compatibility = _load_project_compatibility()
        snapshot = compatibility.snapshot(ROOT)
        self.assertTrue(machine_local.isdisjoint(snapshot))

    def test_baseline_does_not_claim_production_closure(self) -> None:
        task_ledger = json.loads(
            (ROOT / "governance/task-ledger.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(task_ledger, ensure_ascii=False)
        self.assertNotIn('"production_closed": true', serialized)


if __name__ == "__main__":
    unittest.main()
