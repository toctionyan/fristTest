from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement target, found {count}")
    write(path, text.replace(old, new, 1))


def regex_replace_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count == 0:
        if replacement.strip() in text:
            return
        raise RuntimeError(f"{path}: regex target not found")
    write(path, updated)


write(
    "services/agent-service/src/agent_core/modules/contracts.py",
    '''"""Domain-neutral contracts for explicitly installed Agent modules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent_core.operations.assessment import OperationAssessmentDefinition
from agent_core.business.contracts import BusinessPort
from agent_core.kernel.capability_registry import CapabilityBinding
from agent_core.operations.base import OperationPlugin
from agent_core.resources.base import ResourcePlugin


class PresentationAdapter(Protocol):
    """Structural module contribution consumed by the presentation subsystem."""

    adapter_id: str
    priority: int

    def blocks_from_trace(self, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project verified observations into client-neutral public blocks."""


@dataclass(frozen=True)
class SemanticOutputDefinition:
    """Capability-independent user-meaning type contributed by a domain module.

    ``legacy_effect_aliases`` are migration metadata consumed only after the
    semantic contract is frozen. They are deliberately excluded from the
    public writer vocabulary so installed capability availability cannot leak
    back into language understanding.
    """

    output_id: str
    subject_type: str
    effect_kinds: tuple[str, ...]
    description: str
    legacy_effect_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        output_id = str(self.output_id or "").strip().casefold()
        subject_type = str(self.subject_type or "").strip().casefold()
        effect_kinds = tuple(dict.fromkeys(str(value or "").strip().casefold() for value in self.effect_kinds if str(value or "").strip()))
        description = str(self.description or "").strip()
        aliases = tuple(dict.fromkeys(str(value or "").strip().casefold() for value in self.legacy_effect_aliases if str(value or "").strip()))
        if not output_id or output_id == "open":
            raise ValueError("semantic output_id must be non-empty and cannot use reserved 'open'")
        if not subject_type:
            raise ValueError(f"semantic output {output_id} requires subject_type")
        if not effect_kinds:
            raise ValueError(f"semantic output {output_id} requires effect_kinds")
        if not description:
            raise ValueError(f"semantic output {output_id} requires description")
        object.__setattr__(self, "output_id", output_id)
        object.__setattr__(self, "subject_type", subject_type)
        object.__setattr__(self, "effect_kinds", effect_kinds)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "legacy_effect_aliases", aliases)

    def public_snapshot(self) -> dict[str, Any]:
        return {
            "output_id": self.output_id,
            "subject_type": self.subject_type,
            "effect_kinds": list(self.effect_kinds),
            "description": self.description,
        }


@dataclass(frozen=True)
class ModuleContribution:
    module_id: str
    version: str
    capabilities: tuple[CapabilityBinding, ...]
    resources: tuple[ResourcePlugin, ...] = ()
    operations: tuple[OperationPlugin, ...] = ()
    assessments: tuple[OperationAssessmentDefinition, ...] = ()
    presentation_adapters: tuple[PresentationAdapter, ...] = ()
    semantic_outputs: tuple[SemanticOutputDefinition, ...] = ()
    resource_types: frozenset[str] = frozenset()
    action_ids: frozenset[str] = frozenset()
    business_port_factory: Callable[[], BusinessPort] | None = None
    knowledge_documents: tuple[dict[str, Any], ...] = ()


class AgentModule(Protocol):
    module_id: str
    version: str

    def contribution(self) -> ModuleContribution: ...
''',
)

replace_once(
    "services/agent-service/src/agent_core/modules/registry.py",
    "from .contracts import AgentModule, ModuleContribution, PresentationAdapter\n",
    "from .contracts import AgentModule, ModuleContribution, PresentationAdapter, SemanticOutputDefinition\n",
)
replace_once(
    "services/agent-service/src/agent_core/modules/registry.py",
    "        self._validate_module_identity()\n",
    "        self._validate_module_identity()\n        self._validate_semantic_outputs()\n",
)
replace_once(
    "services/agent-service/src/agent_core/modules/registry.py",
    "    def module_ids(self) -> frozenset[str]:\n        return frozenset(row.module_id for row in self._contributions)\n\n",
    '''    def _validate_semantic_outputs(self) -> None:
        seen: set[str] = set()
        aliases: dict[str, list[str]] = {}
        for contribution in self._contributions:
            for definition in contribution.semantic_outputs:
                if definition.output_id in seen:
                    raise ValueError(f"duplicate semantic output_id: {definition.output_id}")
                seen.add(definition.output_id)
                for alias in definition.legacy_effect_aliases:
                    aliases.setdefault(alias, []).append(definition.output_id)
        oversized = {alias: values for alias, values in aliases.items() if len(values) > 8}
        if oversized:
            raise ValueError(f"legacy semantic alias expands to more than 8 outputs: {sorted(oversized)}")

    def module_ids(self) -> frozenset[str]:
        return frozenset(row.module_id for row in self._contributions)

    def semantic_output_definitions(self) -> tuple[SemanticOutputDefinition, ...]:
        return tuple(
            definition
            for contribution in self._contributions
            for definition in contribution.semantic_outputs
        )

    def semantic_output_index(self) -> dict[str, dict[str, object]]:
        return {
            definition.output_id: definition.public_snapshot()
            for definition in self.semantic_output_definitions()
        }

    def semantic_output_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.semantic_output_index()))

    def semantic_vocabulary_snapshot(self) -> dict[str, object]:
        """Public pre-freeze vocabulary. Never expose capability availability."""
        return {
            "version": "semantic-output-vocabulary@1",
            "authority": "domain_semantics_only_capability_independent",
            "availability_exposed": False,
            "tool_names_exposed": False,
            "outputs": [
                definition.public_snapshot()
                for definition in sorted(
                    self.semantic_output_definitions(), key=lambda row: row.output_id
                )
            ],
        }

    def legacy_semantic_output_aliases(self) -> dict[str, tuple[str, ...]]:
        """Internal post-freeze migration compiler from legacy effect identity."""
        aliases: dict[str, list[str]] = {}
        for definition in self.semantic_output_definitions():
            for alias in definition.legacy_effect_aliases:
                aliases.setdefault(alias, []).append(definition.output_id)
        return {
            alias: tuple(sorted(dict.fromkeys(output_ids)))
            for alias, output_ids in aliases.items()
        }

''',
)

