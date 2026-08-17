from __future__ import annotations

from pathlib import Path

helper = Path(__file__).with_name("stage8_round2_policy_patch.py")
namespace = {"__name__": "__main__", "__file__": str(helper)}
exec(compile(helper.read_text(encoding="utf-8"), str(helper), "exec"), namespace, namespace)

root = Path(__file__).resolve().parents[1]
test_path = root / "services/agent-service/tests/transactions/test_stage8_transaction_authority_adversarial.py"
text = test_path.read_text(encoding="utf-8")
target = 'store.advance_draft(offer["draft_id"], draft_state="COMMITTED", draft_revision=offer["draft_revision"])'
lines = text.splitlines()
out: list[str] = []
count = 0
for line in lines:
    stripped = line.lstrip(" ")
    if stripped == target:
        indent = line[: len(line) - len(stripped)]
        out.append(indent + 'store.advance_draft(offer["draft_id"], draft_state="COMMITTING", draft_revision=offer["draft_revision"])')
        out.append(line)
        count += 1
    else:
        out.append(line)
if count != 4:
    raise SystemExit(f"expected four terminal-fixture transitions, found {count}")
test_path.write_text("\n".join(out) + "\n", encoding="utf-8")
