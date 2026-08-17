from __future__ import annotations

from pathlib import Path

helper = Path(__file__).with_name("stage8_terminal_monotonicity_patch_v2.py")
source = helper.read_text(encoding="utf-8")
old = '''    "from agent_core.operations.draft import canonical_command_payload, command_digest_for_offer\\n",\n    "from agent_core.operations.draft import canonical_command_payload, command_digest_for_offer\\nfrom agent_core.storage.repositories.base import TransactionScope\\n",\n'''
new = '''    "from agent_core.transaction.model import canonical_command_payload, command_digest_for_offer\\n",\n    "from agent_core.transaction.model import canonical_command_payload, command_digest_for_offer\\nfrom agent_core.storage.repositories.base import TransactionScope\\n",\n'''
if source.count(old) != 1:
    raise SystemExit(f"v2 coordinator import specification mismatch: {source.count(old)}")
source = source.replace(old, new)
namespace = {"__name__": "__main__", "__file__": str(helper)}
exec(compile(source, str(helper), "exec"), namespace, namespace)