write(
    "services/agent-service/src/agent_modules/ecommerce/semantic_vocabulary.py",
    '''"""Capability-independent ecommerce semantic output vocabulary.

The writer may see these domain meanings before semantic freeze. This file
contains no tool name, capability key, availability flag, planner rule,
discovery example or exclusion example. Legacy aliases are internal migration
metadata used only by the deterministic post-freeze compatibility compiler.
"""
from __future__ import annotations

from agent_core.modules.contracts import SemanticOutputDefinition


def _output(
    output_id: str,
    subject_type: str,
    effect_kinds: tuple[str, ...],
    description: str,
    *legacy_aliases: str,
) -> SemanticOutputDefinition:
    return SemanticOutputDefinition(
        output_id=output_id,
        subject_type=subject_type,
        effect_kinds=effect_kinds,
        description=description,
        legacy_effect_aliases=tuple(legacy_aliases),
    )


SEMANTIC_OUTPUTS = (
    _output("order.collection", "order", ("read",), "订单集合及其可见成员。", "order.list:order"),
    _output("order.details", "order", ("read",), "订单的已验证业务详情。", "order.query_details:order"),
    _output("shipment.current_status", "shipment", ("read",), "物流当前状态或所处阶段。", "order.query_logistics:order"),
    _output("shipment.eta", "shipment", ("read",), "物流预计送达时间。", "order.query_logistics:order"),
    _output("shipment.tracking", "shipment", ("read",), "物流轨迹、节点或运输进展。", "order.query_logistics:order"),
    _output("refund.status", "refund", ("read",), "退款申请的当前处理状态。", "refund.query_status:refund"),
    _output("after_sales.status", "after_sales_request", ("read",), "售后申请的当前处理状态。", "after_sales.query_status:after_sales_request"),
    _output("invoice.status", "invoice", ("read",), "发票申请或开具状态。", "invoice.query_status:invoice"),
    _output("invoice.policy", "order", ("consult",), "与订单开票有关的政策说明。", "invoice.consult_policy:order"),
    _output("refund.policy", "order", ("consult",), "与订单退款有关的政策说明。", "refund.consult_policy:order"),
    _output("after_sales.policy", "order", ("consult",), "与订单售后有关的政策说明。", "after_sales.consult_policy:order"),
    _output("warranty.policy", "order", ("consult",), "与订单或商品保修有关的政策说明。", "warranty.consult_policy:order"),
    _output("refund.eligibility", "order", ("read",), "订单当前是否具备退款办理资格及其已验证结论。", "refund.assess_eligibility:order"),
    _output("order.cancellation", "order", ("cancel",), "取消指定订单所产生的外部业务效果。", "order.cancel:order"),
    _output("after_sales.request", "order", ("create",), "创建售后申请所产生的外部业务效果。", "after_sales.create:order"),
    _output("refund.request", "order", ("create",), "创建退款申请所产生的外部业务效果。", "refund.create:order"),
    _output("invoice.request", "order", ("create",), "创建开票申请所产生的外部业务效果。", "invoice.create:order"),
    _output("transaction.status", "transaction", ("read",), "办理事务的当前生命周期状态。", "transaction.query_status:transaction"),
    _output("refund.eligibility.collection", "refund_eligibility", ("read",), "当前仍有效的退款资格结论集合。", "refund.list_eligibilities:refund_eligibility"),
    _output("transaction.draft.collection", "transaction_draft", ("read",), "当前仍有效的办理草稿集合。", "transaction.list_drafts:transaction_draft"),
    _output("transaction.draft.dismissal", "transaction_draft", ("cancel", "dismiss"), "撤销或关闭一个仍有效的办理草稿。", "transaction.cancel_draft:transaction_draft"),
    _output("refund.eligibility.dismissal", "refund_eligibility", ("dismiss",), "关闭一个仍有效的退款资格结论。", "refund.dismiss_eligibility:refund_eligibility"),
    # Intentionally has zero installed capability coverage. Keeping this valid
    # semantic meaning in the same vocabulary proves that vocabulary presence
    # does not reveal or imply executability.
    _output("courier.contact.phone", "courier", ("read",), "配送人员的联系电话。"),
)

__all__ = ["SEMANTIC_OUTPUTS"]
''',
)

