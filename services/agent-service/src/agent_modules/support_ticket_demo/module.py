from __future__ import annotations

from agent_core.kernel.capability_registry import CapabilityBinding
from agent_core.modules.contracts import ModuleContribution
from agent_modules.support_ticket_demo.business_port import get_support_ticket_demo_business_port
from agent_modules.support_ticket_demo.capabilities import CONTRACT, PRESENTATION_CONTRACT, PUBLIC_LABEL, SCHEMA, execute
from agent_modules.support_ticket_demo.resource_plugin import SupportTicketResourcePlugin


class SupportTicketDemoModule:
    """A complete independent module used to prove multi-module composition."""

    module_id = "support_ticket_demo"
    version = "1.0.0"

    def contribution(self) -> ModuleContribution:
        def dispatch(state, tool_name, args, *, execution_permit, effect_id, transactions=None):
            from agent_core.runtime.capability_gate import permit_allows_dispatch
            if str(tool_name) != CONTRACT.tool_name:
                return {"ok": False, "code": "MODULE_TOOL_MISMATCH", "message": "模块工具绑定不一致，未执行。"}
            if not permit_allows_dispatch(
                state=state, permit=execution_permit, tool_name=CONTRACT.tool_name, effect_id=str(effect_id or ""), args=dict(args or {})
            ):
                return {"ok": False, "code": "EXECUTION_PERMIT_INVALID", "message": "执行许可无效，未执行模块能力。"}
            return execute(state, dict(args or {}), execution_permit=execution_permit, effect_id=effect_id, transactions=transactions)

        from agent_modules.support_ticket_demo.presentation import SupportTicketObservationAdapter
        return ModuleContribution(
            module_id=self.module_id,
            version=self.version,
            capabilities=(CapabilityBinding(domain_id=self.module_id, contract=CONTRACT, schema=SCHEMA, dispatcher=dispatch, public_label=PUBLIC_LABEL),),
            resources=(SupportTicketResourcePlugin(),),
            presentation_adapters=(SupportTicketObservationAdapter(),),
            resource_types=frozenset({"support_ticket"}),
            business_port_factory=get_support_ticket_demo_business_port,
        )
