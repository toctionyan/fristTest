from __future__ import annotations

from pathlib import Path

helper = Path(__file__).with_name("stage8_round3_integrity_patch.py")
namespace = {"__name__": "__main__", "__file__": str(helper)}
exec(compile(helper.read_text(encoding="utf-8"), str(helper), "exec"), namespace, namespace)

root = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    target = root / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} patch anchor mismatch: {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


# The Stage 8 crash-window assertion uses the exact persisted transaction scope.
replace_once(
    "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py",
    "from agent_core.runtime.outcomes import outcome\n",
    "from agent_core.runtime.outcomes import outcome\nfrom agent_core.storage.repositories.base import TransactionScope\n",
    label="Stage 8 TransactionScope import",
)

# The generic provider contract previously jumped STARTED -> ACKED without a
# Business Receipt.  Strengthen the fixture to exercise the real authority
# chain rather than weakening the new Receipt requirement.
replace_once(
    "services/agent-service/tests/transactions/test_transaction_storage.py",
    '''        assert started["reserved"] is True\n        provider.transactions.transition_attempt(attempt_id, state="ACKED", business_result={"success": True}, receipt_handle="h_receipt:1")\n        assert provider.transactions.get_attempt(attempt_id)["state"] == "ACKED"\n''',
    '''        assert started["reserved"] is True\n        business_result = {"success": True, "data": {"resource_id": f"resource-{suffix}"}}\n        receipt = provider.transactions.record_receipt(\n            receipt_id=f"receipt-{suffix}",\n            tenant_id="tenant-a",\n            user_id="u001",\n            thread_id=thread_id,\n            draft_id=draft_id,\n            attempt_id=attempt_id,\n            receipt_handle=f"h_receipt:{suffix}",\n            receipt_state="SUCCESS",\n            business_result=business_result,\n            business_resource_id=f"resource-{suffix}",\n        )\n        assert receipt["attempt_id"] == attempt_id\n        provider.transactions.transition_attempt(\n            attempt_id,\n            state="ACKED",\n            business_result=business_result,\n            receipt_handle=f"h_receipt:{suffix}",\n        )\n        assert provider.transactions.get_attempt(attempt_id)["state"] == "ACKED"\n        provider.transactions.consume_grant(\n            grant_id,\n            attempt_id=attempt_id,\n            receipt_handle=f"h_receipt:{suffix}",\n        )\n        assert provider.transactions.get_grant(grant_id)["state"] == "CONSUMED"\n''',
    label="provider contract receipt-first chain",
)