replace_once(
    "services/agent-service/src/agent_modules/ecommerce/module.py",
    "from agent_modules.ecommerce.rag_seed import ecommerce_builtin_knowledge_documents\n",
    "from agent_modules.ecommerce.rag_seed import ecommerce_builtin_knowledge_documents\nfrom agent_modules.ecommerce.semantic_vocabulary import SEMANTIC_OUTPUTS\n",
)
replace_once(
    "services/agent-service/src/agent_modules/ecommerce/module.py",
    "            presentation_adapters=(EcommerceObservationAdapter(),),\n",
    "            presentation_adapters=(EcommerceObservationAdapter(),),\n            semantic_outputs=SEMANTIC_OUTPUTS,\n",
)

protocol_path = "services/agent-service/src/agent_core/lifecycle/protocol.py"
old_requested_effect = '''                            "requested_effect": {
                                "type": "object",
                                "description": ("开放业务效果身份；domain、operation、object_type 三字段必须完整。"
                                                "若当前部署登记的业务效果身份与用户请求精确对应，必须逐字段使用该身份；"
                                                "没有精确对应时保留开放身份，禁止改写成相近能力或泛化类别。"),
                                "properties": {
                                    "domain": {"type": "string"},
                                    "operation": {"type": "string"},
                                    "object_type": {"type": "string"},
                                    "raw_description": {"type": "string"},
                                },
                                "required": ["domain", "operation", "object_type"],
                                "additionalProperties": False,
                            },
'''
new_requested_effect = '''                            "requested_effect": {
                                "type": "object",
                                "description": (
                                    "能力无关的用户业务效果。effect_kind 与 subject_type 描述用户语义；"
                                    "requested_outputs 只引用当前语义输出词汇中的 canonical output_id，或在词汇没有该概念时使用 open。"
                                    "语义输出词汇不包含能力可用性，因此看到 output_id 不代表系统能执行。"
                                ),
                                "properties": {
                                    "effect_kind": {
                                        "type": "string",
                                        "enum": ["read", "consult", "create", "update", "cancel", "dismiss", "other"],
                                    },
                                    "subject_type": {"type": "string"},
                                    "requested_outputs": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 8,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "output_id": {
                                                    "type": "string",
                                                    "description": "canonical semantic output ID；词汇不存在时只能使用 open。",
                                                },
                                                "evidence_span": {
                                                    "type": "string",
                                                    "description": "必须是当前 Goal evidence_span 内、直接证明该输出需求的连续原文。",
                                                },
                                                "open_description": {
                                                    "type": "string",
                                                    "description": "仅 output_id=open 时填写，原样描述词汇中不存在的用户可见结果。",
                                                },
                                            },
                                            "required": ["output_id", "evidence_span"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "raw_description": {"type": "string"},
                                },
                                "required": ["effect_kind", "subject_type", "requested_outputs", "raw_description"],
                                "additionalProperties": False,
                            },
'''
replace_once(protocol_path, old_requested_effect, new_requested_effect)
replace_once(
    protocol_path,
    '''def planning_schemas() -> list[dict[str, Any]]:
    """Expose the sole semantic declaration protocol before capability discovery."""
    return [deepcopy(DECLARE_TURN_GOALS_SCHEMA)]
''',
    '''def planning_schemas(*, semantic_output_ids: list[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """Expose the sole semantic declaration protocol before capability discovery.

    ``semantic_output_ids`` come from the capability-independent module
    vocabulary. They are not Tool/capability identities and do not encode
    availability. The reserved ``open`` value preserves meanings not present in
    that vocabulary instead of nearest-matching them.
    """
    schema = deepcopy(DECLARE_TURN_GOALS_SCHEMA)
    ids = list(dict.fromkeys(
        str(value or "").strip().casefold()
        for value in list(semantic_output_ids or [])
        if str(value or "").strip() and str(value or "").strip().casefold() != "open"
    ))
    if ids:
        item = (
            schema["function"]["parameters"]["properties"]["goals"]["items"]
            ["properties"]["requested_effect"]["properties"]["requested_outputs"]["items"]
        )
        item["properties"]["output_id"] = {
            "type": "string",
            "enum": [*ids, "open"],
            "description": "能力无关 canonical semantic output ID；没有对应概念时使用 open。",
        }
    return [schema]
''',
)

