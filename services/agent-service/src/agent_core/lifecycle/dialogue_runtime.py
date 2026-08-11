from __future__ import annotations

from copy import deepcopy
from typing import Any

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
except Exception:  # pragma: no cover
    AIMessage = HumanMessage = SystemMessage = None  # type: ignore

from agent_core.lifecycle.protocol import (
    ASK_USER_CLARIFICATION_SCHEMA,
    TERMINAL_TOOL_NAMES,
    agent_loop_schemas,
    planning_schemas,
)
from agent_core.kernel.capability_registry import CapabilityRegistry
from agent_core.kernel.plan_projection_contract import read_plan_projection
from agent_core.observability.audit_turn_trace import create_plan_id
from agent_core.observability.failure_replay import build_failure_replay
from agent_core.context import ContextBundleBuilder, compile_provider_context, render_context_bundle
from agent_core.lifecycle.budget import compute_loop_budget, verified_history_recall_results
from agent_core.lifecycle.finalizer import _answer_from_terminal_tool, _append_terminal_protocol_message, _safe_general_reply
from agent_core.lifecycle.graph_routes import _loop_budget_fallback
from agent_core.lifecycle.task_board import complete_tasks_from_terminal
from agent_core.lifecycle.goal_planning import goal_plan_ready
from agent_core.lifecycle.continuation_runtime import verified_continuation_tool_hints
from agent_core.lifecycle.workflow_runtime import (
    build_workflow_plan,
    materialize_plan_runtime,
    project_plan_runtime,
    verify_workflow_for_final_answer,
)
from agent_core.lifecycle.plan_execution import record_terminal_goal_outcome
from agent_core.lifecycle.pretool_planner import (
    build_pretool_shadow_plan,
    compare_shadow_plan_to_model_calls,
)
from agent_core.lifecycle.pretool_execution_policy import (
    build_pretool_execution_policy,
    execution_policy_prompt_projection,
)
from agent_core.lifecycle.clarification_runtime import (
    clarification_context_projection,
    continuation_tool_hints,
    goal_blockers_for_clarification,
)
from agent_core.model_calls import ModelCallBudgetExceeded, invoke_model
from agent_core.runtime.capability_gate import build_effects
from agent_core.runtime.capability_effects import (
    capability_effect_index,
    discover_exact_effect_surface,
)
from agent_core.lifecycle.semantic_contract import semantic_goals
from agent_core.runtime.outcomes import coerce_runtime_outcome, outcome
from agent_core.transaction.interaction import interaction_response_contract
from agent_core.runtime.node_support import (
    append_decision as _append_decision,
    as_ai_message as _as_ai_message,
    last_human_index as _last_human_index,
    latest_human_text as _latest_human_text,
    max_loop_steps as _max_loop_steps,
    tool_calls as _tool_calls,
)

FINAL_PROTOCOL_MAX_RETRIES = 1
GOAL_DECLARATION_MAX_RETRIES = 2


def _goal_declaration_protocol_repair_rule(state: dict[str, Any]) -> str | None:
    """Return a tool-only planning repair after a model emitted prose.

    This is protocol feedback only. It does not infer a Goal, target, intent or
    business fact from the prose; the model must redeclare the same user turn
    through the normal semantic authority on its one remaining bounded retry.
    """
    if str(state.get("status") or "") != "GoalDeclarationProtocolRetry":
        return None
    return (
        "上一次统一语义声明没有产生且仅产生一次 declare_turn_goals 调用；纯文本回答不能建立本轮正式语义，也不会发送给用户。"
        "本次仍处于同一个用户回合的语义声明阶段，必须只调用一次 declare_turn_goals，重新完整声明当前用户原话中的 Goal、条件和依赖；"
        "不得直接回答用户、不得调用业务能力、不得根据上一次纯文本自行冻结语义。"
    )


def _declaration_clarification_required(state: dict[str, Any]) -> bool:
    """Detect a same-turn declaration rejection that explicitly requires input."""
    if goal_plan_ready(state):
        return False
    plan = state.get("current_turn_plan") if isinstance(state.get("current_turn_plan"), dict) else {}
    if int(plan.get("turn") or -1) != int(state.get("turn_index") or 0):
        return False
    if not any(
        isinstance(call, dict) and str(call.get("name") or "") == "declare_turn_goals"
        for call in list(plan.get("tool_calls") or [])
    ):
        return False
    for row in reversed(list(state.get("tool_trace") or [])):
        if not isinstance(row, dict) or str(row.get("name") or "") != "declare_turn_goals":
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        return bool(
            not result.get("ok")
            and str(result.get("code") or "") == "GOAL_DECLARATION_REQUIRES_CLARIFICATION"
        )
    return False


def get_model():
    from agent_core.config import get_model as resolve_model

    return resolve_model()


def get_model_profile():
    from agent_core.config import get_model_profile as resolve_profile

    return resolve_profile()


def _discover_capability_surface(
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
) -> dict[str, Any]:
    formal_goals = semantic_goals(state)
    if formal_goals and all(
        isinstance(row.get("requested_effect"), dict)
        and str((row.get("requested_effect") or {}).get("operation") or "").strip()
        and str((row.get("requested_effect") or {}).get("domain") or "").strip().lower() != "legacy"
        for row in formal_goals
    ):
        return discover_exact_effect_surface(
            capability_registry,
            formal_goals,
            verified_continuation_tools_by_goal=verified_continuation_tool_hints(
                state, formal_goals, capability_registry
            ),
        )
    return {
        "version": "capability-surface@2",
        "match_basis": "frozen_requested_effect_required",
        "formal_effect_match_proof_available": False,
        "goals": [],
        "candidate_tools": [],
        "tool_names": [],
    }


def _workflow_repair_tools(
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
    surface: dict[str, Any],
) -> tuple[set[str], set[str], set[str]]:
    """Return pending Goal ids plus legal completion/absence reporters.

    Newly frozen turns use exact business-effect identities. Historical
    checkpoints without requested_effect retain their old goal-type completion
    filter as a compatibility-only path.
    """

    execution_plan = read_plan_projection(state) or {}
    pending_rows = [
        goal
        for goal in list(execution_plan.get("goals") or [])
        if isinstance(goal, dict)
        and bool(goal.get("required", True))
        and str(goal.get("coverage_status") or "") == "PENDING"
    ]
    pending_goal_ids = {
        str(goal.get("goal_id") or "") for goal in pending_rows if str(goal.get("goal_id") or "")
    }
    completion_tools = {
        str(name)
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "") in pending_goal_ids
        for name in list(row.get("completion_tools") or [])
        if str(name)
    }
    pending_goals_by_id = {
        str(goal.get("goal_id") or ""): goal
        for goal in pending_rows
        if str(goal.get("goal_id") or "")
    }
    unsupported_tools = {
        str(name)
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict)
        and str(row.get("goal_id") or "") in pending_goal_ids
        # Clarification is itself a supported terminal outcome.  A missing
        # business completion capability must not turn a clarification-only
        # retry into an unsupported-request surface.
        and str(
            (pending_goals_by_id.get(str(row.get("goal_id") or "")) or {}).get("goal_type")
            or ""
        ).lower() != "clarification"
        and str(row.get("status") or "") in {"absent_proven", "completion_capability_absent"}
        for name in list(row.get("candidate_tools") or [])
        if str(name)
        and (
            (contract := capability_registry.contract_for_tool(str(name))) is not None
            and contract.execution_kind == "unsupported"
        )
    }
    return pending_goal_ids, completion_tools, unsupported_tools


def _workflow_repair_allowed_tools(
    *,
    policy_frontier: set[str] | None,
    completion_tools: set[str],
    unsupported_tools: set[str],
    clarification_only: bool = False,
) -> set[str]:
    """Keep an incomplete-workflow repair inside its exact legal frontier.

    A MatchProof rejection does not complete a PlanRun step, so ordinary
    candidate repair preserves the already policy-bounded support frontier. A
    clarification-only Goal is different: clarification is itself the supported
    terminal outcome, therefore support and unsupported-reporting capabilities
    must not leak into that retry. No branch here invents a capability outside
    the exact policy/completion surfaces supplied by Runtime.
    """
    if clarification_only:
        return {*completion_tools, "ask_user_clarification"}
    return {
        *set(policy_frontier or set()),
        *completion_tools,
        *unsupported_tools,
        "ask_user_clarification",
    }


def _workflow_repair_is_clarification_only(
    state: dict[str, Any],
    pending_goal_ids: set[str],
) -> bool:
    """Read clarification-only status from frozen Goal metadata only."""
    if not pending_goal_ids:
        return False
    execution_plan = read_plan_projection(state) or {}
    rows = [
        goal
        for goal in list(execution_plan.get("goals") or [])
        if isinstance(goal, dict)
        and str(goal.get("goal_id") or "") in pending_goal_ids
    ]
    return (
        len(rows) == len(pending_goal_ids)
        and all(str(goal.get("goal_type") or "").lower() == "clarification" for goal in rows)
    )


