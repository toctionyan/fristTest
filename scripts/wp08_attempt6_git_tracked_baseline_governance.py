#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
BASELINE_PATH = ROOT / "skill-system/registry/product-source-baseline.json"
INTEGRITY_TEST = ROOT / "skill-system/tests/test_product_source_baseline_binding.py"
PROJECT_COMPAT = ROOT / "skill-system/controller/project_compatibility.py"
EXPECTED_PRODUCT = {
    "services/agent-service/app/services/lifecycle_command_runner.py",
    "services/agent-service/pyproject.toml",
    "services/agent-service/scripts/verify_preprod_conversation_smoke.py",
    "services/agent-service/src/agent_core/config.py",
    "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py",
    "services/agent-service/tests/runtime/test_wp08_attempt6_release_repairs.py",
    "services/agent-service/uv.lock",
}


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"replacement_anchor_count:{path.relative_to(ROOT)}:{count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
protected_roots = tuple(str(value) for value in baseline["protected_roots"])
raw = subprocess.check_output(
    ["git", "ls-files", "-z", "--", *protected_roots], cwd=ROOT
)
tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
current = {
    relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for relative in tracked
}
old = {str(key): str(value) for key, value in baseline["files"].items()}
changed = {
    path for path in set(current) | set(old)
    if current.get(path) != old.get(path)
}
if changed != EXPECTED_PRODUCT:
    raise SystemExit(
        "unexpected_protected_delta:"
        + json.dumps(
            {"expected": sorted(EXPECTED_PRODUCT), "actual": sorted(changed)},
            sort_keys=True,
        )
    )
if len(old) != 557 or len(current) != 558:
    raise SystemExit(
        f"unexpected_baseline_count:old={len(old)}:current={len(current)}"
    )

prior_release = baseline.get("source_release_sha256")
baseline["generated_from"] = "git:" + os.environ["CANDIDATE_HEAD"]
baseline["generated_at"] = (
    datetime.now(timezone.utc)
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z")
)
baseline["file_count"] = len(current)
baseline["files"] = dict(sorted(current.items()))
if baseline.get("source_release_sha256") != prior_release:
    raise SystemExit("source_release_sha256_changed")
BASELINE_PATH.write_text(
    json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

INTEGRITY_TEST.write_text(
    '''from __future__ import annotations

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
        tracked = sorted(item.decode("utf-8") for item in raw.split(b"\\0") if item)
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
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        recorded = set((baseline.get("files") or {}).keys())
        self.assertFalse(
            any(
                "/runtime/" in path and not path.endswith("/.gitkeep")
                for path in recorded
            )
        )
        compatibility = _load_project_compatibility()
        snapshot = compatibility.snapshot(ROOT)
        self.assertFalse(
            any(
                "/runtime/" in path and not path.endswith("/.gitkeep")
                for path in snapshot
            )
        )

    def test_baseline_does_not_claim_production_closure(self) -> None:
        task_ledger = json.loads(
            (ROOT / "governance/task-ledger.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(task_ledger, ensure_ascii=False)
        self.assertNotIn('"production_closed": true', serialized)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

replace_once(
    PROJECT_COMPAT,
    "import json\nimport sys\n",
    "import json\nimport subprocess\nimport sys\n",
)
replace_once(
    PROJECT_COMPAT,
    '''IGNORED_PARTS = {".venv", ".pytest_cache", "node_modules", "__pycache__"}\n''',
    '''IGNORED_PARTS = {".venv", ".pytest_cache", "node_modules", "__pycache__"}\nMACHINE_LOCAL_PARTS = {"runtime"}\n''',
)
replace_once(
    PROJECT_COMPAT,
    '''def snapshot(root: Path = ROOT) -> dict[str, str]:\n    root = root.resolve()\n    rows: dict[str, str] = {}\n    for name in PROTECTED_NAMES:\n        protected_root = root / name\n        if not protected_root.exists():\n            continue\n        for path in sorted(item for item in protected_root.rglob("*") if item.is_file()):\n            if any(part in IGNORED_PARTS for part in path.parts):\n                continue\n            rows[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()\n    return rows\n''',
    '''def _git_tracked_protected_paths(root: Path) -> list[str] | None:\n    try:\n        top = subprocess.run(\n            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],\n            text=True,\n            capture_output=True,\n            check=False,\n            timeout=10,\n        )\n    except (OSError, subprocess.SubprocessError):\n        return None\n    if top.returncode != 0:\n        return None\n    try:\n        if Path(top.stdout.strip()).resolve() != root.resolve():\n            return None\n    except (OSError, RuntimeError):\n        return None\n    listed = subprocess.run(\n        ["git", "-C", str(root), "ls-files", "-z", "--", *PROTECTED_NAMES],\n        capture_output=True,\n        check=False,\n        timeout=10,\n    )\n    if listed.returncode != 0:\n        return None\n    return sorted(\n        item.decode("utf-8") for item in listed.stdout.split(b"\\0") if item\n    )\n\n\ndef snapshot(root: Path = ROOT) -> dict[str, str]:\n    root = root.resolve()\n    tracked = _git_tracked_protected_paths(root)\n    if tracked is not None:\n        return {\n            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()\n            for relative in tracked\n            if (root / relative).is_file()\n        }\n\n    # Packaged/offline workspaces may intentionally omit .git. Keep the\n    # compatibility verifier usable there, while excluding machine-local\n    # runtime state that the repository itself declares non-source.\n    rows: dict[str, str] = {}\n    for name in PROTECTED_NAMES:\n        protected_root = root / name\n        if not protected_root.exists():\n            continue\n        for path in sorted(item for item in protected_root.rglob("*") if item.is_file()):\n            relative = path.relative_to(root)\n            if any(part in IGNORED_PARTS for part in relative.parts):\n                continue\n            if any(part in MACHINE_LOCAL_PARTS for part in relative.parts) and path.name != ".gitkeep":\n                continue\n            rows[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()\n    return rows\n''',
)

report = {
    "status": "WP08_ATTEMPT6_GIT_TRACKED_BASELINE_GOVERNANCE_GENERATED",
    "candidate_head": os.environ["CANDIDATE_HEAD"],
    "authority": "git-tracked-protected-source",
    "old_file_count": len(old),
    "new_file_count": len(current),
    "approved_product_paths": sorted(EXPECTED_PRODUCT),
    "governance_paths": [
        "skill-system/controller/project_compatibility.py",
        "skill-system/registry/product-source-baseline.json",
        "skill-system/tests/test_product_source_baseline_binding.py",
    ],
    "runtime_workspace_artifacts_excluded": True,
    "source_release_sha256_preserved": prior_release,
    "baseline_sha256": hashlib.sha256(BASELINE_PATH.read_bytes()).hexdigest(),
}
(ROOT / "wp08-attempt6-baseline-refresh-report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False, indent=2))