semantic_path = "services/agent-service/src/agent_core/lifecycle/semantic_contract.py"
regex_replace_once(
    semantic_path,
    r"def normalize_requested_effect\(.*?\n\ndef _normalized_goal_base",
    '''def normalize_requested_effect(raw: Any, *, description: str = "") -> dict[str, Any]:
    """Normalize capability-independent requested outputs or a legacy checkpoint.

    New provider declarations use ``effect_kind/subject_type/requested_outputs``.
    The legacy three-field identity remains readable only for historical/direct
    migration callers; provider schema no longer exposes it.
    """
    source = raw if isinstance(raw, dict) else {}
    if "requested_outputs" in source:
        effect_kind = _text(source.get("effect_kind"), limit=80).casefold()
        subject_type = _text(source.get("subject_type"), limit=160).casefold()
        raw_description = _text(source.get("raw_description") or description)
        values = source.get("requested_outputs")
        if not effect_kind:
            raise ValueError("requested_effect.effect_kind_required")
        if not subject_type:
            raise ValueError("requested_effect.subject_type_required")
        if not isinstance(values, list) or not values or len(values) > 8:
            raise ValueError("requested_effect.requested_outputs_required")
        outputs: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, raw_output in enumerate(values):
            if not isinstance(raw_output, dict):
                raise ValueError(f"requested_effect.output_invalid:{index}")
            output_id = _text(raw_output.get("output_id"), limit=240).casefold()
            evidence_span = _text(raw_output.get("evidence_span"), limit=240)
            open_description = _text(raw_output.get("open_description"), limit=500)
            if not output_id or not evidence_span:
                raise ValueError(f"requested_effect.output_incomplete:{index}")
            if output_id in seen:
                raise ValueError(f"requested_effect.output_duplicate:{output_id}")
            if output_id == "open" and not open_description:
                raise ValueError("requested_effect.open_description_required")
            if output_id != "open" and open_description:
                raise ValueError("requested_effect.open_description_only_for_open")
            seen.add(output_id)
            row = {"output_id": output_id, "evidence_span": evidence_span}
            if open_description:
                row["open_description"] = open_description
            outputs.append(row)
        return {
            "effect_kind": effect_kind,
            "subject_type": subject_type,
            "requested_outputs": outputs,
            "raw_description": raw_description,
        }

    # Compatibility-only historical representation. New provider schemas do
    # not expose these fields, so this branch cannot become a second writer
    # authority for newly declared turns.
    effect = {
        "domain": _text(source.get("domain"), limit=120),
        "operation": _text(source.get("operation"), limit=160),
        "object_type": _text(source.get("object_type"), limit=160),
        "raw_description": _text(source.get("raw_description") or description),
    }
    if not effect["operation"]:
        raise ValueError("requested_effect.operation_required")
    if not effect["domain"]:
        effect["domain"] = "open"
    if not effect["object_type"]:
        effect["object_type"] = "unspecified"
    return effect


def _normalized_goal_base''',
)
replace_once(
    semantic_path,
    '''            expected_object_type=str((goal.get("requested_effect") or {}).get("object_type") or ""),
''',
    '''            expected_object_type=str(
                (goal.get("requested_effect") or {}).get("subject_type")
                or (goal.get("requested_effect") or {}).get("object_type")
                or ""
            ),
''',
)

kernel_semantic = "services/agent-service/src/agent_core/kernel/semantic_contract.py"
replace_once(
    kernel_semantic,
    '''    object_type = _text((row.get("requested_effect") or {}).get("object_type"), limit=160)
''',
    '''    requested_effect = row.get("requested_effect") if isinstance(row.get("requested_effect"), dict) else {}
    object_type = _text(
        requested_effect.get("subject_type") or requested_effect.get("object_type"), limit=160
    )
''',
)

capability_effects = "services/agent-service/src/agent_core/runtime/capability_effects.py"
replace_once(
    capability_effects,
    "from copy import deepcopy\nfrom typing import Any, Iterable\n",
    "from copy import deepcopy\nfrom itertools import combinations\nfrom typing import Any, Iterable\n",
)
regex_replace_once(
    capability_effects,
    r"def _clean\(.*?\n\ndef _effect_semantic_guidance",
    '''def _clean(value: Any) -> str:
    return str(value or "").strip().casefold()


_SEMANTIC_OUTPUT_PREFIX = "semantic-output:"
_SEMANTIC_OUTPUT_SET_PREFIX = "semantic-output-set:"


def requested_semantic_output_ids(raw: Any) -> tuple[str, ...]:
    row = raw if isinstance(raw, dict) else {}
    values = row.get("requested_outputs")
    if not isinstance(values, list):
        return ()
    result: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        output_id = _clean(item.get("output_id"))
        if output_id and output_id not in result:
            result.append(output_id)
    return tuple(sorted(result))


def _semantic_output_identity(output_ids: Iterable[str]) -> str:
    values = tuple(sorted(dict.fromkeys(_clean(value) for value in output_ids if _clean(value))))
    if not values:
        return ""
    if len(values) == 1:
        return f"{_SEMANTIC_OUTPUT_PREFIX}{values[0]}"
    return f"{_SEMANTIC_OUTPUT_SET_PREFIX}{'|'.join(values)}"


def canonical_effect_identity(raw: Any) -> str:
    """Return one exact identity without language inference or similarity.

    New turns are keyed by the frozen requested semantic-output set. Legacy
    ``domain.operation:object_type`` identities remain readable only as a
    migration compatibility representation.
    """
    outputs = requested_semantic_output_ids(raw)
    if outputs:
        return _semantic_output_identity(outputs)
    row = raw if isinstance(raw, dict) else {}
    domain = _clean(row.get("domain")) or "open"
    operation = _clean(row.get("operation"))
    object_type = _clean(row.get("object_type")) or "unspecified"
    return f"{domain}.{operation}:{object_type}" if operation else ""


def effect_identity(domain: str, operation: str, object_type: str) -> str:
    return canonical_effect_identity(
        {"domain": domain, "operation": operation, "object_type": object_type}
    )


def _contract_effects(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        raw = str(value or "").strip().casefold()
        if not raw or ":" not in raw or "." not in raw.split(":", 1)[0]:
            continue
        if raw not in result:
            result.append(raw)
    return tuple(result)


def _legacy_semantic_aliases() -> dict[str, tuple[str, ...]]:
    try:
        from agent_core.modules.registry import current_module_registry
        return current_module_registry().legacy_semantic_output_aliases()
    except RuntimeError:
        return {}


def _semantic_identities_for_legacy_effects(values: Iterable[str]) -> tuple[str, ...]:
    aliases = _legacy_semantic_aliases()
    output_ids: list[str] = []
    for legacy in _contract_effects(values):
        for output_id in aliases.get(legacy, ()):
            if output_id not in output_ids:
                output_ids.append(output_id)
    # ModuleRegistry bounds one legacy alias to at most eight output IDs, so
    # exact subset identities remain finite and deterministic. This lets one
    # legacy broad logistics contract prove status, ETA, tracking, or an exact
    # requested combination without a model mapper.
    identities: list[str] = []
    for size in range(1, len(output_ids) + 1):
        for subset in combinations(sorted(output_ids), size):
            identity = _semantic_output_identity(subset)
            if identity and identity not in identities:
                identities.append(identity)
    return tuple(identities)


def completion_effects_for_contract(contract: Any) -> tuple[str, ...]:
    legacy = _contract_effects(getattr(contract, "completion_effects", ()) or ())
    semantic = _semantic_identities_for_legacy_effects(legacy)
    return tuple(dict.fromkeys((*legacy, *semantic)))


def support_effects_for_contract(contract: Any) -> tuple[str, ...]:
    legacy = _contract_effects(getattr(contract, "support_effects", ()) or ())
    semantic = _semantic_identities_for_legacy_effects(legacy)
    return tuple(dict.fromkeys((*legacy, *semantic)))


def _effect_semantic_guidance''',
)

