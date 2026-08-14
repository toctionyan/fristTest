from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "services/agent-service/src/agent_core/goal_graph/handoff_simulation.py"
TEST_PATH = ROOT / "services/agent-service/tests/runtime/test_typed_goal_dependency_handoff_simulation.py"
BASELINE_PATH = ROOT / "skill-system/registry/product-source-baseline.json"

OLD_BLOCK = '''    timeline = row.get("timeline") if isinstance(row.get("timeline"), list) else []
    if not timeline:
        errors.append("HANDOFF_TIMELINE_REQUIRED")
    typed_steps = 0
    for index, step in enumerate(timeline):
        if not isinstance(step, dict):
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_INVALID")
            continue
        selected = _text(step.get("selected_dependency_authority"), limit=200)
        active = list(step.get("active_authorities") or [])
        if selected not in _ALLOWED_AUTHORITIES:
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_AUTHORITY_INVALID")
        if active != [selected]:
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_NOT_SINGULAR")
        if bool(step.get("legacy_active")) == bool(step.get("typed_active")):
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_ACTIVE_FLAGS_INVALID")
        if selected == LEGACY_DEPENDENCY_AUTHORITY:
            if step.get("legacy_active") is not True or bool(step.get("typed_active")):
                errors.append(f"HANDOFF_TIMELINE_STEP_{index}_LEGACY_FLAGS_INVALID")
        elif selected == TYPED_DEPENDENCY_AUTHORITY:
            typed_steps += 1
            if step.get("typed_active") is not True or bool(step.get("legacy_active")):
                errors.append(f"HANDOFF_TIMELINE_STEP_{index}_TYPED_FLAGS_INVALID")

    status = _text(row.get("status"), limit=100)
    final = _text(row.get("simulated_final_dependency_authority"), limit=200)
    timeline_final = (
        _text(timeline[-1].get("selected_dependency_authority"), limit=200)
        if timeline and isinstance(timeline[-1], dict)
        else ""
    )
    if final != timeline_final:
        errors.append("HANDOFF_FINAL_AUTHORITY_TIMELINE_MISMATCH")

    typed_entered = row.get("typed_candidate_entered_in_simulation") is True
    if typed_entered != bool(typed_steps):
        errors.append("HANDOFF_TYPED_ENTRY_FLAG_MISMATCH")

    rollback_exercised = row.get("rollback_exercised_in_simulation") is True
    if status == "BLOCKED":
        if typed_steps or final != LEGACY_DEPENDENCY_AUTHORITY:
            errors.append("HANDOFF_BLOCKED_STATE_MUST_REMAIN_LEGACY")
    elif status == "LEGACY_ONLY_SIMULATED":
        if typed_steps or final != LEGACY_DEPENDENCY_AUTHORITY:
            errors.append("HANDOFF_LEGACY_ONLY_STATE_INVALID")
    elif status == "TYPED_HANDOFF_SIMULATED":
        if not typed_steps or final != TYPED_DEPENDENCY_AUTHORITY or rollback_exercised:
            errors.append("HANDOFF_TYPED_SIMULATION_STATE_INVALID")
    elif status == "ROLLBACK_DRILL_COMPLETE":
        if not typed_steps or final != LEGACY_DEPENDENCY_AUTHORITY or not rollback_exercised:
            errors.append("HANDOFF_ROLLBACK_DRILL_STATE_INVALID")
    else:
        errors.append("HANDOFF_SIMULATION_STATUS_INVALID")
'''