def _unnecessary_unique_scope_clarification(
    state: dict[str, Any],
    call: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Reject a structured target/scope gap when one public member is unique.

    The model owns all open-language relations. Runtime inspects only the
    model-declared ``missing_kind`` and the authoritative visible-result set;
    it never scans the user utterance for pronouns or zero anaphora.
    """
    args = call.get("args") if isinstance((call or {}).get("args"), dict) else {}
    missing_kind = str(args.get("missing_kind") or "").strip()
    if missing_kind not in {"target", "scope"}:
        return None
    bundle = state.get("context_bundle") if isinstance(state.get("context_bundle"), dict) else {}
    latest_refs = [
        row for row in list(bundle.get("visible_result_refs") or [])
        if isinstance(row, dict) and bool(row.get("is_latest_visible_turn"))
    ]
    members = list(dict.fromkeys(
        str(handle)
        for row in latest_refs
        for handle in list(row.get("member_handles") or [])
        if str(handle)
    ))
    if len(members) != 1:
        return None
    return {
        "reason_code": "unique_latest_visible_scope",
        "reference_mode": "model_declared_target_gap",
        "rejected_missing_kind": missing_kind or None,
        "member_handle": members[0],
        "latest_result_refs": [
            str(row.get("result_ref") or "") for row in latest_refs if str(row.get("result_ref") or "")
        ],
    }


def _bind_verified_history_recall_evidence(
    state: dict[str, Any],
    calls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Attach the immediately audited public handles to a recall response.

    This is deterministic provenance propagation, not target inference: the
    model already selected an indexed audit event for an explicit historical
    reference, and that event contains only handles released to the customer
    in the prior answer. Runtime fills an omitted evidence list but never
    replaces a non-empty model selection.
    """
    budget = compute_loop_budget(state)
    if budget.reason != "verified_history_recall_ready":
        return calls, None
    trusted = list(dict.fromkeys(
        str(handle)
        for data in verified_history_recall_results(state)
        for handle in list(data.get("result_handles") or [])
        if str(handle)
    ))
    if not trusted:
        return calls, None
    normalized: list[dict[str, Any]] = []
    changed_call_ids: list[str] = []
    for raw in calls:
        call = deepcopy(raw)
        args = dict(call.get("args") or {})
        if (
            str(call.get("name") or "") == "respond_to_user"
            and not [value for value in list(args.get("evidence_handles") or []) if str(value)]
        ):
            args["evidence_handles"] = list(trusted)
            call["args"] = args
            changed_call_ids.append(str(call.get("id") or call.get("tool_call_id") or ""))
        normalized.append(call)
    proof = None
    if changed_call_ids:
        proof = {
            "version": "history-recall-evidence-binding@1",
            "source_tool": "inspect_audit_event",
            "source_trace_handles": [
                str(data.get("trace_handle") or "")
                for data in verified_history_recall_results(state)
                if str(data.get("trace_handle") or "")
            ],
            "evidence_handles": trusted,
            "changed_call_ids": changed_call_ids,
            "value_invention_allowed": False,
        }
    return normalized, proof


def _terminal_runtime_outcome(
    state: dict[str, Any],
    *,
    call: dict[str, Any],
    answer: str,
    evidence_handles: list[str],
) -> dict[str, Any] | None:
    """Create a canonical terminal outcome when no single tool outcome owns it.

    A single observation keeps its domain-owned structured projection.  When a
    terminal answer combines multiple successful observations, selecting the
    last RuntimeOutcome would silently narrow the public response to the last
    item.  Preserve an aggregate query outcome so Presentation can release one
    canonical primary block per verified Goal scope.  History-only terminal
    answers remain narrative because they have no current-turn query trace.
    """
    name = str(call.get("name") or "")
    if name == "ask_user_clarification":
        return outcome(
            "clarification",
            effects="none",
            safe_to_continue=True,
            evidence_handles=evidence_handles,
            customer_safe_summary=answer,
            next_interaction="need_selection",
        ).as_dict()
    if name != "respond_to_user":
        return None

    observations: list[dict[str, Any]] = []
    aggregate_handles = list(evidence_handles)
    effect_ids: list[str] = []
    for row in list(state.get("tool_trace") or []):
        if not isinstance(row, dict) or str(row.get("classification") or "") != "observation":
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        runtime = result.get("runtime_outcome") if isinstance(result.get("runtime_outcome"), dict) else None
        if not bool(result.get("ok")) or runtime is None:
            continue
        observations.append(row)
        effect_id = str(row.get("effect_id") or "")
        if effect_id:
            effect_ids.append(effect_id)
        aggregate_handles.extend(
            str(handle) for handle in list(runtime.get("evidence_handles") or []) if str(handle)
        )
    # Preserve a single current-turn domain outcome so its registered
    # structured presentation remains the public source of truth.  A terminal
    # response that only cites an already-visible result has no current-turn
    # RuntimeOutcome, however.  It still needs a canonical narrative outcome;
    # otherwise the finalizer sees the internal goal-declaration trace and
    # incorrectly replaces the validated answer with a fail-closed message.
    existing = coerce_runtime_outcome(
        state.get("runtime_outcome"),
        correlation_id=str(state.get("correlation_id") or "") or None,
    )
    if (
        len(observations) < 2
        and existing is not None
        and existing.outcome_type != "failure"
    ):
        return None
    if existing is not None and existing.outcome_type == "failure" and not evidence_handles:
        # An unsupported/failed business attempt remains authoritative unless
        # the accepted terminal response is independently grounded in already
        # visible evidence.  This prevents arbitrary prose from converting a
        # real failure into success while allowing a bad read candidate to be
        # superseded by a verified historical answer.
        return None
    aggregation_kind = (
        "multi_observation_terminal_answer"
        if len(observations) >= 2
        else "validated_terminal_answer"
    )
    return outcome(
        "query" if len(observations) >= 2 else "narrative",
        effects="none",
        safe_to_continue=True,
        correlation_id=str(state.get("correlation_id") or "") or None,
        evidence_handles=list(dict.fromkeys(aggregate_handles)),
        customer_safe_summary=answer,
        next_interaction="none",
        payload={
            "aggregation": {
                "kind": aggregation_kind,
                "observation_count": len(observations),
                "effect_ids": list(dict.fromkeys(effect_ids)),
            },
        },
    ).as_dict()


def _canonical_observation_release(state: dict[str, Any]) -> tuple[dict[str, Any], str, list[str]] | None:
    """Return the latest safe, non-effecting observation owned by Runtime.

    This is a fallback only for terminal *wording/protocol* failure.  It never
    treats an action, draft or arbitrary model prose as success.  The public
    projector will render the registered structured contract and independently
    verify its scope.
    """
    for row in reversed(list(state.get("tool_trace") or [])):
        if not isinstance(row, dict) or str(row.get("classification") or "") != "observation":
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        if not bool(result.get("ok")):
            continue
        normalized = coerce_runtime_outcome(
            result.get("runtime_outcome"),
            correlation_id=str(state.get("correlation_id") or "") or None,
        )
        if normalized is None or normalized.effects != "none":
            continue
        runtime = normalized.as_dict()
        handles = list(runtime.get("evidence_handles") or [])
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        for key in ("result_handle", "view_handle", "eligibility_handle", "transaction_handle"):
            if data.get(key):
                handles.append(str(data[key]))
        handles = list(dict.fromkeys(str(handle) for handle in handles if str(handle)))
        runtime["evidence_handles"] = handles
        summary = str(runtime.get("customer_safe_summary") or "").strip()
        if summary:
            return runtime, summary, handles
    return None

def _loop_static_system_prompt() -> str:
    """Return the immutable, cache-friendly part of the agent contract."""
    return """你是可扩展客户服务 Agent，运行在可观察的连续 Agent Loop 中。具体业务领域和可用能力只来自 Composition Root 注册的模块，不由核心层预设。

消息序列中最后一条 HumanMessage 是本轮唯一的 user-text authority。目标 evidence_span 和所有 *_span 只能复制其中的连续字面子串；历史回答、可见结果标签、工具错误中的模型复述和你自己的推断都不能冒充当前原文。

你负责自然语言理解：承接、代词、集合、否定、纠正、打断、多任务和话题切换。程序不会用关键词替你重新解释用户的话。

工作规则：
- 先选择当前最有价值的一次观察、澄清、软任务更新或动作草稿；读取结果后再回答。
- 一句话有多个目标、查询后再查询、查询后再动作、多个动作或长流程时，先按用户可以独立判断是否完成的业务效果拆 Goal；不要按接口或 Tool 数量拆 Goal。筛选、排序、数量、原因、权限检查、政策读取、Draft 与展示步骤都不是独立 Goal，除非用户明确把它们作为可单独验收的业务结果。一个 Goal 可以由多个 Tool 完成，多个 Goal 也可由一个综合 Tool 完成；每个 Goal 用开放 requested_effect 表达，系统没有能力时仍保留原 Goal。
- requested_effect 必须完整填写 domain、operation、object_type。若用户业务效果与“当前部署登记的业务效果身份”中的某个身份精确对应，必须逐字段使用该精确身份；只有不存在精确对应时才保留开放身份。禁止用 query/action 等泛化类别替代已登记的精确业务效果，也禁止为了现有能力做同义词、近似或邻近能力改写。
- 任何业务事实、状态、金额、资格、政策或执行结果必须来自工具观察或 ContextBundle 中的真实 handle；不能编造。
- Goal 是用户业务效果，不是 query/consult/action 分类。goal_type 仅为旧执行合同兼容提示，不得覆盖 requested_effect；咨询、资格预检与产生外部效果的能力不得因名称相近而互相替代。
- 只有用户明确要求产生外部效果时，才可选择模块注册的动作草稿能力；模型不能先承诺“可以办理、已提交或已成功”。
- 集合或多目标动作必须遵守注册能力声明的基数边界。Workflow 只能拆解、暂停、澄清或创建允许的草稿，不能绕过事务主链。
- 结构化 Transaction Interaction 是表单字段的唯一写入通道，Authority Interaction 是确认/取消/提交的唯一通道；聊天文字不能写入表单、不能授权、不能提交，也不能替代办理卡。
- 已有办理状态追问只允许使用模块注册的只读生命周期能力，不能重新创建草稿。
- 每个工具调用都只是候选：Runtime 会校验注册合同、输入结构、目标绑定、依赖与当前作用域，精确匹配后才签发 ExecutionPermit；没有 Permit 不会执行。无匹配能力时不得 nearest-match。
- PRETOOL_EXECUTION_POLICY.shared_frontier_bindings 只表示同一个精确 Capability 当前可能一次覆盖多个 Goal。只有 Tool 确实出现在每个 Goal 的前沿、目标/基数兼容，并且每个 Goal 都能获得独立 MatchProof 与完成证明时，调用参数才可携带多个 goal_ids；否则必须分别执行，不能为了少调用而合并。
- update_task_board 是软组织状态，不是事实；当前用户原话优先于旧任务或历史摘要。
- 涉及业务事实的最终回答必须 respond_to_user 并附真实 evidence_handles；普通问候可为空。回答合并多个已观察对象或分支时，必须覆盖全部对象并附上所有可用的观察证据句柄，不能只引用最后一项。
- 历史审计只在用户明确引用旧回答时调用 inspect_audit_event；其返回不是当前指代或业务事实权威。
- 工具参数中的 reference_span、attribute_span、status_span、各种 *_span 与 constraint_bindings.source_span 必须逐字来自“本轮用户原话”，不能把上一轮结果里的商品名、订单号或模型推断伪装成本轮 span。
- 注册能力含 query 与 constraint_bindings 时，query 中每个非空业务条件都必须有一条对应绑定：source_span 逐字来自本轮原话，parameter_path 指向该 query 字段，normalized_value 与实际条件一致。query 条件只放在该能力的 query 中，不得同时塞入 Target 集合操作来重复表达。
- 用户用“它/其中/那个/这些”或最高级、序号继续引用上一轮已经展示的集合时，只能从 ContextBundle.visible_result_refs 选择作用域匹配的 result_ref：简单单步操作可用 target.mode=collection 或 set_operation(left_handle=result_ref)；金额/时间/文本条件或 filter→sort→take/ordinal 组合必须使用 target.mode=pipeline、source_kind=collection、source_handle=result_ref。每个 pipeline 步骤只能使用 Schema 白名单字段与操作，所有 source_span/value_span/*_span 必须逐字来自本轮原话。不得退化成 entity_match 并复制历史标签；没有唯一可验证 ResultRef 时必须澄清。
- 在统一语义声明阶段，只要本轮明确引用历史可见结果、历史轮次或展示顺序成员，就必须在对应 Goal 填写 reference_expression。模型只提出关系；Runtime 生成 ReferentResolutionProof。只有 UNIQUE 证明可冻结为 resolved_reference；AMBIGUOUS/NOT_FOUND/TYPE_CONFLICT/CARDINALITY_CONFLICT 必须澄清或失败关闭，禁止改选更近、相似或更宽的结果。reference_expression.expected_cardinality 描述被引用的历史对象本身，而 expected_result_cardinality 描述本 Goal 最终业务结果；指向一个对象/成员时前者用 single，指向将继续筛选/排序/比较的历史集合时前者用 collection。业务 Tool 的 target 必须消费该 resolved_reference 的精确 ResultRef 或成员 handle。
- context_binding={reference_kind:explicit_return...} 和 explicit_group_reference 仅是现有领域 Tool Schema 的兼容/审计注解，不是第二个语义 Owner；它必须与 FrozenSemanticContract.resolved_reference 一致，不能覆盖 ReferenceExpression 的解析证明或切换目标。
- visible_result_refs 的 discourse_recency_rank 与 is_latest_visible_turn 只表达客户实际看到结果的对话新近度，不替 Runtime 自动选对象。对于“它/其中/这些/那个”这类没有显式回到旧话题的承接，模型必须沿唯一的 is_latest_visible_turn=true ResultRef 继续；选择 is_latest_visible_turn=false 的旧集合属于范围错误，不能因为旧集合成员更多就自行回退。用户明确按标签回到或纠正一个旧结果时，如果该标签在历史可见结果中只对应一个精确 member_handle，即使这个同一成员同时出现在多个父 ResultRef 下，这些父结果也只是同一成员的展示/证明别名；统一语义声明应使用 reference_expression.reference_type=explicit_visible_member 并填写该精确 member_handle，不得仅为父别名消歧而随意填写 source_result_ref。只有标签实际对应不同 member_handle 时才属于成员歧义并需要澄清。业务执行阶段仍选择与冻结 resolved_reference 一致的旧 ResultRef/成员，并填写 context_binding={reference_kind:explicit_return, source_span:本轮逐字出现的旧结果成员标签片段}；source_span 应复制商品名/订单号等标签片段，不能填普通代词。用户用“刚才两个/前面三个/the previous two”明确把最近连续多个可见结果作为一组时，使用 set_operation 逐步组成该组，并填写 context_binding={reference_kind:explicit_group_reference, source_span:本轮逐字出现的组引用, group_size:明确结果数量}；该绑定只允许从最新结果开始、无跳跃地覆盖最近连续可见结果，不能用“它/其中/这些”伪造。若字面标签在旧集合中只匹配唯一成员，必须从 member_handles 的同位置选择该精确 handle，使用 target.mode=artifact（expected_shape=one），不得把包含兄弟成员的父 collection 当成该成员；Runtime 会校验可见父集合成员关系而不会替你选。只有用户明确引用整个旧集合时才继续使用 collection。没有标签级旧范围或明确连续组证据时必须继续最新范围或澄清。
- 单成员 ResultRef 仍是合法集合；“其中最贵/最便宜/最新”等最高级在该集合上的结果就是唯一成员。可直接用该 ResultRef 回答，不必为了制造比较再读详情；回答只陈述这个集合内的唯一成员，不能枚举旧集合中的其他对象，也不能以“只有一项没有比较意义”为由扩大到旧集合或追问范围。
- 中文等语言常在连续对话中省略主语；本轮未复述“它”不等于重置话题。只要最新公开 ResultRef 的成员唯一、用户没有按可见标签显式切换到别的对象，就应沿该唯一成员继续。ask_user_clarification 必须用 missing_kind 区分缺少 target/scope/condition/intent；目标已经唯一时不得把 missing_kind 写成 target 或 scope。
- 资格预检等 target-bearing ResultRef 的 result_ref 是业务结论证据，member_handles 是该结论对应的经 Runtime 验证业务对象。用户随后省略对象并要求办理时，必须沿这个唯一成员或该单项 result_ref 继续，不能把资格证据伪装成订单，也不能从历史文本复制商品名作为新的 entity_match。
- “最贵/最便宜/最新”等选择后继续查询的多步目标，先对可见集合执行 sort 产生有序 ResultRef，再以 take(limit=1) 引用该结果完成后续读取；不得跳过中间可验证结果直接猜实体。
- 链式选择产生的一项 ResultRef 仍可作为集合证据继续做集合级 filter/sort/compare，此时下游工具用 target.mode=collection、left_handle=该 ResultRef；但若后续用户以单对象方式继续指代它，并且冻结的 reference_expression.expected_cardinality=single、ReferentResolutionProof=UNIQUE 且 resolved_reference.member_handles 恰有一个成员，则单对象业务 Tool 必须消费这个已冻结证明中的成员 handle（通常 target.mode=artifact），不得把父 collection 当成单对象，也不得从未冻结的工具原始输出自行抽取/猜测成员。多成员集合绝不能因为代词而由 Runtime 自动挑一个。
- 同一个用户目标需要组合多个集合分支时，必须先得到各分支 ResultRef，再用 union / intersection / difference 产生唯一的合并 ResultRef，并让最终查询消费该合并结果；不得连续执行多个独立观察后让回答或展示层猜测如何拼接。
- Target 固定判别联合：all_orders 只需 mode（可带筛选 span）；entity_match 需要 attribute_span；artifact/collection 需要 left_handle；set_operation 的 identity 需要 left_handle，difference/union/intersection 还需 right_handle，filter 还需 status+status_span，sort 还需 sort_field+sort_direction+sort_span，take 还需 limit，ordinal 还需 position；pipeline 需要 source_kind=all_orders 或 collection（collection 还必须有 source_handle）以及 1–8 个 steps。Pipeline 仅允许注册的 filter/sort/take/ordinal，禁止 SQL、代码、任意字段和任意表达式。不要混用分支字段。
- 本轮原话直接出现业务对象称呼或业务标识符时，只有在该字面表达语义上是一个新的直接目标、而不是在回到/引用已经向客户可见的历史结果或成员时，才可直接使用 entity_match(attribute_span=该连续原文)；fresh literal target 不要求对象先出现在 visible_result_refs。若当前字面标签在本轮话语语义中是在显式回到/引用历史可见成员，即使标签本身逐字出现在当前原话，也必须先在对应 Goal 声明 reference_expression 并由 Runtime 得到 UNIQUE 证明，后续消费冻结的 ResultRef/member handle；不得用 entity_match 绕过历史引用证明。历史中恰好存在同名字面标签本身不自动证明当前是历史引用，语义关系仍由模型结合公开上下文判断；Runtime 不做关键词或名称自动绑定。
- visible_result_refs 中的 source_operation 与 lineage_result_refs 是集合来源证明：若最新单项由 sort/take/ordinal 从父集合产生，而用户本轮改问相反排序、另一端或重新比较，必须对记录的父集合执行新操作，不能在该单项上再次排序后把同一项冒充另一端。
- 会话已有 visible_result_refs 时，target.mode=all_orders 会重新扩大到全量范围；只有本轮原话明确要求全部/所有订单或明确重置范围时才可使用。普通“其中/它/这些/那个”承接不得用 all_orders 绕过最新 ResultRef，也不得先重新全量查询再把新结果伪装成最新范围。
- 所有 *_span 必须直接复制本轮原话中的完整连续片段；set_operation.sort 必须用 sort_span，pipeline 的 filter/sort/take/ordinal 必须分别提供 Schema 要求的 source_span、value_span、lower_span、upper_span 或 value_spans。结构化金额、日期、天数、数量必须与对应原文数值一致，不能只复制一句话后发明参数。删除中间词、改写、概括或拼接不连续文本都不算字面证据。发现候选 span 不是连续原文时应在预算内修正候选，不能把协议错误当成业务结论。
- 当 ContextBundle 的 context_health.transactions=unavailable 时，不得将 active_transaction_state 为空理解为没有进行中的事务，也不得创建新的业务草稿。
- 不得重复调用相同参数的工具；没有新观察时应回答或澄清。最终完成由 RuntimeOutcome 与 WorkflowStep 验证决定，不由模型自称完成。
- 面向用户回答先结论后必要说明；不描述不确定界面位置，不泄露内部字段。
- Runtime 不会替你判断“它”或“它们”指谁，不会自动换目标、换动作或删除用户条件。若上一次执行要求澄清、解释业务结论、说明不支持或仅解释对账状态，只能遵守该受限模式，不能另选工具继续尝试。"""


def _loop_runtime_prompt(
    state: dict[str, Any],
    *,
    context_bundle_builder: ContextBundleBuilder,
    capability_registry: CapabilityRegistry | None = None,
) -> str:
    # A ContextBundle is a per-model-call projection, never cached semantic
    # authority. Tool observations from the preceding loop must be visible on
    # the very next call without replaying raw Ledger JSON.
    pack = context_bundle_builder.build(state)
    max_steps = int(state.get("agent_loop_max_steps") or _max_loop_steps())
    step = int(state.get("agent_loop_step") or 0)
    final_retry = int(state.get("answer_protocol_retry") or 0)
    budget = compute_loop_budget(state)
    declaration_clarification_mode = _declaration_clarification_required(state)
    terminal_rule = (
        "历史审计已返回上一轮公开回答及其 result_handles；下一步只能 respond_to_user，使用这些真实句柄回答历史召回，不得把历史实体伪装成本轮 span 后重新查询。"
        if budget.reason == "verified_history_recall_ready"
        else "本轮已获得足够的简单观察，下一步只能 respond_to_user 或 ask_user_clarification，不得再调用读取工具。"
        if budget.terminal_only
        else "可继续选择一次必要观察；不要重复同一参数工具。"
    )
    goal_declaration_protocol_repair = _goal_declaration_protocol_repair_rule(state)
    if declaration_clarification_mode:
        protocol_repair_rule = (
            "上一次 declare_turn_goals 已由独立语义验证明确判定需要用户澄清；本次不能再次声明 Goal，"
            "也不能调用任何业务能力。只能调用一次 ask_user_clarification，直接询问缺失的对象、范围、条件或真实意图。"
        )
    elif goal_declaration_protocol_repair is not None:
        protocol_repair_rule = goal_declaration_protocol_repair
    elif str(state.get("status") or "") == "ClarificationNotNeededRetry":
        protocol_repair_rule = (
            "上一次澄清被 Runtime 拒绝：普通承接已有唯一的最新公开范围，不能复活更旧、更宽的集合制造歧义。"
            "本次必须沿唯一 is_latest_visible_turn ResultRef 调用当前业务能力或给出已有证据回答，不得再次澄清。"
        )
    elif str(state.get("status") or "") == "WorkflowIncompleteRetry":
        repair_surface = (
            _discover_capability_surface(state, capability_registry)
            if capability_registry is not None else {}
        )
        pending_goal_ids, completion_tools, _unsupported_tools = (
            _workflow_repair_tools(state, capability_registry, repair_surface)
            if capability_registry is not None else (set(), set(), set())
        )
        pending_effects = [
            str(row.get("requested_effect_identity") or "")
            for row in list(repair_surface.get("goals") or [])
            if isinstance(row, dict) and str(row.get("goal_id") or "") in pending_goal_ids
        ]
        pending_label = "、".join(value for value in pending_effects if value) or "未知"
        completion_label = "、".join(sorted(completion_tools)) or "无；只能澄清或报告能力缺失"
        protocol_repair_rule = (
            f"上一次终止调用因仍有必需目标未覆盖而被拒绝。未覆盖业务效果：{pending_label}；"
            f"本次可精确完成这些目标的业务工具仅为：{completion_label}。若缺少用户选择、对象或条件，调用一次 "
            "ask_user_clarification 暂停并明确追问；若能力面已证明不存在，则调用唯一的不支持报告能力。"
            "respond_to_user 当前没有暴露，重复上一次终止调用属于协议错误。"
        )
    elif final_retry > 0:
        protocol_repair_rule = (
            "上一次模型输出没有完成终止协议，纯文本不会发送给用户。本次必须只调用一个终止工具："
            "需要用户补充对象或条件时调用 ask_user_clarification；已有足够证据可回答时调用 respond_to_user。"
            "不要再次输出纯文本，也不要重复观察。"
        )
    else:
        protocol_repair_rule = "所有面向用户的结论或问题都必须通过 respond_to_user 或 ask_user_clarification 发出，不能只输出纯文本。"
    planning_phase = not goal_plan_ready(state)
    blocker_context = clarification_context_projection(state)
    if declaration_clarification_mode:
        planning_rule = (
            "当前处于语义冻结前的澄清暂停阶段：独立验证已证明当前候选不能安全冻结。"
            "只能向用户提出一个最小必要澄清问题；不得改写或冻结 requested_effect，不得发现、调用或暗示任何业务能力。"
        )
    elif planning_phase:
        planning_rule = (
            "当前处于统一语义声明阶段：只能调用 declare_turn_goals。先完整理解当前原话与公开上下文，再按用户可独立判断完成与否的业务效果拆 Goal；不要按接口、Tool 或现有能力数量拆，也不要把筛选、输入、前置校验、政策读取、Draft 或展示步骤提升为 Goal。每个 Goal 必须给出开放 requested_effect(domain/operation/object_type/raw_description)、字面 evidence_span、对象/输入候选、封闭 condition 和依赖。depends_on 只表示真实结果依赖：只有后一个 Goal 的目标、输入、条件或完成含义必须使用前一个 Goal 的结果才依赖；并列、再/然后/另外、共享业务对象或共享主题只是话语顺序/共同范围，不得据此制造依赖。同一用户原话中前文已明确业务对象或范围、后文真正省略重复对象（零指代）时，应继承这个已明示范围作为省略语义，不得因此依赖前一个 Goal 的执行结果；即使后续执行需要先做一次读取把这个已明示对象解析成稳定 ID/artifact handle，那也是执行支持数据流，不是 Goal 语义依赖。若后一个 Goal 使用显式指代表达（例如它/这个/其中某项）指向本轮前一个 Goal 尚未产生的结果，或条件显式依赖前一个结果，则应声明 depends_on；这种显式结果指代不是普通零指代省略，并且优先于上一条省略规则。显式引用历史结果、历史轮次或展示顺序成员时必须给出 reference_expression，由 Runtime 解析并只接受 UNIQUE 证明。对于没有明确要求回到更早结果的承接式历史引用，由你根据对话语义判断是否承接最近一次客户可见结果；若是，应提出 temporal_visible_result/latest 关系。更早的可见结果仍保留给显式回看，但它们仅仅存在并不会自动让最近结果的承接变成歧义；Runtime 仍不会自动选择目标。系统没有对应能力时仍保留原 Goal且保持原本的独立/依赖关系，后续由 Capability MatchProof 证明缺失，禁止改写成相近能力或因 unsupported 状态附加依赖。goal_type 只在旧能力合同确实需要时作为兼容提示，不是正式语义。"
            + (
                " 当前存在一个或多个 Goal Blocker：只处理本轮明确涉及的 blocker，可同时解决一个 blocker、新建独立 Goal、暂停或替换另一个 Goal。使用 blocker_resolutions/goal_changes 表达具体状态操作；已有 Goal/Focus 的 expected_revision 必须复制 ContextBundle 当前值，evidence_span 必须是本轮原话连续片段；requested_effect 变化必须新建 Goal 并 supersede，不能 PATCH 偷换。不得强迫整轮采用一个全局 disposition；只提交 goal_changes 和 blocker_resolutions。"
                if blocker_context is not None else ""
            )
        )
    else:
        planning_rule = "本轮正式语义已冻结。能力发现和执行只能实现这些 Goal，不能因工具失败或能力缺失改写 requested_effect。每个业务工具调用必须显式绑定一个 goal_id；终止调用必须覆盖全部已处理 Goal。"
    surface = state.get("capability_surface") if isinstance(state.get("capability_surface"), dict) else None
    if capability_registry is not None and not planning_phase and surface is None:
        surface = _discover_capability_surface(state, capability_registry)
    surfaced_tools = set(surface.get("tool_names") or []) if isinstance(surface, dict) else None
    capability_rules = (
        capability_registry.planner_capability_rules(surfaced_tools)
        if capability_registry is not None and not planning_phase
        else ("语义冻结前澄清阶段不暴露业务能力。" if declaration_clarification_mode else "目标声明阶段不暴露业务能力。")
    )
    return f"""【当前规划阶段】
{planning_rule}

【当前协议约束】
{protocol_repair_rule}

【当前 Loop 预算】
同一轮最多 {max_steps} 次模型循环；当前是第 {step + 1} 次。{terminal_rule}（原因：{budget.reason}）

【当前部署登记的业务效果身份】
{capability_effect_index(capability_registry) if capability_registry is not None else {"status": "registry_unavailable"}}
说明：这是模块注册的精确业务效果身份及其语义边界，只帮助模型选择结构化 identity；Runtime 仍只按结构化 identity 精确匹配，不使用自然语言说明、示例、关键词或相似度授予能力。没有精确对应时保留开放 requested_effect。

【当前模块注册的能力规则】
{capability_rules}

【当前能力发现结果】
{surface or {"status": "goal_declaration_required"}}

【Pre-tool 执行策略：当前模型调用的业务能力前沿】
{execution_policy_prompt_projection(state.get("pretool_execution_policy")) if not planning_phase else {"status": "goal_declaration_required"}}

【上一执行路径限制】
{state.get("model_mode_restriction") or ["respond", "observe", "prepare"]}

【本轮正式语义合同】
{state.get("frozen_semantic_contract") or {"status": "not_frozen"}}

【待解决的 Goal Blocker / 兼容澄清投影】
{blocker_context or {"status": "none"}}

【ContextBundle：已验证动态上下文；原始对话已作为 provider messages 发送，不在此重复】
{render_context_bundle(pack, include_conversation=False, include_digest=False)}

【回答协议重试次数】{final_retry}
"""


def _loop_system_prompt(
    state: dict[str, Any],
    *,
    context_bundle_builder: ContextBundleBuilder,
    capability_registry: CapabilityRegistry | None = None,
) -> str:
    """Return the complete prompt for diagnostics and compatibility tests."""
    return "\n\n".join((
        _loop_static_system_prompt(),
        _loop_runtime_prompt(
            state,
            context_bundle_builder=context_bundle_builder,
            capability_registry=capability_registry,
        ),
    ))


def _loop_messages(
    state: dict[str, Any],
    *,
    context_bundle_builder: ContextBundleBuilder,
    capability_registry: CapabilityRegistry | None = None,
) -> list[Any]:
    messages = list(state.get("messages") or [])
    current_text = _latest_human_text(state)
    # Recent raw dialogue is the primary semantic context.  The Bundle gives
    # verified references and observations, but it must not replace the exact
    # wording that lets the model resolve "it", "them", corrections and topic
    # switches.  We never replay prior System messages.
    # Bound semantic history by user exchanges, not raw protocol messages.
    # Tool-heavy turns otherwise evict earlier user/assistant turns after only
    # a few interactions.  Completed history is compacted to exact user text +
    # released final answer; the current exchange keeps its full tool protocol.
    compiled = compile_provider_context(
        messages,
        max_messages=96,
        max_chars=48_000,
        max_exchanges=12,
        compact_completed_history=True,
    )
    raw_window = list(compiled.messages)
    if not raw_window and HumanMessage is not None:
        raw_window = [HumanMessage(content=current_text)]
    if SystemMessage is None:
        return raw_window
    # Keep the immutable contract in the first provider message so DeepSeek can
    # reuse its automatic prefix cache.  Per-call state follows in a separate
    # system message; changing a goal, budget or ContextBundle no longer
    # invalidates the large stable prefix.  Dialogue remains native messages and
    # is not duplicated inside the ContextBundle projection.
    return [
        SystemMessage(content=_loop_static_system_prompt()),
        SystemMessage(content=_loop_runtime_prompt(
            state,
            context_bundle_builder=context_bundle_builder,
            capability_registry=capability_registry,
        )),
        *raw_window,
    ]



def _clarification_terminal_goal_ids(
    workflow_plan: dict[str, Any] | None,
    call: dict[str, Any],
) -> list[str]:
    """Bind a clarification pause without inventing or widening semantics.

    A singleton pending Goal is a deterministic orchestration fact and may be
    bound without asking the model to repeat its ID.  When several required
    Goals remain pending, only an explicit unique subset from the currently
    pending set is accepted.  Invalid/unknown bindings fail closed as empty.
    """
    plan = workflow_plan if isinstance(workflow_plan, dict) else {}
    pending = [
        str(goal.get("goal_id") or "")
        for goal in list(plan.get("goals") or [])
        if isinstance(goal, dict)
        and bool(goal.get("required", True))
        and str(goal.get("goal_id") or "")
        and str(goal.get("coverage_status") or "") in {"PENDING", "BLOCKED"}
    ]
    pending = list(dict.fromkeys(pending))
    pending_set = set(pending)
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    explicit = [str(value) for value in list(args.get("goal_ids") or []) if str(value)]
    if explicit:
        if len(explicit) != len(set(explicit)) or any(value not in pending_set for value in explicit):
            return []
        return explicit
    return pending if len(pending) == 1 else []


def _bind_loop_tools(
    model: Any,
    schemas: list[dict[str, Any]],
    *,
    require_tool_call: bool,
    required_tool_name: str | None = None,
    allow_tool_choice: bool = True,
) -> Any:
    """Bind the current protocol surface and enforce bounded repair calls.

    Prompt text alone is not a protocol guarantee: an OpenAI-compatible model
    can repeatedly emit plain assistant content after being told to use a
    terminal tool.  On the single bounded retry we expose only terminal schemas
    and request one tool call from the provider.  The returned candidate still
    passes goal binding, workflow verification and Answer Release.
    """
    if not hasattr(model, "bind_tools"):
        return model
    if require_tool_call and allow_tool_choice:
        try:
            # A single protocol tool has a stronger contract than generic
            # ``required``. DeepSeek-compatible providers may satisfy
            # ``required`` inconsistently on history-recall turns; naming the
            # only legal function prevents plain prose from consuming the
            # entire Loop before Goal declaration.
            return model.bind_tools(
                schemas,
                tool_choice=required_tool_name or "required",
            )
        except TypeError:
            # Lightweight adapters may implement the older bind_tools(schemas)
            # surface.  The terminal-only schema set still narrows the retry;
            # Runtime never invents a call on the adapter's behalf.
            return model.bind_tools(schemas)
    return model.bind_tools(schemas)



def _build_loop_plan(state: dict[str, Any], user_input: str, calls: list[dict[str, Any]], raw_content: str, *, capability_registry: CapabilityRegistry) -> dict[str, Any]:
    """Build the TurnPlan for the current user turn.

    The model contributes candidate effects only.  The execution runtime later
    attaches a MatchProof and ExecutionPermit to each effect; a candidate is
    never permission to use a nearby capability or write business state.
    """
    prior = state.get("current_turn_plan") if isinstance(state.get("current_turn_plan"), dict) else {}
    plan_id = str(prior.get("plan_id") or f"turn-plan:{create_plan_id()}")
    effects, decorated_calls = build_effects(
        plan_id=plan_id,
        calls=calls,
        capability_registry=capability_registry,
        existing_effects=list(prior.get("effects") or []),
    )
    return {
        **{key: value for key, value in prior.items() if key not in {"tool_calls", "raw_model_content", "loop_step", "iteration_id", "user_text"}},
        "plan_id": plan_id,
        "iteration_id": create_plan_id(),
        "architecture": "customer_agent.runtime",
        "turn": int(state.get("turn_index") or 0),
        "loop_step": int(state.get("agent_loop_step") or 0) + 1,
        "user_text": user_input,
        "tool_calls": decorated_calls,
        "effects": effects,
        "raw_model_content": raw_content,
        "semantic_authority": "model_candidate_only_runtime_verified",
        "not_future_semantic_authority": True,
        "workflow_authority": "orchestration_only_not_business_fact",
        "formal_semantic_contract": deepcopy(state.get("frozen_semantic_contract")) if isinstance(state.get("frozen_semantic_contract"), dict) else None,
    }



def _all_formal_goals_require_action_draft(
    state: dict[str, Any],
    capability_registry: CapabilityRegistry,
) -> bool:
    """Derive a serialized write lane from exact capability contracts only."""
    goals = [row for row in semantic_goals(state) if bool(row.get("required", True))]
    if not goals:
        return False
    surface = _discover_capability_surface(state, capability_registry)
    by_goal = {
        str(row.get("goal_id") or ""): row
        for row in list(surface.get("goals") or [])
        if isinstance(row, dict) and str(row.get("goal_id") or "")
    }
    for goal in goals:
        row = by_goal.get(str(goal.get("goal_id") or ""), {})
        tools = [str(value) for value in list(row.get("completion_tools") or []) if str(value)]
        if not tools:
            return False
        contracts = [capability_registry.contract_for_tool(name) for name in tools]
        if not contracts or any(
            contract is None or str(contract.execution_kind or "") != "action_draft"
            for contract in contracts
        ):
            return False
    return True

def agent_loop_node(
    state: dict[str, Any],
    *,
    context_bundle_builder: ContextBundleBuilder,
    capability_registry: CapabilityRegistry,
    model_resolver=get_model,
) -> dict[str, Any]:
    if bool(state.get("transaction_context_blocked")):
        return {
            "current_final_answer": "当前无法确认此前办理状态，请稍后刷新或在事务中心查看；为避免重复办理，本次不会创建新的业务申请。",
            "phase": "final",
            "status": "TransactionContextUnavailable",
            "decision_chain": _append_decision(state, stage="agent_loop", decision="transaction_context_unavailable_blocked", details={}),
        }
    # A durable pending Draft does not preempt every later chat turn.  Chat
    # may still perform read-only queries (including lifecycle status queries),
    # but it can never write form values or authorize/submit the Draft.  The
    # action-preparation runtime returns an explicit interaction redirect when
    # a new write request conflicts with an existing live interaction.
    user_input = _latest_human_text(state)
    if not user_input:
        return {"current_final_answer": "没有收到用户消息。", "phase": "final", "status": "NeedInput"}
    pending_interaction = interaction_response_contract(state)
    if (
        pending_interaction is not None
        and _all_formal_goals_require_action_draft(state, capability_registry)
    ):
        # The goal declaration has already decided that this is a pure write
        # continuation.  A durable pending card serializes the write lane, so
        # no second model call or nearby/unsupported capability is allowed to
        # reinterpret chat as form input, confirmation, cancellation or a
        # successful stop.  Read-only and mixed goal turns deliberately stay in
        # the normal Loop.
        interaction = dict(pending_interaction.get("interaction") or {})
        summary = "当前已有待办理事项；聊天文字不会修改、不会提交，也不会取消草稿。请在办理卡中补充、确认或取消。"
        return {
            "current_final_answer": None,
            "response_contract": pending_interaction,
            "runtime_outcome": outcome(
                "interaction_redirect",
                effects="none",
                safe_to_continue=True,
                evidence_handles=[str(interaction.get("interaction_id") or "")],
                customer_safe_summary=summary,
                next_interaction="open_form",
                payload={"interaction_id": interaction.get("interaction_id")},
            ).as_dict(),
            "phase": "offer_confirmation",
            "status": "PendingInteractionActionRedirect",
            "decision_chain": _append_decision(
                state,
                stage="agent_loop",
                decision="pending_interaction_action_preempted_model_loop",
                details={"interaction_id": interaction.get("interaction_id")},
            ),
        }
    if int(state.get("agent_loop_step") or 0) >= int(state.get("agent_loop_max_steps") or _max_loop_steps()):
        return {
            "current_final_answer": _loop_budget_fallback(state),
            "phase": "final",
            "status": "LoopBudgetExhausted",
            "decision_chain": _append_decision(state, stage="agent_loop", decision="loop_budget_exhausted", details={"max_steps": state.get("agent_loop_max_steps")}),
        }
    try:
        model = model_resolver()
        planning_phase = not goal_plan_ready(state)
        declaration_clarification_mode = planning_phase and _declaration_clarification_required(state)
        pre_model_budget = compute_loop_budget(state)
        history_recall_ready = (
            not planning_phase
            and pre_model_budget.reason == "verified_history_recall_ready"
        )
        workflow_completion_repair = (
            not planning_phase
            and str(state.get("status") or "") == "WorkflowIncompleteRetry"
        )
        clarification_scope_repair = (
            not planning_phase
            and str(state.get("status") or "") == "ClarificationNotNeededRetry"
        )
        terminal_protocol_repair = (
            not planning_phase and int(state.get("answer_protocol_retry") or 0) > 0
            and not workflow_completion_repair
        )
        capability_surface = None if planning_phase else _discover_capability_surface(
            state,
            capability_registry,
        )
        pretool_shadow_plan = None
        if not planning_phase:
            try:
                # V20.14 shadow evidence is compiled before the model sees or
                # emits a business Tool Call. It is intentionally omitted from
                # model messages and cannot create an ExecutionPermit.
                pretool_shadow_plan = build_pretool_shadow_plan(
                    state=state,
                    capability_registry=capability_registry,
                )
            except Exception as exc:
                pretool_shadow_plan = {
                    "version": "pretool-grounded-shadow-plan@1",
                    "authority": "shadow_only_not_execution_authority",
                    "status": "SHADOW_BUILD_FAILED",
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "generated_before_model_tool_call": True,
                    "observed_model_tool_calls": [],
                    "must_not_dispatch": True,
                    "creates_permit": False,
                    "mutates_semantics": False,
                }
        pretool_execution_policy = None
        surfaced_tools = set(capability_surface.get("tool_names") or []) if capability_surface else None
        if not planning_phase:
            try:
                pretool_execution_policy = build_pretool_execution_policy(
                    state=state,
                    capability_registry=capability_registry,
                    shadow_plan=pretool_shadow_plan,
                )
                surfaced_tools = set(
                    str(value)
                    for value in list(pretool_execution_policy.get("allowed_capability_tools") or [])
                    if str(value)
                )
            except Exception as exc:
                # The policy is an enforcement boundary. A compiler failure must
                # not bypass it by restoring the wider exact-effect surface.
                # Internal/terminal controls remain available through
                # ``agent_loop_schemas`` while all business capabilities fail
                # closed for this model call.
                surfaced_tools = set()
                pretool_execution_policy = {
                    "version": "pretool-execution-policy@1",
                    "authority": "provider_tool_surface_only_not_execution_permit",
                    "mode": "POLICY_BUILD_FAILED_FAIL_CLOSED",
                    "allowed_capability_tools": [],
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "creates_permit": False,
                    "dispatches_tools": False,
                    "mutates_semantics": False,
                    "mutates_business_state": False,
                }
        schemas = (
            [deepcopy(ASK_USER_CLARIFICATION_SCHEMA)]
            if declaration_clarification_mode
            else planning_schemas()
            if planning_phase
            else agent_loop_schemas(
                capability_registry,
                allowed_capability_tools=surfaced_tools,
            )
        )
        if history_recall_ready:
            schemas = [
                schema
                for schema in schemas
                if str((schema.get("function") or {}).get("name") or "") == "respond_to_user"
            ]
        elif workflow_completion_repair:
            pending_goal_ids, completion_tools, unsupported_tools = _workflow_repair_tools(
                state, capability_registry, capability_surface or {}
            )
            clarification_only = _workflow_repair_is_clarification_only(
                state, pending_goal_ids
            )
            # A rejected terminal answer proves the workflow is not complete.
            # Ordinary candidate repair preserves the exact pre-tool frontier so
            # a rejected support/target can be corrected and re-proved. A frozen
            # clarification-only Goal remains strictly clarification-only.
            allowed = _workflow_repair_allowed_tools(
                policy_frontier=surfaced_tools,
                completion_tools=completion_tools,
                unsupported_tools=unsupported_tools,
                clarification_only=clarification_only,
            )
            schemas = [
                schema
                for schema in schemas
                if str((schema.get("function") or {}).get("name") or "") in allowed
            ]
        elif terminal_protocol_repair:
            schemas = [
                schema
                for schema in schemas
                if str((schema.get("function") or {}).get("name") or "")
                in TERMINAL_TOOL_NAMES
            ]
        bound = _bind_loop_tools(
            model,
            schemas,
            require_tool_call=(
                declaration_clarification_mode
                or planning_phase
                or terminal_protocol_repair
                or workflow_completion_repair
                or history_recall_ready
                or clarification_scope_repair
            ),
            required_tool_name=(
                "ask_user_clarification"
                if declaration_clarification_mode
                else "declare_turn_goals" if planning_phase else None
            ),
            allow_tool_choice=bool(get_model_profile().get("tool_choice_supported", True)),
        )
        response, model_call = invoke_model(
            purpose="agent_loop",
            model=bound,
            payload=_loop_messages(
                {
                    **state,
                    "pretool_execution_policy": deepcopy(pretool_execution_policy)
                    if isinstance(pretool_execution_policy, dict)
                    else None,
                },
                context_bundle_builder=context_bundle_builder,
                capability_registry=capability_registry,
            ),
            state=state,
        )
        raw_calls = _tool_calls(response)
        raw_content = str(getattr(response, "content", "") or "").strip()
        if declaration_clarification_mode:
            invalid = [call for call in raw_calls if str(call.get("name") or "") != "ask_user_clarification"]
            next_step = int(state.get("agent_loop_step") or 0) + 1
            ai = _as_ai_message(response, raw_calls)
            debug_call = {
                "node": "agent_loop",
                "model_profile": get_model_profile(),
                "loop_step": next_step,
                "tool_call_count": len(raw_calls),
                "tool_names": [str(call.get("name") or "") for call in raw_calls],
                "workflow_level": None,
                "workflow_status": "declaration_clarification",
                "response_content": raw_content,
                "model_call": model_call,
            }
            if invalid or len(raw_calls) != 1:
                retry = int(state.get("goal_declaration_retry") or 0) + 1
                exhausted = retry >= GOAL_DECLARATION_MAX_RETRIES
                return {
                    "messages": [ai],
                    "agent_loop_step": next_step,
                    "goal_declaration_retry": retry,
                    "phase": "final" if exhausted else "agent_loop",
                    "status": "GoalDeclarationClarificationUnavailable" if exhausted else "GoalDeclarationClarificationProtocolRetry",
                    "current_final_answer": (
                        "当前仍缺少必要信息，系统未执行任何业务操作；请重新说明要查询的对象或范围。"
                        if exhausted else None
                    ),
                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],
                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                    "decision_chain": _append_decision(
                        state,
                        stage="agent_loop",
                        decision="declaration_clarification_protocol_violation",
                        details={"emitted_tools": [str(call.get("name") or "") for call in raw_calls], "retry": retry},
                    ),
                }
            call = raw_calls[0]
            if (scope_conflict := _unnecessary_unique_scope_clarification(state, call)) is not None:
                return {
                    "messages": [ai],
                    "agent_loop_step": next_step,
                    "current_final_answer": _loop_budget_fallback(state),
                    "phase": "final",
                    "status": "UnnecessaryClarificationFallback",
                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],
                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                    "decision_chain": _append_decision(
                        state,
                        stage="agent_loop",
                        decision="declaration_clarification_rejected_for_unique_scope",
                        details=scope_conflict,
                    ),
                }
            answer, error, handles = _answer_from_terminal_tool(state, call)
            if error is not None or answer is None:
                return {
                    "messages": [ai],
                    "agent_loop_step": next_step,
                    "current_final_answer": "当前仍缺少必要信息，系统未执行任何业务操作；请重新说明要查询的对象或范围。",
                    "phase": "final",
                    "status": "GoalDeclarationClarificationUnavailable",
                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],
                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                    "decision_chain": _append_decision(
                        state,
                        stage="agent_loop",
                        decision="declaration_clarification_terminal_rejected",
                        details={"reason": error},
                    ),
                }
            final_ai = AIMessage(
                content=answer,
                additional_kwargs={"context_trust": "safe_nonbusiness", "evidence_handles": handles},
            ) if AIMessage else None
            messages = [ai]
            acknowledgment = _append_terminal_protocol_message(call, code="FINAL_ACCEPTED")
            if acknowledgment is not None:
                messages.append(acknowledgment)
            if final_ai is not None:
                messages.append(final_ai)
            terminal_runtime = _terminal_runtime_outcome(state, call=call, answer=answer, evidence_handles=handles)
            return {
                "messages": messages,
                "agent_loop_step": next_step,
                "current_final_answer": answer,
                "answer_evidence_handles": handles,
                **({"runtime_outcome": terminal_runtime} if terminal_runtime is not None else {}),
                "phase": "final",
                "status": "GeneralFinalAnswer",
                "debug_llm_calls": [*(state.get("debug_llm_calls") or []), debug_call],
                "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                "decision_chain": _append_decision(
                    state,
                    stage="agent_loop",
                    decision="declaration_clarification_accepted",
                    details={"missing_kind": str((call.get("args") or {}).get("missing_kind") or "")},
                ),
            }
        if planning_phase:
            invalid = [call for call in raw_calls if str(call.get("name") or "") != "declare_turn_goals"]
            if invalid or len(raw_calls) != 1:
                next_step = int(state.get("agent_loop_step") or 0) + 1
                retry = int(state.get("goal_declaration_retry") or 0) + 1
                exhausted = retry >= GOAL_DECLARATION_MAX_RETRIES
                return {
                    "messages": [_as_ai_message(response, raw_calls)],
                    "agent_loop_step": next_step,
                    "goal_declaration_retry": retry,
                    "phase": "final" if exhausted else "agent_loop",
                    "status": "GoalDeclarationUnavailable" if exhausted else "GoalDeclarationProtocolRetry",
                    "current_final_answer": (
                        "系统未能建立本轮目标计划，未执行任何业务操作；请重新发送这条问题。"
                        if exhausted else None
                    ),
                    "debug_llm_calls": [*(state.get("debug_llm_calls") or []), {
                        "node": "agent_loop",
                        "model_profile": get_model_profile(),
                        "loop_step": next_step,
                        "tool_call_count": len(raw_calls),
                        "tool_names": [str(call.get("name") or "") for call in raw_calls],
                        "workflow_level": None,
                        "workflow_status": "goal_declaration_protocol_violation",
                        "response_content": raw_content,
                        "model_call": model_call,
                    }],
                    "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
                    "decision_chain": _append_decision(
                        state,
                        stage="agent_loop",
                        decision=(
                            "goal_declaration_protocol_exhausted"
                            if exhausted else "goal_declaration_required_before_tools"
                        ),
                        details={
                            "emitted_tools": [str(call.get("name") or "") for call in raw_calls],
                            "retry": retry,
                            "max_retries": GOAL_DECLARATION_MAX_RETRIES,
                        },
                    ),
                }
        # Provider-side ``tool_choice=required`` only requires *a* function
        # call.  Some OpenAI-compatible providers may still repeat a function
        # from conversation history even when it is absent from the tool list
        # bound for this exact model call.  Never accept such a call: the
        # dynamically narrowed surface is a runtime protocol boundary, not a
        # prompt suggestion.
        exposed_tool_names = {
            str((schema.get("function") or {}).get("name") or "")
            for schema in schemas
            if isinstance(schema, dict)
        }
        unavailable_calls = [
            call for call in raw_calls
            if str(call.get("name") or "") not in exposed_tool_names
        ]
        if unavailable_calls:
            next_step = int(state.get("agent_loop_step") or 0) + 1
            ai = _as_ai_message(response, raw_calls)
            protocol_messages = [
                message
                for message in (
                    _append_terminal_protocol_message(
                        call,
                        code="TOOL_NOT_AVAILABLE_IN_CURRENT_WORKFLOW",
                    )
                    for call in unavailable_calls
                )
                if message is not None
            ]
            return {
                "messages": [ai, *protocol_messages],
                "agent_loop_step": next_step,
                "phase": "agent_loop" if next_step < int(state.get("agent_loop_max_steps") or _max_loop_steps()) else "final",
                "status": "WorkflowIncompleteRetry" if workflow_completion_repair else "ToolSurfaceViolationRetry",
                "decision_chain": _append_decision(
                    state,
                    stage="agent_loop",
                    decision="model_called_unexposed_tool",
                    details={
                        "tools": [str(call.get("name") or "") for call in unavailable_calls],
                        "exposed_tools": sorted(exposed_tool_names),
                    },
                ),
            }
        restriction = [str(value) for value in state.get("model_mode_restriction") or [] if str(value)]
        if restriction:
            allowed_terminal = set(TERMINAL_TOOL_NAMES)
            if not any(mode in {"clarify", "respond", "explain", "handoff", "safe_grounding"} for mode in restriction):
                allowed_terminal = set()
            invalid_calls = [call for call in raw_calls if str(call.get("name") or "") not in allowed_terminal]
            if invalid_calls:
                next_step = int(state.get("agent_loop_step") or 0) + 1
                messages = [message for message in (
                    _append_terminal_protocol_message(call, code="EXECUTION_DISPOSITION_RESTRICTED")
                    for call in invalid_calls
                ) if message is not None]
                return {
                    "messages": messages,
                    "agent_loop_step": next_step,
                    "phase": "agent_loop" if next_step < int(state.get("agent_loop_max_steps") or _max_loop_steps()) else "final",
                    "status": "ExecutionDispositionRestricted",
                    "decision_chain": _append_decision(state, stage="agent_loop", decision="execution_disposition_blocked_replan", details={"restriction": restriction, "tools": [call.get("name") for call in invalid_calls]}),
                }
        budget = compute_loop_budget(state)
        if budget.terminal_only and any(str(call.get("name") or "") not in TERMINAL_TOOL_NAMES for call in raw_calls):
            # Do not execute a second, unnecessary observation after a simple
            # query/consultation already produced enough evidence.  Feed a
            # deterministic protocol error back to the model once so it can
            # produce the required terminal response without hidden retries.
            rejected = [call for call in raw_calls if str(call.get("name") or "") not in TERMINAL_TOOL_NAMES]
            next_step = int(state.get("agent_loop_step") or 0) + 1
            if next_step >= int(state.get("agent_loop_max_steps") or _max_loop_steps()):
                return {
                    "agent_loop_step": next_step,
                    "current_final_answer": _loop_budget_fallback(state),
                    "phase": "final",
                    "status": "LoopBudgetExhausted",
                    "decision_chain": _append_decision(state, stage="agent_loop", decision="simple_observation_budget_exhausted", details={"tools": [call.get("name") for call in rejected], "reason": budget.reason}),
                }
            messages = [
                message for message in (_append_terminal_protocol_message(call, code="LOOP_BUDGET_TERMINAL_ONLY") for call in rejected)
                if message is not None
            ]
            return {
                "messages": messages,
                "agent_loop_step": next_step,
                "phase": "agent_loop",
                "status": "LoopBudgetTerminalOnly",
                "decision_chain": _append_decision(state, stage="agent_loop", decision="simple_observation_budget_blocked_extra_tool", details={"tools": [call.get("name") for call in rejected], "reason": budget.reason}),
            }
        executable, history_recall_binding = _bind_verified_history_recall_evidence(
            state,
            raw_calls,
        )
        shadow_business_calls = [
            call for call in executable
            if str(call.get("name") or "") not in TERMINAL_TOOL_NAMES
        ]
        pretool_shadow_comparison = (
            compare_shadow_plan_to_model_calls(pretool_shadow_plan, shadow_business_calls)
            if pretool_shadow_plan is not None
            else None
        )
        plan = _build_loop_plan(state, user_input, executable, raw_content, capability_registry=capability_registry)
        # The exact capability surface is compiled for this model invocation.
        # Plan validation must see the same immutable snapshot immediately;
        # waiting for the graph state merge would make unsupported reporters
        # appear unbound even though absence was already proven.
        planning_state = {
            **state,
            "capability_surface": deepcopy(capability_surface) if capability_surface is not None else None,
        }
        candidate_workflow_plan = build_workflow_plan(
            state=planning_state,
            turn_plan=plan,
            user_text=user_input,
        )
        frozen_plan_definition, plan_run, workflow_plan = materialize_plan_runtime(
            state=planning_state,
            workflow_plan=candidate_workflow_plan,
        )
        plans = [*(state.get("loop_plans") or []), plan]
        common: dict[str, Any] = {
            "current_user_input": user_input,
            "current_turn_plan": plan,
            "loop_plans": plans,
            "semantic_proposal": deepcopy(state.get("semantic_proposal")) if isinstance(state.get("semantic_proposal"), dict) else None,
            "frozen_semantic_contract": deepcopy(state.get("frozen_semantic_contract")) if isinstance(state.get("frozen_semantic_contract"), dict) else None,
            "frozen_plan_definition": frozen_plan_definition,
            "plan_run": plan_run,
            "grounded_execution_plan": workflow_plan,
            "pretool_shadow_plan": deepcopy(pretool_shadow_plan) if isinstance(pretool_shadow_plan, dict) else None,
            "pretool_execution_policy": deepcopy(pretool_execution_policy) if isinstance(pretool_execution_policy, dict) else None,
            "pretool_shadow_comparisons": [
                *[deepcopy(row) for row in list(state.get("pretool_shadow_comparisons") or []) if isinstance(row, dict)],
                *([deepcopy(pretool_shadow_comparison)] if isinstance(pretool_shadow_comparison, dict) else []),
            ],
            "goal_blockers": [deepcopy(row) for row in list(state.get("goal_blockers") or []) if isinstance(row, dict)],
            "goal_records": [deepcopy(row) for row in list(state.get("goal_records") or []) if isinstance(row, dict)],
            "focus_state": deepcopy(state.get("focus_state")) if isinstance(state.get("focus_state"), dict) else None,
            "capability_surface": deepcopy(capability_surface) if capability_surface is not None else None,
            "agent_loop_step": int(state.get("agent_loop_step") or 0) + 1,
            "debug_llm_calls": [*(state.get("debug_llm_calls") or []), {
                "node": "agent_loop",
                "model_profile": get_model_profile(),
                "loop_step": plan["loop_step"],
                "tool_call_count": len(executable),
                "tool_names": [str(call.get("name") or "") for call in executable],
                "workflow_level": workflow_plan.get("level"),
                "workflow_status": workflow_plan.get("status"),
                "plan_definition_id": frozen_plan_definition.get("plan_definition_id"),
                "plan_run_id": plan_run.get("plan_run_id"),
                "pretool_shadow_status": (pretool_shadow_plan or {}).get("status") if isinstance(pretool_shadow_plan, dict) else None,
                "pretool_execution_policy_mode": (pretool_execution_policy or {}).get("mode") if isinstance(pretool_execution_policy, dict) else None,
                "pretool_allowed_capability_tools": list((pretool_execution_policy or {}).get("allowed_capability_tools") or []) if isinstance(pretool_execution_policy, dict) else [],
                "pretool_shadow_comparison": (pretool_shadow_comparison or {}).get("status") if isinstance(pretool_shadow_comparison, dict) else None,
                "response_content": raw_content,
                "model_call": model_call,
            }],
            "model_call_trace": [*(state.get("model_call_trace") or []), model_call],
            "model_call_budget": {
                "max_calls": int((state.get("model_call_budget") or {}).get("max_calls") or 8),
            },
            **(
                {"history_recall_evidence_binding": history_recall_binding}
                if history_recall_binding is not None else {}
            ),
        }

        terminal = [call for call in executable if str(call.get("name") or "") in TERMINAL_TOOL_NAMES]
        nonterminal = [call for call in executable if str(call.get("name") or "") not in TERMINAL_TOOL_NAMES]
        ai = _as_ai_message(response, raw_calls)

        # A final answer and fresh actions in one response is a protocol issue:
        # run the observations first and make the model see their results.
        if terminal and nonterminal:
            deferred = [*list(state.get("deferred_terminal_calls") or []), *terminal]
            return {
                **common,
                "messages": [ai],
                "current_turn_plan": {**plan, "tool_calls": nonterminal},
                "deferred_terminal_calls": deferred,
                "phase": "loop_execute",
                "status": "TerminalDeferredUntilObservations",
                "decision_chain": _append_decision(state, stage="agent_loop", decision="terminal_deferred_until_tools_observed", details={"terminal": [c.get("name") for c in terminal], "other": [c.get("name") for c in nonterminal]}),
            }

        if len(terminal) == 1 and not nonterminal:
            if (
                str(terminal[0].get("name") or "") == "ask_user_clarification"
                and (scope_conflict := _unnecessary_unique_scope_clarification(state, terminal[0])) is not None
            ):
                retry = int(state.get("clarification_scope_retry") or 0)
                tool_message = _append_terminal_protocol_message(
                    terminal[0],
                    code="CLARIFICATION_NOT_NEEDED_UNIQUE_LATEST_SCOPE",
                )
                if retry < 1:
                    return {
                        **common,
                        "messages": [ai, *([tool_message] if tool_message is not None else [])],
                        "clarification_scope_retry": retry + 1,
                        "phase": "agent_loop",
                        "status": "ClarificationNotNeededRetry",
                        "decision_chain": _append_decision(
                            state,
                            stage="agent_loop",
                            decision="unnecessary_clarification_rejected",
                            details=scope_conflict,
                        ),
                    }
                return {
                    **common,
                    "messages": [ai],
                    "current_final_answer": _loop_budget_fallback(state),
                    "phase": "final",
                    "status": "UnnecessaryClarificationFallback",
                    "decision_chain": _append_decision(
                        state,
                        stage="agent_loop",
                        decision="unnecessary_clarification_retry_exhausted",
                        details=scope_conflict,
                    ),
                }
            if isinstance(frozen_plan_definition, dict) and isinstance(plan_run, dict):
                terminal_args = terminal[0].get("args") if isinstance(terminal[0].get("args"), dict) else {}
                terminal_name = str(terminal[0].get("name") or "")
                terminal_goal_ids = (
                    _clarification_terminal_goal_ids(workflow_plan, terminal[0])
                    if terminal_name == "ask_user_clarification"
                    else [
                        str(value)
                        for value in list(terminal_args.get("goal_ids") or [])
                        if str(value)
                    ]
                )
                normalized_terminal_call = terminal[0]
                if terminal_name == "ask_user_clarification" and terminal_goal_ids:
                    normalized_terminal_call = {
                        **terminal[0],
                        "args": {**terminal_args, "goal_ids": terminal_goal_ids},
                    }
                try:
                    plan_run = record_terminal_goal_outcome(
                        definition=frozen_plan_definition,
                        plan_run=plan_run,
                        goal_ids=terminal_goal_ids,
                        terminal_tool=terminal_name,
                    )
                    workflow_plan = project_plan_runtime(
                        definition=frozen_plan_definition,
                        plan_run=plan_run,
                    )
                    common.update({
                        "plan_run": plan_run,
                        "grounded_execution_plan": workflow_plan,
                                })
                except ValueError:
                    pass
            workflow_verification = verify_workflow_for_final_answer({**state, **common, "grounded_execution_plan": workflow_plan})
            if not workflow_verification.get("ok"):
                retry = int(state.get("answer_protocol_retry") or 0)
                tool_message = _append_terminal_protocol_message(terminal[0], code="WORKFLOW_INCOMPLETE")
                if retry < FINAL_PROTOCOL_MAX_RETRIES:
                    return {
                        **common,
                        "messages": [ai, *([tool_message] if tool_message is not None else [])],
                        "answer_protocol_retry": retry + 1,
                        "phase": "agent_loop",
                        "status": "WorkflowIncompleteRetry",
                        "decision_chain": _append_decision(state, stage="agent_loop", decision="terminal_answer_rejected_for_workflow", details=workflow_verification),
                    }
                return {
                    **common,
                    "messages": [ai],
                    "current_final_answer": _loop_budget_fallback(state),
                    "phase": "final",
                    "status": "WorkflowIncompleteFallback",
                    "decision_chain": _append_decision(state, stage="agent_loop", decision="terminal_answer_workflow_fallback", details=workflow_verification),
                }
            answer, error, handles = _answer_from_terminal_tool(state, terminal[0])
            if error is None and answer is not None:
                final_ai = AIMessage(content=answer, additional_kwargs={"context_trust": "grounded" if handles else "safe_nonbusiness", "evidence_handles": handles}) if AIMessage else None
                messages = [ai]
                acknowledgment = _append_terminal_protocol_message(terminal[0], code="FINAL_ACCEPTED")
                if acknowledgment is not None:
                    messages.append(acknowledgment)
                if final_ai is not None:
                    messages.append(final_ai)
                terminal_runtime = _terminal_runtime_outcome(
                    state,
                    call=terminal[0],
                    answer=answer,
                    evidence_handles=handles,
                )
                clarification_patch: dict[str, Any] = {}
                if str(terminal[0].get("name") or "") == "ask_user_clarification":
                    clarification_state = {
                        **state,
                        **common,
                        "grounded_execution_plan": workflow_plan,
                    }
                    clarification_patch["goal_blockers"] = goal_blockers_for_clarification(
                        state=clarification_state,
                        call=normalized_terminal_call,
                        capability_surface=capability_surface,
                    )
                return {
                    **common,
                    "messages": messages,
                    "current_final_answer": answer,
                    "answer_evidence_handles": handles,
                    **({"runtime_outcome": terminal_runtime} if terminal_runtime is not None else {}),
                    **clarification_patch,
                    "phase": "final",
                    "status": "GroundedFinalAnswer" if handles else "GeneralFinalAnswer",
                    "task_board": complete_tasks_from_terminal(state.get("task_board") or [], call=terminal[0], turn=int(state.get("turn_index") or 0)),
                    "decision_chain": _append_decision(state, stage="agent_loop", decision="terminal_answer_accepted", details={"evidence_handles": handles}),
                }
            retry = int(state.get("answer_protocol_retry") or 0)
            if retry < FINAL_PROTOCOL_MAX_RETRIES:
                tool_message = _append_terminal_protocol_message(terminal[0], code=str(error))
                return {
                    **common,
                    "messages": [ai, *([tool_message] if tool_message is not None else [])],
                    "answer_protocol_retry": retry + 1,
                    "phase": "agent_loop",
                    "status": "FinalAnswerProtocolRetry",
                    "decision_chain": _append_decision(state, stage="agent_loop", decision="terminal_answer_rejected_for_evidence", details={"reason": error}),
                }
            return {
                **common,
                **(
                    {
                        "messages": [ai],
                        "current_final_answer": canonical[1],
                        "answer_evidence_handles": canonical[2],
                        "runtime_outcome": canonical[0],
                        "phase": "final",
                        "status": "CanonicalObservationFinalized",
                        "decision_chain": _append_decision(
                            state,
                            stage="agent_loop",
                            decision="invalid_terminal_replaced_by_canonical_observation",
                            details={"reason": error, "evidence_handles": canonical[2]},
                        ),
                    }
                    if (canonical := _canonical_observation_release(state)) is not None
                    else {
                        "messages": [ai],
                        "current_final_answer": _loop_budget_fallback(state),
                        "phase": "final",
                        "status": "FinalAnswerProtocolFallback",
                        "decision_chain": _append_decision(state, stage="agent_loop", decision="terminal_answer_protocol_fallback", details={"reason": error}),
                    }
                ),
            }

        if executable:
            return {
                **common,
                "messages": [ai],
                "phase": "loop_execute",
                "status": "AgentNextStepPlanned",
                "decision_chain": _append_decision(state, stage="agent_loop", decision="next_step_planned", details={"tool_names": [call.get("name") for call in executable]}),
            }

        # Pure prose cannot be repaired as a terminal-format problem while a
        # declared Goal still lacks a verified completion step.  Doing so
        # would expose only terminal tools on the next call and make the very
        # business capability needed to finish the Goal unavailable.  Reopen
        # only the surfaced completion capabilities under the existing
        # bounded WorkflowIncompleteRetry protocol.
        workflow_verification = verify_workflow_for_final_answer({
            **state,
            **common,
            "grounded_execution_plan": workflow_plan,
        })
        if not workflow_verification.get("ok"):
            return {
                **common,
                "messages": [ai],
                "phase": "agent_loop",
                "status": "WorkflowIncompleteRetry",
                "decision_chain": _append_decision(
                    state,
                    stage="agent_loop",
                    decision="plain_prose_rejected_for_incomplete_workflow",
                    details=workflow_verification,
                ),
            }

        # Models that do not support tool calls can still make a safe reply to
        # small-talk.  After observations, ask once for the terminal protocol;
        # do not treat arbitrary prose as a grounded business answer.
        if raw_content and not state.get("tool_trace"):
            answer = _safe_general_reply(user_input)
            final_ai = AIMessage(content=answer, additional_kwargs={"context_trust": "safe_nonbusiness"}) if AIMessage else None
            return {
                **common,
                "messages": [final_ai] if final_ai is not None else [],
                "current_final_answer": answer,
                "phase": "final",
                "status": "SafeGeneralFallback",
                "decision_chain": _append_decision(state, stage="agent_loop", decision="ungrounded_model_prose_replaced", details={"had_content": True}),
            }
        if state.get("tool_trace") and int(state.get("answer_protocol_retry") or 0) < FINAL_PROTOCOL_MAX_RETRIES:
            return {
                **common,
                "answer_protocol_retry": int(state.get("answer_protocol_retry") or 0) + 1,
                "phase": "agent_loop",
                "status": "FinalAnswerProtocolRetry",
                "decision_chain": _append_decision(state, stage="agent_loop", decision="no_terminal_call_after_observation", details={}),
            }
        canonical = _canonical_observation_release(state)
        if canonical is not None:
            return {
                **common,
                "current_final_answer": canonical[1],
                "answer_evidence_handles": canonical[2],
                "runtime_outcome": canonical[0],
                "phase": "final",
                "status": "CanonicalObservationFinalized",
                "decision_chain": _append_decision(
                    state,
                    stage="agent_loop",
                    decision="missing_terminal_replaced_by_canonical_observation",
                    details={"evidence_handles": canonical[2]},
                ),
            }
        return {
            **common,
            "current_final_answer": _loop_budget_fallback(state),
            "phase": "final",
            "status": "NoToolTerminalFallback",
            "decision_chain": _append_decision(state, stage="agent_loop", decision="no_tool_terminal_fallback", details={}),
        }
    except ModelCallBudgetExceeded as exc:
        safe = "本次请求已达到模型处理预算，系统未继续猜测或重复执行。请根据当前结果继续，或稍后重新发起请求。"
        replay = build_failure_replay(
            state=state, stage="agent_loop", error_type=exc.__class__.__name__, error_message=str(exc)
        )
        return {
            "current_final_answer": safe,
            "phase": "final",
            "status": "ModelCallBudgetExhausted",
            "tool_error": {"type": exc.__class__.__name__, "message": str(exc), "replay": replay},
            "decision_chain": _append_decision(state, stage="agent_loop", decision="model_call_budget_exhausted", details={}),
        }
    except Exception as exc:
        # Keep provider diagnostics in ``tool_error``/trace only.  Customer
        # text must never reveal exception classes, stack details or source
        # rules.
        safe = "当前无法完成本次理解或查询，未创建或提交任何业务申请。请稍后重试。"
        replay = build_failure_replay(
            state=state, stage="agent_loop", error_type=exc.__class__.__name__, error_message=str(exc)
        )
        return {
            "current_final_answer": safe,
            "runtime_outcome": outcome(
                "system_unavailable",
                correlation_id=str(state.get("correlation_id") or "") or None,
                customer_safe_summary=safe,
                next_interaction="retry_later",
                payload={
                    "reason": "model_invocation_failed",
                    "failure_category": (replay.get("error") or {}).get("category"),
                },
            ).as_dict(),
            "phase": "final",
            "status": "LLMUnavailable",
            "tool_error": {"type": exc.__class__.__name__, "message": str(exc), "replay": replay},
        }