goal_planning = "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    goal_planning,
    '''            "requested_effect_rule": "preserve the user's open business effect; do not coerce it into a nearby registered capability",
''',
    '''            "requested_effect_rule": "rederive effect_kind, subject_type and requested_outputs from current_user_input; never copy verifier semantic answers or capability identities",
''',
)
replace_once(
    goal_planning,
    '''def _known_tool_names(capability_registry: CapabilityRegistry) -> set[str]:
''',
    '''def _validate_semantic_output_effect(
    effect: dict[str, Any],
    *,
    user_text: str,
    goal_evidence_span: str,
    goal_id: str,
) -> list[str]:
    outputs = effect.get("requested_outputs")
    if not isinstance(outputs, list):
        return []  # historical/direct compatibility representation
    errors: list[str] = []
    try:
        from agent_core.modules.registry import current_module_registry
        vocabulary = current_module_registry().semantic_output_index()
    except RuntimeError:
        vocabulary = {}
    effect_kind = _clean_text(effect.get("effect_kind"), limit=80).casefold()
    subject_type = _clean_text(effect.get("subject_type"), limit=160).casefold()
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            errors.append(f"semantic_output_invalid:{goal_id}:{index}")
            continue
        output_id = _clean_text(output.get("output_id"), limit=240).casefold()
        span = _clean_text(output.get("evidence_span"), limit=240)
        if not span or span not in user_text or not goal_evidence_span or span not in goal_evidence_span:
            errors.append(f"semantic_output_evidence_not_in_goal:{goal_id}:{index}")
        if output_id == "open":
            if not _clean_text(output.get("open_description"), limit=500):
                errors.append(f"semantic_open_description_required:{goal_id}:{index}")
            continue
        definition = vocabulary.get(output_id)
        if definition is None:
            errors.append(f"semantic_output_unknown:{goal_id}:{output_id or index}")
            continue
        if subject_type != str(definition.get("subject_type") or "").casefold():
            errors.append(f"semantic_output_subject_mismatch:{goal_id}:{output_id}")
        if effect_kind not in {
            str(value).casefold() for value in list(definition.get("effect_kinds") or [])
        }:
            errors.append(f"semantic_output_effect_kind_mismatch:{goal_id}:{output_id}")
    return errors


def _known_tool_names(capability_registry: CapabilityRegistry) -> set[str]:
''',
)
replace_once(
    goal_planning,
    '''            requested_effect = normalize_requested_effect(raw_effect, description=description)
            effect_source = "model_open_effect"
''',
    '''            requested_effect = normalize_requested_effect(raw_effect, description=description)
            errors.extend(_validate_semantic_output_effect(
                requested_effect,
                user_text=user_text,
                goal_evidence_span=evidence_span,
                goal_id=goal_id,
            ))
            effect_source = (
                "model_semantic_output_effect"
                if "requested_outputs" in requested_effect
                else "legacy_direct_compatibility_effect"
            )
''',
)
replace_once(
    goal_planning,
    '''                    expected_object_type=str((row.get("requested_effect") or {}).get("object_type") or ""),
''',
    '''                    expected_object_type=str(
                        (row.get("requested_effect") or {}).get("subject_type")
                        or (row.get("requested_effect") or {}).get("object_type")
                        or ""
                    ),
''',
)
regex_replace_once(
    goal_planning,
    r"def _alignment_repair_feedback\(.*?\n\ndef validate_goal_declaration",
    '''def _alignment_repair_feedback(alignment: GoalAlignmentVerdict) -> dict[str, Any]:
    """Return violation-only feedback; never a verifier-authored semantic graph."""
    if alignment.verdict != "incomplete" or not alignment.independent:
        return {}
    details = alignment.details if isinstance(alignment.details, dict) else {}
    spans: list[str] = []
    for value in (*alignment.evidence_spans, *alignment.missing_spans):
        span = _clean_text(value, limit=240)
        if span and span not in spans:
            spans.append(span)
    if alignment.reason_code == "goal_alignment_dependency_graph_mismatch":
        for raw in list(details.get("dependency_edges") or []):
            if not isinstance(raw, dict):
                continue
            span = _clean_text(raw.get("basis_span"), limit=240)
            if span and span not in spans:
                spans.append(span)
    return {
        "independent_verifier_feedback": {
            "authority": "read_only_violation_evidence",
            "required_action": "redeclaration_from_current_user_input",
            "violation": {
                "field": "depends_on" if alignment.reason_code == "goal_alignment_dependency_graph_mismatch" else "semantic_declaration",
                "reason_code": alignment.reason_code,
                "evidence_spans": spans,
            },
            "constraints": [
                "rederive_semantics_from_current_user_input",
                "do_not_copy_verifier_dependency_edges_or_replacement_semantic_values",
                "do_not_infer_tool_order_or_capability_prerequisites_as_goal_dependencies",
                "runtime_does_not_auto_rewrite_the_candidate",
            ],
        }
    }


def _granularity_repair_feedback(granularity: Any) -> dict[str, Any]:
    verdict = str(getattr(granularity, "verdict", "") or "")
    reason_code = str(getattr(granularity, "reason_code", "") or "")
    if verdict not in {"under_split", "over_split", "mixed"}:
        return {}
    spans: list[str] = []
    for finding in tuple(getattr(granularity, "findings", ()) or ()):
        if not isinstance(finding, dict):
            continue
        span = _clean_text(finding.get("evidence_span"), limit=240)
        if span and span not in spans:
            spans.append(span)
    return {
        "independent_verifier_feedback": {
            "authority": "read_only_violation_evidence",
            "required_action": "redeclaration_from_current_user_input",
            "violation": {
                "field": "goal_inventory",
                "reason_code": reason_code or f"goal_granularity_{verdict}",
                "evidence_spans": spans,
            },
            "constraints": [
                "literal_user_text_spans_only",
                "do_not_emit_or_copy_recommended_semantic_roles",
                "do_not_copy_verifier_dependency_graph_or_requested_effect_values",
                "preserve_unsupported_or_open_business_meaning",
            ],
        }
    }


def validate_goal_declaration''',
)
text = read(goal_planning)
text = text.replace("domain, operation, object_type and raw_description", "effect_kind, subject_type, requested_outputs and raw_description")
text = text.replace("domain, operation, object_type and raw_description together", "effect_kind, subject_type, requested_outputs and raw_description together")
write(goal_planning, text)

