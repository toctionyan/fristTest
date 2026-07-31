# 新抽象替换记录：浏览器会话闭环

## 真实模型浏览器会话验收 Gate

- 新增项：`configured-model-browser-conversation` 与 `strong_context_journey.mjs`。
- 唯一职责：证明当前配置模型通过真实 Agent、Business、Vite 和 Chromium 后，公开连续对话满足非空、实体与意图一致、线程隔离和实时/刷新等价。
- 替换或删除项：替换“单轮网页问候或两轮 API canary 可以代表网页强上下文可用”的验收假设；删除以 HTTP 200、非空 JSON 或 Graph 完成代替客户可见语义正确性的完成条件。
- 为什么不能并入现有 Owner：确定性 `product-browser-journey` 负责高频 UI/事务回归，受保护 lifecycle Gate 负责完整 Graph；真实模型网页上下文同时跨 provider、API 投影和浏览器，只能作为独立证据层，三者互不替代。
- 迁移顺序：先记录真实 Chromium 红基线，再修复公开投影和上下文，最后加入配置模型 Gate 与 Mutation。
- 删除证据：质量策略不再只用确定性问候证明对话产品可用；Mutation 删除该 Gate、非空断言或语义 Oracle 时必须失败。
- 验证：`product-browser-journey`、`configured-model-browser-conversation`、`systemic-operational-counterexamples`。

## 可修复候选协议状态

- 新增项：`agent_core.lifecycle.candidate_repair` 的有限候选协议错误集合。
- 唯一职责：区分“模型候选尚未获得 ExecutionPermit 且可在预算内修正”与用户歧义、业务拒绝和基础设施失败。
- 替换或删除项：替换 schema/span/goal binding 首次失败就进入最终解释模式的路径；成功修正后，旧候选只保留审计并标记为被新 effect 替代。
- 为什么不能并入现有 Owner：错误分类由 lifecycle 共享，ExecutionDisposition 与 WorkflowStep 都必须消费同一有限集合；不能在某个电商能力或提示词里各自维护局部重试。
- 迁移顺序：先加入失败反例，再让 disposition 保持 Loop 开放，最后让 Workflow 将成功修正与旧失败收敛为 SUCCEEDED/SKIPPED。
- 删除证据：不再把 `CAPABILITY_EXACT_MATCH_REQUIRED`、source span 协议错误直接当成业务最终结论。
- 验证：`test_rejected_model_candidate_remains_in_bounded_repair_loop` 与 `test_corrected_candidate_supersedes_retryable_protocol_failure_for_same_goal`。

## 资格判定与只读咨询展示合同

- 新增项：`commerce.eligibility_decision@1` 与 `commerce.advisory@1`。
- 唯一职责：前者展示资格通过/不通过的目标、判定和原因；后者展示只读政策咨询的目标、问题、结论和来源。
- 替换或删除项：替换“资格不通过且无按钮就没有主展示”和“所有订单咨询都固定展示退款/售后动作”的错误抽象。
- 为什么不能并入现有 Owner：`next_actions` 只拥有已验证动作选择，不能同时冒充负资格判定或只读知识结论；新合同仍由既有 e-commerce PresentationRegistry 和 ReleaseGate 管理，没有新增平行 Registry。
- 迁移顺序：先加入正负展示反例，再注册服务端合同和 Web renderer，最后移除咨询适配器中的固定退款/售后映射。
- 删除证据：宽泛的 `consult_order_issue` 已拆成发票、退款、售后、保修四个原子咨询能力；任一公开结果都不能再出现其他知识域或未请求的动作，负资格也不再降级为缺少主展示。
- 验证：presentation Python 套件、前端 Vitest、确定性 Chromium 产品旅程和配置模型强上下文旅程。

已发布集合成员仍由既有 `VisibleResultRef` Owner 管理：父 Result/View 的发布来源证明其成员标签已经过公开展示；后续只能验证模型明确提出的精确成员 handle，不能由 Core 自动选第一个、最近一个或任意对象。未发布父集合、集合外成员、跨线程/用户、过期或 shape 不符仍然拒绝。

`VisibleResultRef` 现在附带 `discourse_recency_rank` 与 `is_latest_visible_turn`，只描述公开结果在对话中的新近层级。它不产生 focus、不自动绑定目标，也不授权执行；模型仍须按用户的承接或显式话题返回提出精确 ResultRef，Runtime 继续校验作用域、TTL、shape 与集合成员关系。这样“其中”默认沿最近公开集合，而“回到之前的蓝牙耳机”仍可显式选旧结果。

## Answer Release 的历史可见证据投影

