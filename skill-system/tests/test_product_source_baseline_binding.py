from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from typing import Any


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


def _current_protected_snapshot(protected_roots: tuple[str, ...]) -> dict[str, str]:
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--", *protected_roots], cwd=ROOT
    )
    tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
    return {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in tracked
    }


def _recorded_files(baseline: dict[str, Any]) -> dict[str, str]:
    files = baseline.get("files")
    if not isinstance(files, dict):
        return {}
    return {str(key): str(value) for key, value in files.items()}


def _path_is_under_protected_root(relative: str, protected_roots: tuple[str, ...]) -> bool:
    return any(
        relative == root.rstrip("/") or relative.startswith(root.rstrip("/") + "/")
        for root in protected_roots
    )


def _baseline_binding_errors(
    baseline: dict[str, Any],
    current: dict[str, str],
    *,
    require_workspace_equality: bool,
) -> list[str]:
    errors: list[str] = []
    generated_from = str(baseline.get("generated_from") or "")
    if re.fullmatch(r"git:[0-9a-f]{40}", generated_from) is None:
        errors.append("baseline_generated_from_invalid")

    roots_value = baseline.get("protected_roots")
    if not isinstance(roots_value, list) or not roots_value:
        errors.append("baseline_protected_roots_missing")
        protected_roots: tuple[str, ...] = ()
    else:
        protected_roots = tuple(str(value) for value in roots_value)
        if any(
            not root
            or root.startswith("/")
            or root.startswith("../")
            or "/../" in root
            for root in protected_roots
        ):
            errors.append("baseline_protected_root_invalid")

    files_value = baseline.get("files")
    if not isinstance(files_value, dict):
        errors.append("baseline_files_not_object")
    recorded = _recorded_files(baseline)

    try:
        file_count = int(baseline.get("file_count"))
    except (TypeError, ValueError):
        file_count = -1
        errors.append("baseline_file_count_invalid")
    if file_count != len(recorded):
        errors.append("baseline_file_count_mismatch")

    for relative, recorded_sha in recorded.items():
        if not _path_is_under_protected_root(relative, protected_roots):
            errors.append(f"baseline_path_outside_protected_roots:{relative}")
        if re.fullmatch(r"[0-9a-f]{64}", recorded_sha) is None:
            errors.append(f"baseline_hash_invalid:{relative}")

    if not require_workspace_equality:
        return errors

    if set(recorded) != set(current):
        errors.append("accepted_snapshot_path_set_mismatch")
    for relative in sorted(set(recorded) & set(current)):
        if recorded[relative] != current[relative]:
            errors.append(f"accepted_snapshot_hash_mismatch:{relative}")
    return errors


def _require_workspace_equality() -> bool:
    # A pull-request checkout is an unaccepted candidate snapshot. Protected-source
    # drift is expected there and is governed later by G0-G5 plus baseline acceptance.
    # Push/manual control-plane runs still bind the accepted baseline to the checked-out
    # repository snapshot, so an accepted ref cannot silently drift.
    return os.environ.get("GITHUB_EVENT_NAME", "").strip().casefold() != "pull_request"


class ProductSourceBaselineBindingTests(unittest.TestCase):
    def test_baseline_contract_and_accepted_snapshot_binding(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        protected_roots = tuple(
            str(value) for value in baseline.get("protected_roots") or ()
        )
        current = _current_protected_snapshot(protected_roots)
        errors = _baseline_binding_errors(
            baseline,
            current,
            require_workspace_equality=_require_workspace_equality(),
        )
        self.assertEqual(errors, [])

    def test_pr_candidate_protected_delta_does_not_promote_the_accepted_baseline(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        recorded = _recorded_files(baseline)
        candidate = dict(recorded)
        candidate["contracts/generated/unaccepted-candidate-proof.json"] = "a" * 64

        candidate_errors = _baseline_binding_errors(
            baseline,
            candidate,
            require_workspace_equality=False,
        )
        self.assertEqual(candidate_errors, [])

        accepted_ref_errors = _baseline_binding_errors(
            baseline,
            candidate,
            require_workspace_equality=True,
        )
        self.assertIn("accepted_snapshot_path_set_mismatch", accepted_ref_errors)

    def test_pr_candidate_mode_still_fails_closed_on_baseline_document_tampering(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        recorded = _recorded_files(baseline)
        tampered = dict(baseline)
        tampered["file_count"] = len(recorded) + 1

        errors = _baseline_binding_errors(
            tampered,
            recorded,
            require_workspace_equality=False,
        )
        self.assertIn("baseline_file_count_mismatch", errors)

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

    def test_offline_workspace_fallback_excludes_runtime_state_but_keeps_source(self) -> None:
        compatibility = _load_project_compatibility()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "services/agent-service/src/example.py"
            runtime_db = root / "services/agent-service/runtime/sqlite/local.db"
            source.parent.mkdir(parents=True, exist_ok=True)
            runtime_db.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            runtime_db.write_bytes(b"machine-local")

            snapshot = compatibility.snapshot(root)

        self.assertIn("services/agent-service/src/example.py", snapshot)
        self.assertNotIn("services/agent-service/runtime/sqlite/local.db", snapshot)

    def test_baseline_does_not_claim_production_closure(self) -> None:
        task_ledger = json.loads(
            (ROOT / "governance/task-ledger.json").read_text(encoding="utf-8")
        )
        serialized = json.dumps(task_ledger, ensure_ascii=False)
        self.assertNotIn('"production_closed": true', serialized)


if __name__ == "__main__":
    unittest.main()