granularity_path = "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"
text = read(granularity_path)
text = text.replace('                "recommended_role": role or None,\n', '')
text = text.replace('                "recommended_role": "goal",\n', '')
text = text.replace('                "recommended_role": "support_step",\n', '')
write(granularity_path, text)

dialogue_path = "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
replace_once(
    dialogue_path,
    '''from agent_core.runtime.capability_effects import (
    capability_effect_index,
    discover_exact_effect_surface,
)
''',
    '''from agent_core.runtime.capability_effects import discover_exact_effect_surface
''',
)
replace_once(
    dialogue_path,
    "from agent_core.kernel.capability_registry import CapabilityRegistry\n",
    "from agent_core.kernel.capability_registry import CapabilityRegistry\nfrom agent_core.modules.registry import current_module_registry\n",
)
replace_once(
    dialogue_path,
    '''def get_model_profile():
    from agent_core.config import get_model_profile as resolve_profile

    return resolve_profile()


def _discover_capability_surface(
''',
    '''def get_model_profile():
    from agent_core.config import get_model_profile as resolve_profile

    return resolve_profile()


def _planning_semantic_vocabulary_snapshot() -> dict[str, Any]:
    """Return only domain meaning; never capability availability."""
    try:
        return current_module_registry().semantic_vocabulary_snapshot()
    except RuntimeError:
        return {
            "version": "semantic-output-vocabulary@1",
            "authority": "domain_semantics_only_capability_independent",
            "availability_exposed": False,
            "tool_names_exposed": False,
            "outputs": [],
            "status": "module_registry_unavailable",
        }


def _planning_semantic_output_ids() -> list[str]:
    return [
        str(row.get("output_id") or "")
        for row in list(_planning_semantic_vocabulary_snapshot().get("outputs") or [])
        if isinstance(row, dict) and str(row.get("output_id") or "")
    ]


def _discover_capability_surface(
''',
)
replace_once(
    dialogue_path,
    '''            else planning_schemas()
            if planning_phase
''',
    '''            else planning_schemas(semantic_output_ids=_planning_semantic_output_ids())
            if planning_phase
''',
)
replace_once(
    dialogue_path,
    '''- requested_effect 必须完整填写 domain、operation、object_type。若用户业务效果与“当前部署登记的业务效果身份”中的某个身份精确对应，必须逐字段使用该精确身份；只有不存在精确对应时才保留开放身份。禁止用 query/action 等泛化类别替代已登记的精确业务效果，也禁止为了现有能力做同义词、近似或邻近能力改写。
''',
    '''- requested_effect 必须填写 effect_kind、subject_type、requested_outputs、raw_description。requested_outputs 只能选择能力无关语义输出词汇中的 canonical output_id；词汇没有该用户概念时使用 open 并保留原始描述。语义词汇不包含能力可用性，禁止因为某个能力存在、缺失或名称相近而改变用户业务效果。
''',
)
replace_once(
    dialogue_path,
    '''每个 Goal 必须给出开放 requested_effect(domain/operation/object_type/raw_description)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。''',
    '''每个 Goal 必须给出能力无关 requested_effect(effect_kind/subject_type/requested_outputs/raw_description)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。requested_outputs 从当前语义输出词汇选择；没有对应概念时使用 open，绝不能按已安装能力改写。''',
)
replace_once(
    dialogue_path,
    '''    surface = state.get("capability_surface") if isinstance(state.get("capability_surface"), dict) else None
''',
    '''    semantic_vocabulary = (
        _planning_semantic_vocabulary_snapshot()
        if planning_phase
        else {"status": "frozen_semantics_only", "availability_exposed": False}
    )
    surface = state.get("capability_surface") if isinstance(state.get("capability_surface"), dict) else None
''',
)
regex_replace_once(
    dialogue_path,
    r'''【当前部署登记的业务效果身份】\n.*?\n\n【当前模块注册的能力规则】''',
    '''【能力无关语义输出词汇】
{semantic_vocabulary}
说明：这里只发布领域语义 output_id、subject_type、effect_kinds 和语义说明，不包含 Tool 名、Capability key、可用性、规划规则、发现/排除示例或 supported 标记。词汇项可以没有任何已安装能力；没有对应语义概念时必须保留 open，而不是靠相似度选择附近含义。

【当前模块注册的能力规则】''',
)

