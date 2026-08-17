from __future__ import annotations

from pathlib import Path

helper = Path(__file__).with_name("stage8_round2_policy_patch.py")
namespace = {"__name__": "__main__", "__file__": str(helper)}
exec(compile(helper.read_text(encoding="utf-8"), str(helper), "exec"), namespace, namespace)

root = Path(__file__).resolve().parents[1]
test_path = root / "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py"
text = test_path.read_text(encoding="utf-8")
old = '    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])\n'
new = (
    '    store.advance_draft(offer["draft_id"], draft_state="COMMITTING", draft_revision=offer["draft_revision"])\n'
    '    store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])\n'
)
count = text.count(old)
if count != 4:
    raise SystemExit(f"expected four terminal-fixture transitions, found {count}")
test_path.write_text(text.replace(old, new), encoding="utf-8")
