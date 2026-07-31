from __future__ import annotations

from agent_modules.ecommerce.assessments import ecommerce_operation_assessments
from agent_core.kernel.capability_registry import CapabilityBinding
from agent_core.modules.contracts import ModuleContribution
from agent_modules.ecommerce.business_port import get_ecommerce_business_port
from agent_modules.ecommerce.capabilities import CAPABILITIES
from agent_modules.ecommerce.operation_plugins import default_order_operations
from agent_modules.ecommerce.resource_plugins import OrderResourcePlugin
from agent_modules.ecommerce.rag_seed import ecommerce_builtin_knowledge_documents


class EcommerceModule:
    """Explicitly installed ecommerce module; all public capability facts originate in ``capabilities/*``."""

    module_id = "ecommerce"
    version = "20.6.1"

    @classmethod
    def _bindings(cls) -> tuple[CapabilityBinding, ...]:
        rows: list[CapabilityBinding] = []
        for definition in CAPABILITIES:
            contract = definition.contract

            def dispatcher(state, tool_name, args, *, execution_permit, effect_id, transactions=None, _definition=definition):
                if str(tool_name) != _definition.tool_name:
                    return {"ok": False, "code": "MODULE_TOOL_MISMATCH", "message": "模块工具绑定不一致，未执行。"}
                from agent_core.runtime.capability_gate import permit_allows_dispatch
                if not permit_allows_dispatch(
                    state=state,
                    permit=execution_permit,
                    tool_name=_definition.tool_name,
                    effect_id=str(effect_id or ""),
                    args=dict(args or {}),
                ):
                    return {"ok": False, "code": "EXECUTION_PERMIT_INVALID", "message": "执行许可无效，未执行模块能力。"}
                return _definition.executor(
                    state,
                    dict(args or {}),
                    execution_permit=execution_permit,
                    effect_id=str(effect_id or ""),
                    transactions=transactions,
                )

            rows.append(
                CapabilityBinding(
                    domain_id=cls.module_id,
                    contract=contract,
                    schema=definition.schema,
                    dispatcher=dispatcher,
                    public_label=definition.public_label,
                )
            )
        return tuple(rows)

    def contribution(self) -> ModuleContribution:
        from agent_modules.ecommerce.presentation import EcommerceObservationAdapter

        operations = tuple(default_order_operations())
        return ModuleContribution(
            module_id=self.module_id,
            version=self.version,
            capabilities=self._bindings(),
            resources=(OrderResourcePlugin(),),
            operations=operations,
            assessments=tuple(ecommerce_operation_assessments()),
            presentation_adapters=(EcommerceObservationAdapter(),),
            resource_types=frozenset({"order", "logistics", "refund", "after_sales", "invoice", "product", "coupon"}),
            action_ids=frozenset(plugin.action_id for plugin in operations) | frozenset(plugin.business_operation for plugin in operations),
            business_port_factory=get_ecommerce_business_port,
            knowledge_documents=ecommerce_builtin_knowledge_documents(),
        )