write(
    "services/agent-service/tests/architecture/test_semantic_single_writer_invariants.py",
    '''from __future__ import annotations

import inspect
import json


def test_planning_schema_is_requested_output_based_and_has_no_legacy_deployed_identity_fields() -> None:
    from agent_core.lifecycle.protocol import planning_schemas

    schema = planning_schemas(semantic_output_ids=["shipment.current_status", "courier.contact.phone"])[0]
    effect = (
        schema["function"]["parameters"]["properties"]["goals"]["items"]
        ["properties"]["requested_effect"]
    )
    assert set(effect["required"]) == {"effect_kind", "subject_type", "requested_outputs", "raw_description"}
    assert not {"domain", "operation", "object_type"}.intersection(effect["properties"])
    output_id = effect["properties"]["requested_outputs"]["items"]["properties"]["output_id"]
    assert output_id["enum"] == ["shipment.current_status", "courier.contact.phone", "open"]
    assert "当前部署登记的业务效果身份" not in json.dumps(schema, ensure_ascii=False)


def test_pre_freeze_prompt_source_never_renders_capability_effect_index() -> None:
    from agent_core.lifecycle import dialogue_runtime

    runtime_source = inspect.getsource(dialogue_runtime._loop_runtime_prompt)
    static_prompt = dialogue_runtime._loop_static_system_prompt()
    assert "capability_effect_index" not in runtime_source
    assert "当前部署登记的业务效果身份" not in runtime_source
    assert "当前部署登记的业务效果身份" not in static_prompt
    assert "能力无关语义输出词汇" in runtime_source
    assert "requested_outputs" in static_prompt


def test_alignment_and_granularity_feedback_are_violation_only() -> None:
    from types import SimpleNamespace
    from agent_core.lifecycle.goal_planning import (
        GoalAlignmentVerdict,
        _alignment_repair_feedback,
        _granularity_repair_feedback,
    )

    alignment = GoalAlignmentVerdict(
        "incomplete",
        ("然后退款",),
        (),
        "goal_alignment_dependency_graph_mismatch",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": False,
            "dependency_edges": [{
                "dependent_goal_id": "g2",
                "requires_result_of_goal_id": "g1",
                "basis_kind": "result_reference",
                "basis_span": "然后退款",
            }],
        },
    )
    alignment_feedback = _alignment_repair_feedback(alignment)
    encoded = json.dumps(alignment_feedback, ensure_ascii=False)
    assert "dependency_edges" not in encoded
    assert "requires_result_of_goal_id" not in encoded
    assert alignment_feedback["independent_verifier_feedback"]["authority"] == "read_only_violation_evidence"

    granularity = SimpleNamespace(
        verdict="under_split",
        reason_code="blind_inventory_has_more_outcomes_than_declared_goals",
        findings=({
            "goal_id": None,
            "reason": "blind_inventory_outcome_not_covered",
            "recommended_role": "goal",
            "evidence_span": "快递员手机号",
        },),
        details={},
    )
    granularity_feedback = _granularity_repair_feedback(granularity)
    encoded = json.dumps(granularity_feedback, ensure_ascii=False)
    assert "recommended_role" not in encoded
    assert "dependency_edges" not in encoded
    assert "快递员手机号" in encoded
''',
)