- 新增项：Answer Release verifier 的 `released_result_ref` runtime evidence 投影。
- 唯一职责：把当前终止答案实际引用、且已经过 actor/thread/TTL/shape 校验的客户可见 `VisibleResultRef` 投影进发布验证证据域；集合同时携带成员 handle/label 与 source turn。
- 替换或删除项：替换“Answer Release 只能看本轮最后一次业务调用，因此一次失败的冗余详情调用会抹掉仍然有效的历史可见证据”的错误假设。
- 为什么不能并入现有 Owner：`VisibleResultRef` 仍拥有历史可见性与作用域验证，Answer Release 只消费验证后的证据，不重新解析上下文或选择对象。
- 安全边界：单元素公开集合足以证明“其中最贵/最便宜”等必然单选，但不能证明集合外标签、扩张查询范围或未发布成员；失败 RuntimeOutcome 在没有已验证 evidence handle 时仍然 fail-closed。
- 验证：`test_answer_release_projects_scoped_visible_singleton_as_runtime_evidence`、真实网页“哪些还在路上 → 其中最贵”回指、刷新历史等价以及 `configured-model-browser-conversation`。

## 无工具终止候选的 bounded protocol repair

- 新增项：Agent Loop 的 terminal-only tool binding 与 `tool_choice=required` 单次修正。
- 唯一职责：模型在目标已声明后只输出正确自然语言、却没有产生正式终止调用时，第一次仍拒绝纯文本；唯一一次重试只暴露 `respond_to_user` / `ask_user_clarification`，并在 provider API 层要求工具调用。
- 替换或删除项：替换“重复提示模型必须调用工具，但每次仍暴露全部能力且允许继续返回纯文本”的非强制重试。
- 安全边界：Runtime 不把纯文本合成工具调用、不自动补 goal IDs、不执行近似能力；模型返回的 terminal candidate 仍经过 schema、goal binding、Workflow 完整性和 Answer Release。旧式 model adapter 不支持 `tool_choice` 时只缩窄 schema，不静默伪造调用。
- 验证：`test_plain_content_protocol_retry_forces_a_bound_terminal_tool_call` 与真实网页“我买过什么 → 可以退货退款吗”的订单范围澄清。

## 目标声明修正的当前原文权威

- 新增项：目标声明错误回执的 `current_user_input` / `repair_contract`，以及每次 Agent Loop system prompt 的“权威当前用户原文”区块。
- 唯一职责：模型把历史标签、旧问题或幻觉文本写进 `evidence_span` 时，明确返回本轮唯一可用的原始用户文本，要求模型重新声明；不让模型根据错误码继续猜原文。
- 替换或删除项：替换只返回 `evidence_not_in_current_turn`、却不告诉模型当前权威文本的空转修正。
- 安全边界：Runtime 只回传已有 `current_user_input`，不改写、不补全、不从历史抽取；语义目标、依赖和 span 仍必须由模型重新提出并经过 Goal Alignment。
- 验证：`test_invalid_goal_declaration_returns_authoritative_current_user_text`、错误 span 负例和配置模型完整浏览器旅程。

## 架构重规划证据血缘

- 新增项：Target 的 `重规划来源证据` / `重规划失败 Gate` 与控制器 `_validate_replan_predecessor`。
- 唯一职责：在旧目标因连续无改善停止后，把后继架构目标密码学绑定到真实旧红 evidence，同时保留后继目标自己的红 baseline 和红到绿 claim。
- 替换或删除项：替换“新目标在说明文本中手工引用旧失败目录即可”的不可审计做法。
- 安全边界：验证 attestation、旧 target identity、失败 verification、`ARCHITECTURE_REPLAN_REQUIRED`、失败 claim、失败 Gate 和 repair-plan Owner；拒绝篡改、普通失败、Gate 不匹配和自引用。
- 验证：`test_replanned_target_requires_attested_stopped_predecessor`、新目标 run summary 的 `replan_predecessor` 字段与完整 Integration evidence。

## 托管 Integration 环境与 Gate 制品边界

- 新增项：`run_managed_quality_integration.py` 与前端 `scripts/build.mjs`。
- 唯一职责：前者拥有一次本地 Integration 所需的临时 pgvector、Agent、Business 进程和精确环境变量；后者把 Gate 构建制品写入控制器声明的 evidence 边界。
- 替换或删除项：替换“要求开发者手工常驻三个服务并复制五个 URL/Token”以及“验证构建直接改写受治理 `frontend/dist`”的运行方式；不替换 protected preproduction 或真实模型认证。
- 为什么不能并入现有 Owner：Quality Controller 仍是只读 Judge，不能负责环境生命周期；既有 `ProductRuntimeHarness` 只拥有一次产品 canary，不能拥有整个多 Gate Judge。托管入口组合这些 Owner，但不复制其服务启动逻辑。
- 迁移顺序：先由控制器向 Gate 注入 evidence/mode/gate identity，再让前端构建消费 evidence 边界，最后由托管入口启动数据库与双服务并调用原始 Integration Controller。
- 删除证据：本地 Integration 不再因未手工设置 `AGENT_TEST_URL`、`BUSINESS_TEST_URL` 和 PostgreSQL URL 而跳过；Gate 结束后 workspace snapshot 不再被构建产物改变；进入 Controller 前删除 deterministic provider identity，配置模型 Gate 仍只能绑定真实 `.env`。
- 验证：`test_shell_gate_receives_controller_owned_evidence_boundary`、`test_managed_integration_owns_environment_without_faking_real_model_gate`、`quality-integration-managed` 和 `controller-workspace-immutability`。
