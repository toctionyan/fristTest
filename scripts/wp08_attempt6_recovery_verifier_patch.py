#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"replacement_anchor_count:{path}:{count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "scripts/verify_managed_postgres_recovery.py"
replace_once(
    path,
    '''def _pending_interaction(\n    harness: ProductRuntimeHarness,\n    *,\n    token: str,\n    thread_id: str,\n    lifecycle: str,\n) -> tuple[dict, dict]:\n    payload = _call(\n        harness.agent_url,\n        f"/api/threads/{thread_id}/pending",\n        token=token,\n    )\n    return _interaction(payload, lifecycle)\n\n\ndef _input_values(interaction: dict[str, Any]) -> dict[str, str]:\n    values: dict[str, str] = {}\n    for field in list(interaction.get("fields") or []):\n        if not isinstance(field, dict) or not field.get("required", True):\n            continue\n''',
    '''def _pending_live_interaction(\n    harness: ProductRuntimeHarness,\n    *,\n    token: str,\n    thread_id: str,\n) -> tuple[dict, dict]:\n    payload = _call(\n        harness.agent_url,\n        f"/api/threads/{thread_id}/pending",\n        token=token,\n    )\n    if payload.get("type") != "interaction_required":\n        raise RuntimeError(f"expected live transaction interaction: {payload}")\n    interaction = payload.get("interaction")\n    if not isinstance(interaction, dict):\n        raise RuntimeError(f"pending response has no interaction: {payload}")\n    control = interaction.get("control")\n    if not isinstance(control, dict):\n        raise RuntimeError(f"interaction has no control: {payload}")\n    return interaction, control\n\n\ndef _pending_interaction(\n    harness: ProductRuntimeHarness,\n    *,\n    token: str,\n    thread_id: str,\n    lifecycle: str,\n) -> tuple[dict, dict]:\n    interaction, control = _pending_live_interaction(\n        harness, token=token, thread_id=thread_id\n    )\n    if interaction.get("lifecycle") != lifecycle:\n        raise RuntimeError(\n            f"unexpected interaction lifecycle, expected {lifecycle}: {interaction}"\n        )\n    return interaction, control\n\n\ndef _input_values(interaction: dict[str, Any]) -> dict[str, str]:\n    values: dict[str, str] = {}\n    current_step = max(1, int(interaction.get("current_step") or 1))\n    for field in list(interaction.get("fields") or []):\n        if not isinstance(field, dict) or not field.get("required", True):\n            continue\n        if max(1, int(field.get("step") or 1)) != current_step:\n            continue\n''',
)

replace_once(
    path,
    '''    input_result = _call(harness.agent_url, "/api/transactions/input", method="POST", token=token, body={\n        "thread_id": thread_id,\n        "interaction_mode": "submit_input",\n        "offer_handle": form_control["offer_handle"],\n        "action_id": form_control["action_id"],\n        "target_handle": form_control["target_handle"],\n        "form_id": form_control["form_id"],\n        "form_version": int(form_control["form_version"]),\n        "form_step": int(form_control["form_step"]),\n        "conversation_revision": int(form_control["conversation_revision"]),\n        "client_request_id": f"input-{uuid4().hex}",\n        "input_values": _input_values(form),\n    })\n    _authority, authority_control = _pending_interaction(\n        harness, token=token, thread_id=thread_id, lifecycle="awaiting_authority"\n    )\n''',
    '''    authority_control: dict[str, Any] | None = None\n    previous_step = 0\n    for _ in range(8):\n        current_step = int(form_control.get("form_step") or 0)\n        if current_step <= previous_step:\n            raise RuntimeError(\n                f"managed recovery form did not advance monotonically: {previous_step} -> {current_step}"\n            )\n        previous_step = current_step\n        _call(harness.agent_url, "/api/transactions/input", method="POST", token=token, body={\n            "thread_id": thread_id,\n            "interaction_mode": "submit_input",\n            "offer_handle": form_control["offer_handle"],\n            "action_id": form_control["action_id"],\n            "target_handle": form_control["target_handle"],\n            "form_id": form_control["form_id"],\n            "form_version": int(form_control["form_version"]),\n            "form_step": current_step,\n            "conversation_revision": int(form_control["conversation_revision"]),\n            "client_request_id": f"input-{uuid4().hex}",\n            "input_values": _input_values(form),\n        })\n        live, live_control = _pending_live_interaction(\n            harness, token=token, thread_id=thread_id\n        )\n        lifecycle = str(live.get("lifecycle") or "")\n        if lifecycle == "awaiting_authority":\n            authority_control = live_control\n            break\n        if lifecycle != "collecting_input":\n            raise RuntimeError(f"unexpected post-input lifecycle: {lifecycle}")\n        next_step = int(live_control.get("form_step") or 0)\n        if next_step <= current_step:\n            raise RuntimeError(\n                f"managed recovery form stalled at step {current_step}: next={next_step}"\n            )\n        form, form_control = live, live_control\n    if authority_control is None:\n        raise RuntimeError("managed recovery form exceeded bounded step budget")\n''',
)

# Extend the already-generated focused test file with a multi-step verifier counterexample.
test_path = ROOT / "services/agent-service/tests/runtime/test_wp08_attempt6_release_repairs.py"
if not test_path.is_file():
    raise SystemExit("attempt6_focused_test_missing")
text = test_path.read_text(encoding="utf-8")
append = '''\n\ndef test_recovery_input_values_are_scoped_to_current_form_step() -> None:\n    import verify_managed_postgres_recovery as recovery\n\n    interaction = {\n        "current_step": 2,\n        "fields": [\n            {"name": "reason_code", "required": True, "step": 1, "options": [{"value": "QUALITY_ISSUE"}]},\n            {"name": "reason", "required": True, "step": 2, "options": []},\n        ],\n    }\n    assert recovery._input_values(interaction) == {\n        "reason": "managed-postgres-restart-recovery"\n    }\n'''
if "test_recovery_input_values_are_scoped_to_current_form_step" in text:
    raise SystemExit("attempt6_multistep_test_already_present")
test_path.write_text(text + append, encoding="utf-8")

print("attempt6 multi-step recovery verifier patch applied")