write(
    "services/agent-service/tests/runtime/test_semantic_output_coverage.py",
    '''from __future__ import annotations


def _installed_registry():
    from agent_core.modules.registry import ModuleRegistry, configure_registry_providers
    from agent_modules.ecommerce.module import EcommerceModule

    modules = ModuleRegistry([EcommerceModule()])
    configure_registry_providers(
        runtime_registry=modules.build_runtime_registry,
        module_registry=lambda: modules,
    )
    return modules, modules.build_runtime_registry().capabilities


def _goal(goal_id: str, *output_ids: str, effect_kind: str = "read", subject_type: str = "shipment") -> dict:
    return {
        "goal_id": goal_id,
        "required": True,
        "requested_effect": {
            "effect_kind": effect_kind,
            "subject_type": subject_type,
            "requested_outputs": [
                {"output_id": output_id, "evidence_span": output_id}
                for output_id in output_ids
            ],
            "raw_description": "test",
        },
    }


def test_semantic_vocabulary_contains_zero_capability_meaning_without_availability_leak() -> None:
    modules, registry = _installed_registry()
    snapshot = modules.semantic_vocabulary_snapshot()
    outputs = {row["output_id"]: row for row in snapshot["outputs"]}
    assert snapshot["availability_exposed"] is False
    assert snapshot["tool_names_exposed"] is False
    assert "courier.contact.phone" in outputs
    serialized = str(snapshot)
    assert "get_order_logistics" not in serialized
    assert "report_unsupported_request" not in serialized

    from agent_core.runtime.capability_effects import discover_exact_effect_surface
    result = discover_exact_effect_surface(registry, [_goal("g1", "courier.contact.phone", subject_type="courier")])
    row = result["goals"][0]
    assert row["status"] == "absent_proven"
    assert row["completion_tools"] == []
    assert "report_unsupported_request" in row["candidate_tools"]


def test_logistics_status_eta_and_exact_combination_use_one_deterministic_coverage_compiler() -> None:
    _modules, registry = _installed_registry()
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    for outputs in [
        ("shipment.current_status",),
        ("shipment.eta",),
        ("shipment.tracking",),
        ("shipment.current_status", "shipment.eta"),
    ]:
        result = discover_exact_effect_surface(registry, [_goal("g1", *outputs)])
        row = result["goals"][0]
        assert row["status"] == "exact_supported"
        assert row["completion_tools"] == ["get_order_logistics"]
        assert row["match_basis"] == "structured_identity_exact_only"
        assert row["similarity_used"] is False


def test_supported_and_unsupported_siblings_do_not_collapse_into_each_other() -> None:
    _modules, registry = _installed_registry()
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    result = discover_exact_effect_surface(
        registry,
        [
            _goal("status", "shipment.current_status"),
            _goal("phone", "courier.contact.phone", subject_type="courier"),
        ],
    )
    by_id = {row["goal_id"]: row for row in result["goals"]}
    assert by_id["status"]["completion_tools"] == ["get_order_logistics"]
    assert by_id["phone"]["completion_tools"] == []
    assert by_id["phone"]["status"] == "absent_proven"
    assert result["unsupported_goal_ids"] == ["phone"]


def test_open_output_never_auto_coerces_to_registered_capability_and_legacy_checkpoint_still_reads() -> None:
    _modules, registry = _installed_registry()
    from agent_core.runtime.capability_effects import discover_exact_effect_surface

    open_goal = _goal("open", "open", subject_type="courier")
    open_goal["requested_effect"]["requested_outputs"][0]["open_description"] = "配送人员的私人联系方式"
    result = discover_exact_effect_surface(registry, [open_goal])
    assert result["goals"][0]["status"] == "absent_proven"

    legacy = {
        "goal_id": "legacy",
        "required": True,
        "requested_effect": {
            "domain": "order",
            "operation": "query_logistics",
            "object_type": "order",
        },
    }
    legacy_result = discover_exact_effect_surface(registry, [legacy])
    assert legacy_result["goals"][0]["completion_tools"] == ["get_order_logistics"]
''',
)

# Append one focused freeze/validation regression to the already-governed test file.
unified_path = "services/agent-service/tests/runtime/test_unified_semantic_planning_contract.py"
unified = read(unified_path)
marker = "\ndef test_v2018_requested_outputs_freeze_as_digest_bound_semantics() -> None:\n"
if marker not in unified:
    unified += '''

def test_v2018_requested_outputs_freeze_as_digest_bound_semantics() -> None:
    from agent_core.modules.registry import ModuleRegistry, configure_registry_providers
    from agent_modules.ecommerce.module import EcommerceModule
    from agent_core.lifecycle.semantic_contract import freeze_semantic_contract, semantic_contract_integrity

    modules = ModuleRegistry([EcommerceModule()])
    configure_registry_providers(
        runtime_registry=modules.build_runtime_registry,
        module_registry=lambda: modules,
    )
    goal = {
        "goal_id": "g1",
        "description": "查物流状态和预计送达时间",
        "evidence_span": "物流状态和预计送达时间",
        "requested_effect": {
            "effect_kind": "read",
            "subject_type": "shipment",
            "requested_outputs": [
                {"output_id": "shipment.current_status", "evidence_span": "物流状态"},
                {"output_id": "shipment.eta", "evidence_span": "预计送达时间"},
            ],
            "raw_description": "查物流状态和预计送达时间",
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }
    contract = freeze_semantic_contract(
        turn=3,
        user_text="查物流状态和预计送达时间",
        summary="物流查询",
        goals=[goal],
        alignment_proof={"verdict": "exact"},
    )
    assert contract["authority"] == "sole_formal_turn_semantics"
    assert contract["goals"][0]["requested_effect"]["requested_outputs"][1]["output_id"] == "shipment.eta"
    assert semantic_contract_integrity(contract)["ok"] is True
'''
    write(unified_path, unified)

print("V20.18 A2/B builder patch applied")
