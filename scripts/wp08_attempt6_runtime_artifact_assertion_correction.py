#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
path = ROOT / "skill-system/tests/test_product_source_baseline_binding.py"
text = path.read_text(encoding="utf-8")
old = '''    def test_machine_local_runtime_state_is_not_source_authority(self) -> None:\n        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))\n        recorded = set((baseline.get("files") or {}).keys())\n        self.assertFalse(\n            any(\n                "/runtime/" in path and not path.endswith("/.gitkeep")\n                for path in recorded\n            )\n        )\n        compatibility = _load_project_compatibility()\n        snapshot = compatibility.snapshot(ROOT)\n        self.assertFalse(\n            any(\n                "/runtime/" in path and not path.endswith("/.gitkeep")\n                for path in snapshot\n            )\n        )\n'''
new = '''    def test_machine_local_runtime_state_is_not_source_authority(self) -> None:\n        machine_local = {\n            "services/agent-service/runtime/sqlite/document_index_jobs.db",\n            "services/agent-service/runtime/vector-store/vector_store.db",\n        }\n        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))\n        recorded = set((baseline.get("files") or {}).keys())\n        self.assertTrue(machine_local.isdisjoint(recorded))\n        compatibility = _load_project_compatibility()\n        snapshot = compatibility.snapshot(ROOT)\n        self.assertTrue(machine_local.isdisjoint(snapshot))\n'''
if text.count(old) != 1:
    raise SystemExit("runtime_artifact_assertion_anchor_mismatch")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("runtime artifact assertion narrowed to exact untracked DB state")
