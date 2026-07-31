"""Read-only projection of installed ecommerce capability definitions."""
from __future__ import annotations

from agent_modules.ecommerce.capabilities import CAPABILITIES, definition_for_tool

TOOL_CAPABILITY_CONTRACTS = tuple(row.contract for row in CAPABILITIES)


def contract_for_tool(tool_name: str):
    row = definition_for_tool(tool_name)
    return row.contract if row is not None else None


def public_label_for_contract(contract):
    row = definition_for_tool(getattr(contract, "tool_name", ""))
    return row.public_label if row is not None else None


def public_capability_labels():
    return list(dict.fromkeys(row.public_label for row in CAPABILITIES if row.public_label))
