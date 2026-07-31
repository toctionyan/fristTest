from __future__ import annotations

"""Operation-declared transaction target validation.

The transaction kernel cannot assume that every future customer-service action
acts on an ecommerce order.  It asks the registered operation plugin for the
single resource type it accepts; domain plugins remain responsible for their
own resource declarations.
"""

from typing import Any

from agent_core.modules import current_runtime_registry


def allowed_target_resource_types(action_id: str) -> set[str]:
    plugin = current_runtime_registry().operations.get(str(action_id or ""))
    if plugin is None:
        return set()
    resource_type = str(getattr(plugin, "target_resource_type", "") or "").strip()
    return {resource_type} if resource_type else set()


def target_unavailable_message(action_id: str) -> str:
    allowed = allowed_target_resource_types(action_id)
    if not allowed:
        return "该办理动作的目标类型已不可验证，未执行任何业务写操作。"
    return "办理目标已失效或不符合当前动作能力，未执行任何业务写操作。"
