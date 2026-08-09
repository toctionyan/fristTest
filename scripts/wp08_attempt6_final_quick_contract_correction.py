#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, got {text.count(old)}")
    return text.replace(old, new)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: wp08_attempt6_final_quick_contract_correction.py <candidate-root>")
    root = Path(sys.argv[1]).resolve()

    runner = root / "services/agent-service/app/services/lifecycle_command_runner.py"
    text = runner.read_text(encoding="utf-8")
    old = '''        merged = {**base_state, **dict(update or {})}\n        # Structured API commands enter between normal graph nodes. Persist the\n        # verified ingress state together with the formal node update so scope\n        # identity, target artifacts and transaction controls survive the\n        # checkpoint before outgoing edges are scheduled. The node update wins\n        # on overlap and remains the transition authority.\n        graph.update_state(config, merged, as_node=node_name)'''
    new = '''        ingress_keys = ("current_thread_id", "current_user_id", "current_role", "current_tenant_id", "current_subject", "turn_index", "ledger_schema_version")\n        verified_ingress = {key: base_state[key] for key in ingress_keys if key in base_state}\n        merged = {**verified_ingress, **dict(update or {})}\n        graph.update_state(config, merged, as_node=node_name)'''
    runner.write_text(replace_once(text, old, new, label="lifecycle runner ingress boundary"), encoding="utf-8")

    lock_test = root / "services/agent-service/tests/architecture/test_dependency_lock_contract.py"
    text = lock_test.read_text(encoding="utf-8")
    old = '''def _canonical_requirement(raw: str) -> tuple[str, tuple[str, ...], str]:\n    from packaging.requirements import Requirement\n\n    requirement = Requirement(raw)\n    return (\n        requirement.name.lower().replace("_", "-"),\n        tuple(sorted(requirement.extras)),\n        str(requirement.specifier),\n    )\n'''
    new = '''def _canonical_specifier(raw: str) -> str:\n    from packaging.specifiers import SpecifierSet\n\n    return str(SpecifierSet(raw))\n\n\ndef _canonical_requirement(raw: str) -> tuple[str, tuple[str, ...], str]:\n    from packaging.requirements import Requirement\n\n    requirement = Requirement(raw)\n    return (\n        requirement.name.lower().replace("_", "-"),\n        tuple(sorted(requirement.extras)),\n        _canonical_specifier(str(requirement.specifier)),\n    )\n'''
    text = replace_once(text, old, new, label="dependency requirement canonicalizer")
    text = replace_once(
        text,
        '                str(entry.get("specifier", "")),\n',
        '                _canonical_specifier(str(entry.get("specifier", ""))),\n',
        label="locked dependency specifier canonicalizer",
    )
    lock_test.write_text(text, encoding="utf-8")

    contract_test = root / "services/agent-service/tests/runtime/test_wp08_attempt6_release_repairs.py"
    text = contract_test.read_text(encoding="utf-8")
    old = '''    base = {\n        "current_thread_id": "thread-1",\n        "current_user_id": "u001",\n        "current_tenant_id": "default",\n        "artifact_ledger": [{"handle": "artifact:1", "kind": "artifact"}],\n        "status": "ActionProposalReady",\n    }'''
    new = '''    base = {\n        "current_thread_id": "thread-1",\n        "current_user_id": "u001",\n        "current_role": "customer",\n        "current_tenant_id": "default",\n        "current_subject": "u001",\n        "turn_index": 1,\n        "ledger_schema_version": 2,\n        "artifact_ledger": [{"handle": "artifact:1", "kind": "artifact"}],\n        "phase": "action_gateway",\n        "status": "ActionProposalReady",\n    }'''
    text = replace_once(text, old, new, label="structured ingress test base")
    old = '''    assert persisted["current_thread_id"] == "thread-1"\n    assert persisted["current_user_id"] == "u001"\n    assert persisted["current_tenant_id"] == "default"\n    assert [row["handle"] for row in persisted["artifact_ledger"]] == ["artifact:1", "offer:1"]\n    assert persisted["status"] == "ActionInputRequired"\n    assert result["focused_draft_id"] == "offer:1"'''
    new = '''    assert persisted["current_thread_id"] == "thread-1"\n    assert persisted["current_user_id"] == "u001"\n    assert persisted["current_role"] == "customer"\n    assert persisted["current_tenant_id"] == "default"\n    assert persisted["current_subject"] == "u001"\n    assert persisted["turn_index"] == 1\n    assert persisted["ledger_schema_version"] == 2\n    assert "phase" not in persisted\n    assert [row["handle"] for row in persisted["artifact_ledger"]] == ["artifact:1", "offer:1"]\n    assert persisted["status"] == "ActionInputRequired"\n    assert result["focused_draft_id"] == "offer:1"'''
    contract_test.write_text(replace_once(text, old, new, label="structured ingress assertions"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