NEW_BLOCK = '''    status = _text(row.get("status"), limit=100)
    expected_timeline_by_status = {
        "BLOCKED": [
            ("observed_runtime_baseline", LEGACY_DEPENDENCY_AUTHORITY),
        ],
        "LEGACY_ONLY_SIMULATED": [
            ("observed_runtime_baseline", LEGACY_DEPENDENCY_AUTHORITY),
        ],
        "TYPED_HANDOFF_SIMULATED": [
            ("observed_runtime_baseline", LEGACY_DEPENDENCY_AUTHORITY),
            ("simulated_typed_handoff", TYPED_DEPENDENCY_AUTHORITY),
        ],
        "ROLLBACK_DRILL_COMPLETE": [
            ("observed_runtime_baseline", LEGACY_DEPENDENCY_AUTHORITY),
            ("simulated_typed_handoff", TYPED_DEPENDENCY_AUTHORITY),
            ("simulated_rollback_to_legacy", LEGACY_DEPENDENCY_AUTHORITY),
        ],
    }
    expected_timeline = expected_timeline_by_status.get(status)
    if expected_timeline is None:
        errors.append("HANDOFF_SIMULATION_STATUS_INVALID")

    timeline = row.get("timeline") if isinstance(row.get("timeline"), list) else []
    if not timeline:
        errors.append("HANDOFF_TIMELINE_REQUIRED")
    typed_steps = 0
    actual_timeline: list[tuple[str, str]] = []
    for index, step in enumerate(timeline):
        if not isinstance(step, dict):
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_INVALID")
            actual_timeline.append(("", ""))
            continue
        if step.get("index") != index:
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_INDEX_INVALID")
        phase = _text(step.get("phase"), limit=200)
        selected = _text(step.get("selected_dependency_authority"), limit=200)
        actual_timeline.append((phase, selected))
        active = list(step.get("active_authorities") or [])
        if selected not in _ALLOWED_AUTHORITIES:
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_AUTHORITY_INVALID")
        if active != [selected]:
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_NOT_SINGULAR")
        if bool(step.get("legacy_active")) == bool(step.get("typed_active")):
            errors.append(f"HANDOFF_TIMELINE_STEP_{index}_ACTIVE_FLAGS_INVALID")
        if selected == LEGACY_DEPENDENCY_AUTHORITY:
            if step.get("legacy_active") is not True or bool(step.get("typed_active")):
                errors.append(f"HANDOFF_TIMELINE_STEP_{index}_LEGACY_FLAGS_INVALID")
        elif selected == TYPED_DEPENDENCY_AUTHORITY:
            typed_steps += 1
            if step.get("typed_active") is not True or bool(step.get("legacy_active")):
                errors.append(f"HANDOFF_TIMELINE_STEP_{index}_TYPED_FLAGS_INVALID")

    if expected_timeline is not None and actual_timeline != expected_timeline:
        errors.append("HANDOFF_TIMELINE_SHAPE_INVALID")

    final = _text(row.get("simulated_final_dependency_authority"), limit=200)
    timeline_final = (
        _text(timeline[-1].get("selected_dependency_authority"), limit=200)
        if timeline and isinstance(timeline[-1], dict)
        else ""
    )
    if final != timeline_final:
        errors.append("HANDOFF_FINAL_AUTHORITY_TIMELINE_MISMATCH")
    if expected_timeline is not None and final != expected_timeline[-1][1]:
        errors.append("HANDOFF_FINAL_AUTHORITY_STATUS_MISMATCH")

    expected_typed_steps = 1 if status in {
        "TYPED_HANDOFF_SIMULATED",
        "ROLLBACK_DRILL_COMPLETE",
    } else 0
    if typed_steps != expected_typed_steps:
        errors.append("HANDOFF_TYPED_STEP_COUNT_INVALID")

    typed_entered = row.get("typed_candidate_entered_in_simulation") is True
    expected_typed_entered = status in {
        "TYPED_HANDOFF_SIMULATED",
        "ROLLBACK_DRILL_COMPLETE",
    }
    if typed_entered != expected_typed_entered:
        errors.append("HANDOFF_TYPED_ENTRY_FLAG_MISMATCH")

    rollback_exercised = row.get("rollback_exercised_in_simulation") is True
    expected_rollback_exercised = status == "ROLLBACK_DRILL_COMPLETE"
    if rollback_exercised != expected_rollback_exercised:
        errors.append("HANDOFF_ROLLBACK_FLAG_MISMATCH")

    requested = _text(row.get("requested_simulated_authority"), limit=200)
    if status == "LEGACY_ONLY_SIMULATED" and requested != LEGACY_DEPENDENCY_AUTHORITY:
        errors.append("HANDOFF_REQUEST_STATUS_MISMATCH")
    if status in {"TYPED_HANDOFF_SIMULATED", "ROLLBACK_DRILL_COMPLETE"} and requested != TYPED_DEPENDENCY_AUTHORITY:
        errors.append("HANDOFF_REQUEST_STATUS_MISMATCH")

    evidence_errors = row.get("errors") if isinstance(row.get("errors"), list) else []
    if status == "BLOCKED" and not evidence_errors:
        errors.append("HANDOFF_BLOCKED_ERRORS_REQUIRED")
    if status != "BLOCKED" and evidence_errors:
        errors.append("HANDOFF_NONBLOCKED_ERRORS_MUST_BE_EMPTY")

    if status != "BLOCKED" and not _text(row.get("source_gate_digest"), limit=128):
        errors.append("HANDOFF_SOURCE_GATE_DIGEST_REQUIRED")
    if status == "ROLLBACK_DRILL_COMPLETE" and not _text(
        row.get("source_rollback_digest"), limit=128
    ):
        errors.append("HANDOFF_SOURCE_ROLLBACK_DIGEST_REQUIRED")
'''

