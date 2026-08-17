from __future__ import annotations

from pathlib import Path

helper = Path(__file__).with_name("stage8_terminal_monotonicity_patch_v3.py")
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


# append_entries has its own merge path before the final normalize_ledger pass.
# Guard the immutable terminal Draft there too, otherwise the stale candidate
# has already replaced the terminal row before normalize_ledger can see both.
replace_once(
    "services/agent-service/src/agent_core/ledger/ledger.py",
    '''        current = merged.get(str(raw["handle"]))\n        candidate = deepcopy(raw)\n        if str(candidate.get("kind") or "") == "offer":\n            candidate = ensure_transaction_draft(candidate, previous=current if isinstance(current, dict) else None)\n        if current is not None:\n''',
    '''        current = merged.get(str(raw["handle"]))\n        candidate = deepcopy(raw)\n        if str(candidate.get("kind") or "") == "offer":\n            if (\n                isinstance(current, dict)\n                and str(current.get("kind") or "") == "offer"\n                and str(current.get("draft_state") or "").upper() in TERMINAL_DRAFT_STATES\n            ):\n                continue\n            candidate = ensure_transaction_draft(candidate, previous=current if isinstance(current, dict) else None)\n        if current is not None:\n''',
    label="append_entries terminal guard",
)

# Compatibility callers may issue the first Grant directly from an unpersisted
# offer. When there is no canonical Draft yet, establish the canonical
# AWAITING_AUTHORIZATION state before minting the Grant. If a canonical Draft
# already exists, the earlier durable-state guard still requires it to be
# exactly AWAITING_AUTHORIZATION and matching revision.
replace_once(
    "services/agent-service/src/agent_core/transaction/coordinator.py",
    '''    persisted = persist_draft_from_offer(state=state, offer=offer, draft_state=str(offer.get("draft_state") or "AWAITING_AUTHORIZATION"))\n    if str(persisted.get("draft_state") or "").upper() != "AWAITING_AUTHORIZATION":\n''',
    '''    persisted = persist_draft_from_offer(state=state, offer=offer, draft_state="AWAITING_AUTHORIZATION")\n    if str(persisted.get("draft_state") or "").upper() != "AWAITING_AUTHORIZATION":\n''',
    label="initial grant canonical awaiting state",
)