TEST_MARKER = '''\ndef test_stage4c_simulation_module_has_no_runtime_or_domain_import_authority() -> None:\n'''

NEW_TESTS = '''\ndef test_integrity_rejects_recomputed_repeated_typed_handoff_sequence() -> None:\n    gate = _ready_gate()\n    simulation = build_dependency_authority_handoff_simulation(\n        gate=gate,\n        rollback=_rollback(gate),\n        exercise_rollback=True,\n    )\n    tampered = deepcopy(simulation)\n    tampered["timeline"].extend(\n        [\n            {\n                "index": 3,\n                "phase": "simulated_typed_handoff",\n                "selected_dependency_authority": TYPED_DEPENDENCY_AUTHORITY,\n                "active_authorities": [TYPED_DEPENDENCY_AUTHORITY],\n                "legacy_active": False,\n                "typed_active": True,\n            },\n            {\n                "index": 4,\n                "phase": "simulated_rollback_to_legacy",\n                "selected_dependency_authority": LEGACY_DEPENDENCY_AUTHORITY,\n                "active_authorities": [LEGACY_DEPENDENCY_AUTHORITY],\n                "legacy_active": True,\n                "typed_active": False,\n            },\n        ]\n    )\n    tampered["simulation_digest"] = _digest(\n        {k: v for k, v in tampered.items() if k != "simulation_digest"}\n    )\n\n    integrity = dependency_authority_handoff_simulation_integrity(tampered)\n\n    assert integrity["ok"] is False\n    assert "HANDOFF_TIMELINE_SHAPE_INVALID" in integrity["errors"]\n    assert "HANDOFF_TYPED_STEP_COUNT_INVALID" in integrity["errors"]\n\n\ndef test_integrity_rejects_recomputed_phase_and_index_drift() -> None:\n    simulation = build_dependency_authority_handoff_simulation(gate=_ready_gate())\n    tampered = deepcopy(simulation)\n    tampered["timeline"][1]["index"] = 7\n    tampered["timeline"][1]["phase"] = "unexpected_phase"\n    tampered["simulation_digest"] = _digest(\n        {k: v for k, v in tampered.items() if k != "simulation_digest"}\n    )\n\n    integrity = dependency_authority_handoff_simulation_integrity(tampered)\n\n    assert integrity["ok"] is False\n    assert "HANDOFF_TIMELINE_STEP_1_INDEX_INVALID" in integrity["errors"]\n    assert "HANDOFF_TIMELINE_SHAPE_INVALID" in integrity["errors"]\n\n\ndef test_integrity_rejects_recomputed_request_status_mismatch() -> None:\n    simulation = build_dependency_authority_handoff_simulation(gate=_ready_gate())\n    tampered = deepcopy(simulation)\n    tampered["requested_simulated_authority"] = LEGACY_DEPENDENCY_AUTHORITY\n    tampered["simulation_digest"] = _digest(\n        {k: v for k, v in tampered.items() if k != "simulation_digest"}\n    )\n\n    integrity = dependency_authority_handoff_simulation_integrity(tampered)\n\n    assert integrity["ok"] is False\n    assert "HANDOFF_REQUEST_STATUS_MISMATCH" in integrity["errors"]\n\n'''


def harden() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    if source.count(OLD_BLOCK) != 1:
        raise SystemExit(f"source_exact_block_match_count:{source.count(OLD_BLOCK)}")
    SOURCE_PATH.write_text(source.replace(OLD_BLOCK, NEW_BLOCK, 1), encoding="utf-8")

    tests = TEST_PATH.read_text(encoding="utf-8")
    if tests.count(TEST_MARKER) != 1:
        raise SystemExit(f"test_marker_match_count:{tests.count(TEST_MARKER)}")
    if "test_integrity_rejects_recomputed_repeated_typed_handoff_sequence" in tests:
        raise SystemExit("hardening_tests_already_present")
    TEST_PATH.write_text(tests.replace(TEST_MARKER, NEW_TESTS + TEST_MARKER, 1), encoding="utf-8")

    subprocess.run(
        [sys.executable, "-m", "py_compile", str(SOURCE_PATH), str(TEST_PATH)],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(json.dumps({"status": "PASS", "mode": "harden"}, indent=2))


def refresh() -> None:
    source_sha = os.environ.get("SOURCE_SHA", "").strip()
    if len(source_sha) != 40:
        raise SystemExit("source_sha_identity_missing")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != source_sha:
        raise SystemExit(f"source_sha_not_current_hardened_head:{source_sha}:{head}")

    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    previous = {str(k): str(v) for k, v in (payload.get("files") or {}).items()}
    if int(payload.get("file_count") or 0) != 589 or len(previous) != 589:
        raise SystemExit(f"unexpected_previous_baseline:{payload.get('file_count')}/{len(previous)}")
    protected_roots = tuple(str(v) for v in payload.get("protected_roots") or ())
    if protected_roots != ("services", "web", "contracts"):
        raise SystemExit(f"unexpected_protected_roots:{protected_roots!r}")

    raw = subprocess.check_output(["git", "ls-files", "-z", "--", *protected_roots], cwd=ROOT)
    tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
    current = {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in tracked
    }
    if len(current) != 589:
        raise SystemExit(f"unexpected_protected_file_count:{len(current)}")

    changed = {
        path
        for path in set(current) & set(previous)
        if current[path] != previous[path]
    }
    added = set(current) - set(previous)
    removed = set(previous) - set(current)
    expected_changed = {
        "services/agent-service/src/agent_core/goal_graph/handoff_simulation.py",
        "services/agent-service/tests/runtime/test_typed_goal_dependency_handoff_simulation.py",
    }
    if changed != expected_changed:
        raise SystemExit("unexpected_changed_existing:" + ",".join(sorted(changed)))
    if added:
        raise SystemExit("unexpected_added:" + ",".join(sorted(added)))
    if removed:
        raise SystemExit("unexpected_removed:" + ",".join(sorted(removed)))

    payload["file_count"] = len(current)
    payload["files"] = dict(sorted(current.items()))
    payload["generated_at"] = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    payload["generated_from"] = "git:" + source_sha
    BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "PASS",
        "mode": "refresh",
        "file_count": len(current),
        "generated_from": payload["generated_from"],
        "changed": sorted(changed),
        "added": sorted(added),
        "removed": sorted(removed),
    }, ensure_ascii=False, indent=2))


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"harden", "refresh"}:
        raise SystemExit("usage: typed_goal_stage4c_harden_helper.py harden|refresh")
    if sys.argv[1] == "harden":
        harden()
    else:
        refresh()


if __name__ == "__main__":
    main()
