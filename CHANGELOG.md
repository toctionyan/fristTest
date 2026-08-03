# Changelog

## V20.17 B28 — Repository onboarding authority

- Added fail-closed repository onboarding preflight.
- Locked `integration-diagnostic.yml` to the release toolchain authority.
- Explicitly admitted that workflow under skill-only governance.
- Kept WP-08/WP-09 open and `production_closed=false`.

## V20.17 B27 — Stage-6 host and production-closure readiness

- Added fail-closed real Codex/Claude host preflight with WP-08, clean-main, strict adapter and protected-CI checks.
- Added independent production artifact consumer bound to signed toolchain/run identity, exact hashes and safe ZIP contents.
- Closed stale release test-oracle issue without renaming the workflow to satisfy the test.
- Focused 8/8, full Skill 115/115 and protected release authority 90/90 passed.
- Real hosts, WP-08 and production artifacts remain external blockers; `production_closed=false`.

## V20.17 B26 — WP-08 cross-run resume authority

- Connected the resumable WP-08 runner to explicit prior GitHub workflow artifacts.
- Added fail-closed prior-artifact validation for repository/run/attempt/commit, signed release toolchain evidence, source/workspace fingerprints, symlinks and path escapes.
- Added WP-08-specific protected-main run identity while preserving `release.yml` defaults.
- Pinned `actions/download-artifact` v7 by commit SHA without widening unrelated workflow allowlists.
- Added success, retry, wrong-run, source-drift, symlink, tampered-toolchain and wrong-workflow counterexamples.
- Cross-run repair is `CLOSED_VERIFIED`; WP-08 and production remain environment/repository blocked.

# V20.17 B25 — WP-08 resumable certification orchestration

- Added `wp08-resumable-certification@1`: bounded component timeouts, process-group termination, atomic checkpoints and same-source resume.
- Added protected, SHA-pinned `wp08-full-stack-certification` workflow with four required batches and always-uploaded evidence.
- Added deterministic workflow/config contract and negative tests for mutable Actions, unbounded timeouts, stale resume and forbidden production-close claims.
- Verified 11 focused/counterexample tests, 97 Skill tests and 51 production-authority/supply-chain regressions.
- Current container records all four batches as `BLOCKED_BY_ENVIRONMENT`; WP-08 remains open and `production_closed=false`.

# V20.17 B24 — Stage-5 quality toolchain authority closure

- Bound quality CI to `release-toolchain-lock.json`: Ubuntu 24.04, Python 3.12.13, Node 24.18.0, npm 11.16.0, uv 0.11.29, pinned Action SHAs and pgvector digest.
- Added `quality-toolchain-contract@1` with static and runtime fail-closed validation before project dependency synchronization.
- Added positive and negative controls: 5 focused tests and 31 supply-chain/protected-environment regression tests passed.
- Current container is accurately rejected as `BLOCKED_BY_ENVIRONMENT`; WP-08 and `production_closed=false` remain unchanged.

# V20.17 B23 — Stage-5 non-environment regression closure

- Closed two runtime authority defects and migrated stale B20/B22 test oracles under ChangePermit.
- Focused 19/19, ResultRef 24/24, changed-file non-environment 123/123, Business 38/38.
- Standard Quick remains `BLOCKED_BY_ENVIRONMENT`; WP-08 and `production_closed=false` are unchanged.

# V20.17 B22 / Skill 6.7.0 — Transaction, Multi-Draft and Identity Security Closure

- WP-06：新增 canonical `focused_draft_id`，`active_draft_id` 仅保留兼容投影；多个持久 Draft、焦点选择、终态迁移、过期卡片和恢复行为形成独立合同。
- WP-07：Actor、Subject、Resource、Role、Tenant 与 Expected Version 在认证、传输和 Business Service 中独立验证；领域所有权仍由 Business Service 最终裁决。
- 受治理非环境回归：Agent 63 passed（1 PostgreSQL integration deselected），Business Service 38 passed，compileall PASS，独立 DiffReview PASS。
- 标准 product-quality-quick 因缺少 LangGraph/前端依赖保持 `BLOCKED_BY_ENVIRONMENT`，真实 PostgreSQL、模型、RAG、浏览器与生产认证进入 WP-08；`production_closed=false`。

# V20.17 B21 / Skill 6.7.0 — Context, Multi-Goal and Exact Capability Closure

- 完成 STAGE-3 / WP-04 / WP-05：新增只读 ReferentSet，不允许历史集合投影自动成为目标。
- 同轮多结果后的单数续问 fail closed；显式返回和显式分组验证原文字面证据、成员数与连续性。
- Task 依赖合并 Goal 依赖与执行步骤依赖，修复查询→写操作在多 Goal 中丢失顺序的问题。
- Capability 只按结构化 requested effect 精确匹配；缺失能力明确 unsupported，550 个锁定/holdout 案例均未使用相似能力替代。
- 定向测试 620 passed、1 deselected；Campaign 50+100+200+200 = 550/550 PASS，`real_model_claimed=false`。
- 第一次独立 DiffReview 正确拒绝错误的测试资产目录；恢复基线、修订方案、重新签发 Permit 后第二次 PASS。
- 未触碰回归在 B20 与候选中保持相同的 12 个依赖型收集错误，候选新增错误为 0；这些错误和真实模型/全栈认证继续归 WP-08。
- WP-04/WP-05 ClosureMatrix 为 `CLOSED_VERIFIED`，任务总账推进到 STAGE-4；产品版本保持 20.6.1，`production_closed=false`。

# V20.17 B20 / Skill 6.6.0 — Semantic Authority Cutover and Legacy Runtime Exit

- 完成 STAGE-2 / WP-03：State Schema v2 正式对象成为新 Turn 唯一语义权威。
- Runtime、Capability verifier、Clarification、Tool execution 与 Answer release 不再读取或写入 `turn_goal_plan`、`workflow_plan`、`pending_clarification`。
- 退休字段只保留在 `lifecycle/state_schema.py` 的一次性 checkpoint 迁移器中；旧 checkpoint 可迁移，非法状态 fail closed。
- 删除模糊旧能力修复和静默 legacy fallback；工具与目标必须绑定精确 `requested_effect`。
- 正常测试夹具迁移到正式 Frozen Semantic Contract 与 Plan Definition/Run；迁移/负例测试继续保留旧状态构造。
- 依赖较轻的最终定向集 `81 passed, 10 deselected`，兼容投影 `4 passed`；缺少 `langchain_core/langgraph` 的锁定全栈测试明确归属 WP-08，未冒充通过。
- 独立 DiffReview 前两次分别拒绝漏列文件和测试完整性下降；恢复基线、重新审批、重放后第三次 PASS，拒绝证据未覆盖。
- WP-03 ClosureMatrix 为 `CLOSED_VERIFIED`，任务总账推进到 STAGE-3；产品版本保持 20.6.1，`production_closed=false`。

# V20.17 B19 / Skill 6.5.0 — Authoritative Task Ledger and Modular Quality Controller

- 新增机器校验的 `governance/task-ledger.json`：固定 6 个阶段、9 个必做工作包、依赖、Owner、阻塞项、Known Issues 与 Scope Decisions。
- 新增 `task-ledger-validate` / `task-ledger-status`，拒绝重复 ID、依赖环、无证据关单、必做项延期和无 Decision Record 的取消。
- `scripts/quality_loop.py` 从 3181 行缩减为 978 行兼容入口；职责拆分为 `scripts/quality_control/` 下 8 个 focused modules。
- `migration` Change Contract 现在在 `contract-begin` 前强制校验三方案架构决策，避免实现和测试结束后才发现缺少决策记录。
- 保留 `repair_loop.py` 和历史私有 helper 的导入兼容；新增模块边界测试，禁止把原实现重复留在入口形成双权威。
- Trusted Judge 覆盖全部提取的质量裁判模块；修改任一内部模块都会使候选指纹失败。
- 记录既存 `ISSUE-REL-001`（发布 Workflow 测试步骤名漂移）和 `ISSUE-ENV-001`（锁定运行环境/凭证缺失），未跨范围偷修。
- 客服语义、Capability、事务、业务服务、前端、RAG 和业务合同未修改。

# V20.17 B18 / Skill 6.4.0 — Governed Repair Closure

- 新增 `FailureCase -> RootCauseProof -> RepairPlan -> PlanReview -> ChangePermit -> DiffReview -> ClosureMatrix` 强制证据链。
- `contract-begin`、`contract-verify`、`contract-close` 现在分别验证修改许可、当前 Diff 和闭环证据；缺失或陈旧记录会明确失败。
- PreToolUse Hook 只允许 `implementing` 状态的唯一写入者，并同时执行 Change Contract 与 ChangePermit 双重路径授权。
- Stop Hook 对可写 transition 重新验证当前治理链，防止修改后未审查或证据失效仍宣布完成。
- 新增只读 Failure Explorer、Repair Plan Reviewer、Diff Integrity Reviewer、Closure Arbiter，并同步到当前环境、Codex 和 Claude Code。
- Diff Review 从 baseline manifest 重算真实 changed paths，拒绝越权、测试删除/弱化、skip 增加、未批准 Mock、禁止模式和无实际候选变更。
- Closure Matrix 强制八类证据；最大循环耗尽和环境阻塞不能映射为 `CONVERGED`。
- 新增定向、反例和负路径测试；客服语义、Prompt、Capability、事务、业务服务、数据库、RAG 和前端行为均未修改。
- B17i 的真实受保护生产执行仍保持环境阻塞，B18 不把代码治理测试冒充生产认证。

# V20.17 B17i — Production execution handoff

- `release-admission` now atomically persists sanitized PASS/FAIL/BLOCKED results and always uploads a run-bound Artifact from the secret-free Job.
- The supply-chain contract locks the admission result path, Artifact name, no-secret boundary and missing-file failure behavior.
- Added the final GitHub repository, protected Environment, secret, dispatch, Artifact and `production_closed` Runbook.
- No customer-agent semantic, prompt, capability, transaction or business behavior changed.
- Real protected production certification remains environment-blocked because no GitHub App installation/repository, Docker or production secrets are available here.

# Changelog

## V20.17 B17h — Protected Environment Preflight

- 新增标准库 `protected-environment-preflight@1`，在昂贵依赖安装和任何模型调用之前验证受保护生产 Environment。
- 预检验证 protected `main`、Commit/Run identity、精确 Python 3.12.13、Node 24.18.0、npm 11.16.0、Docker/Git、官方模型端点、Provider/Model 一致性、Embedding HTTPS 端点与维度。
- 聊天密钥、Embedding 密钥和至少 32 字节的 Evidence 签名密钥必须存在且不得包含测试/占位标记。
- 失败 JSON 不输出密钥值，只记录脱敏状态和原因；`if: always()` 的证据上传包含 `protected-environment-preflight.json`。
- 供应链静态合同锁定预检脚本、调用、执行顺序和 Artifact 路径，拒绝把预检移动到 `uv sync`/`npm ci` 之后。
- 当前 GitHub App 没有可访问仓库，本地也缺少精确锁定运行时、Docker 和生产 Secrets；真实生产认证与 `production_closed` 仍未执行。
- 客服语义、Prompt、Capability、事务协议、业务规则、数据库、模型路由和 RAG 行为保持不变。

## V20.17 B17g — Production Execution Readiness

- 新增无密钥 `release-admission` Job；非法 event、workflow、受保护 Ref、provider、model 或 Embedding 参数会显式失败。
- `protected-release` 必须依赖 admission 成功，同时保留 GitHub 平台级保护条件，避免唯一 Job 被静默跳过后形成绿色 Workflow。
- 新增 `release-workflow-admission@1` 标准库合同及错误分支、未保护分支、空模型和非法维度反例。
- Admission Job 不绑定 production Environment、不读取 Secrets；正式 Job 仍由 Environment、平台条件和 B17f Run Identity 三重约束。
- Admission 脚本、Workflow 依赖边和调用入口进入发布工具链静态锁与指纹。
- 修正 `PHASE_CANDIDATE_NOTICE.md` 仍描述 B17e、README 仍宣称只修改 Skill 的过期元数据。
- 当前环境无法取得锁定 uv/Python 运行时、Docker 与真实凭证，完整 Quick 和 `production_closed` 仍保持未关闭。
- 客服语义、Prompt、Capability、事务协议、业务规则、数据库、模型路由和 RAG 行为保持不变。

## V20.17 B17f — Protected CI Run Identity and Replay Authority

- 新增 `release-run-identity@1`，将 repository、repository ID、Workflow、Workflow SHA、受保护 Ref、Commit、Run ID、Run Number、Run Attempt 和 Job 规范化为唯一运行身份指纹。
- 正式 Workflow 只允许 `workflow_dispatch + protected main`，checkout 固定当前 SHA、清理工作树、浅克隆并关闭凭证持久化。
- 运行时验证 HEAD 等于 `GITHUB_SHA`、工作树干净、origin 与 GitHub repository 一致，且不存在 checkout 写入的 `http.*.extraheader`。
- CI 运行身份进入工具链指纹，并贯穿生产认证、Quality Loop Summary、Clean Release provenance 与最终关单台账。
- 正式 Artifact 名称包含 Run ID 与 Run Attempt；上一轮 Attempt、另一 Run、另一 Commit 或缺失身份的证据均失败关闭。
- 候选包仍可在非 CI 环境构建，但只有 protected-release 才强制完整运行身份，避免把本地候选误判成正式生产包。
- 客服 Agent 语义、Prompt、Capability、事务协议、数据库和业务规则保持不变。

## V20.17 B17e — Release Supply-Chain Authority

- 正式发布 Workflow 的 checkout、setup-python、setup-node 与 upload-artifact 全部固定到完整提交 SHA，并保留精确版本注释。
- Python 3.12.13、Node 24.18.0、npm 11.16.0、uv 0.11.29 固定；uv 通过 PyPI wheel SHA256 和 `--require-hashes` 安装。
- 新增 `release-toolchain-lock@1` 与 `release-toolchain-provenance@1`，绑定锁文件、安装环境树、可执行文件、Docker 客户端/服务端及前端依赖树。
- `upload-artifact` 显式启用隐藏文件，防止 `.quality` Target/Claims 在绿色 CI 中被默认漏传。
- pgvector 从可变 `pg16` 标签改为不可变 manifest digest；PostgreSQL 与浏览器认证必须报告相同镜像引用和实际容器镜像 ID。
- 生产组件、Quality Loop 维度、发布 Summary 与最终关单台账必须携带同一 `toolchain_fingerprint_sha256`，并在质量执行后和打包后再次校验。
- 可变 Action、未锁 uv、跨工具链、跨容器镜像及运行时篡改均作为反例失败关闭。
- 客服 Agent 语义、Prompt、Capability、事务协议和业务规则保持不变。

## V20.17 B17d — Protected Browser Runtime Authority

- 修复 B17c 浏览器旅程仍使用 local/SQLite/dev_token/未签名 Actor/local_sparse RAG，却与独立 PostgreSQL PASS 拼接成生产认证的假绿。
- 两个配置模型浏览器旅程现在由同一个控制器拥有同一临时 PostgreSQL，Agent、Checkpoint、Business、RAG 和 Document Job 共用同一数据库权威。
- 受保护运行时统一使用 `preprod + JWT + 关闭开发登录 + 签名 Actor + strict state/persistence + model verifiers`。
- 前端 E2E 在页面初始化前注入短期 JWT，不再依赖开发登录按钮；认证 Secret 不进入 evidence。
- 数据库迁移、Business 临时数据和 pgvector RAG 临时数据改为显式管理命令，服务启动不再隐式播种。
- 聊天模型与 Embedding 配置分别预检；外部认证、配额、限流、超时和连接故障保持为 `BLOCKED_BY_ENVIRONMENT`。
- 修正 Skill 架构债务单元测试仍期待旧 `PASS_WITH_DEBT/UNCHANGED` 的过期断言，使其与当前 `PASS/RESOLVED` 权威一致。
- 客服 Agent 语义、Prompt、Capability、事务和业务规则保持不变。

## V20.17 B17c — Production Release Control Closure

- 修复正式 CI 永远无法关单的字段错配：生产维度读取 `contract=production-certification-dimension@1`，Bundle 合同从 real-model 维度读取。
- 接受 CI 实际完成状态 `CI_VERIFIED`，继续接受本地目标 Loop 的 `CONVERGED`，拒绝虚构 `CLOSED`。
- 发布预检脱离 Agent 全量 LangChain/LangGraph import，可在依赖损坏时先输出结构化环境诊断。
- 新增 workspace 外部控制结果，预检、质量、Summary、打包和产物验证失败均留下不含密钥的阶段记录。
- 生产 Summary 的两个模型身份必须与预检 provider/endpoint/model/credential fingerprint 完全一致。
- protected artifact 必须是精确三文件集合，并验证 source ZIP SHA256 sidecar；workflow 修正 claims 上传路径。
- 客服 Agent 业务逻辑、Prompt、事务、数据库和 B17a 生产认证组件保持不变。

## V20.17 B14e — Compatibility Exit Boundary

- 删除生产 Runtime 中直接修改 `grounded_execution_plan` 的兼容 API，旧测试变换迁入测试专用辅助层。
- preprod 诊断统一通过 Kernel Plan authority 读取正式 Definition/Run 投影，不再信任退休 `workflow_plan`。
- 修复终端澄清 blocker 使用旧 Plan 权威覆盖本轮更新结果的问题。
- 删除零调用 singleton clarification 兼容投影；B14e 红基线声明由 `FAILED` 转为 `VERIFIED`。
- 标准 Python 回归 708 项通过，架构保持 `PASS / RESOLVED / 0 cycles`；完整 Quick 仍由前端锁定依赖缺失阻断。


## Skill 6.3.0 — Architecture Truth and Dependency Debt Ratchet

- 修复 Architecture Gate 只阻断 Composition 环、却允许其他大规模 Core 强连通分量显示为全绿的问题。
- 新增依赖债务棘轮：已登记环允许保持或缩小，新增、扩大、合并和未登记环直接失败。
- 架构结果新增 `architecture_status` 与 `architecture_debt_status`，当前技术债明确显示为 `PASS_WITH_DEBT`。
- Quality Loop Evidence 新增功能、架构和真实模型认证三个独立维度；确定性测试不再隐含真实模型已认证。
- 新增控制平面单元测试，覆盖依赖环保持、缩小、清零、新增和扩大五类反例。
- 客服 Agent、Business Service、前端和共享业务合同保持不变。

## Skill 6.2.0 — Stable Architecture Governance Closure

- 将通用 Skill 与 Project Architecture Baseline 明确分离；目录、必需文件和当前 Owner 不再冒充跨项目硬不变量。
- 新增 `PROJECT_BASELINE` 规则等级、Architecture Migration Delta Schema、有效策略解析器和 Baseline Promotion Record。
- Architecture Gate 现在读取“当前 Baseline + Change Contract 绑定的 approved Delta”；Variance 从说明文档升级为可执行治理输入。
- Migration Delta 仅能修改白名单内的项目形状字段，不能修改业务权威、证据、Judge、配置事实和禁止相似能力替代等硬边界。
- Architecture Decision 增加职责选择、硬不变量、Cutover、Acceptance Claims 和 Baseline changes。
- 客服领域规则改为行为合同：开放语义唯一 Owner、Goal 不迁就能力、语义 Goal 与 Tool 步骤分离、局部能力计划、固定 Adapter 构造技术参数。
- 对话回归和状态机合同移除对 `TurnGoalPlan`、`WorkflowPlan`、`pending_clarification` 与三分类协议的通用强制，改为验证语义覆盖、事实证明、Goal 生命周期、能力落地和事务结果。
- 新增 Policy Delta 应用、错误操作拒绝、活动迁移生效和 Baseline Promotion 单元测试。
- 产品源码保持不变。

## Skill 6.1.0 — Portable Product Code Governance

- 新增产品 diagnosis/design/oracle-review/repair/migration/revert/certification Profile。
- 新增根目录 `skillctl.py`，当前环境、Codex 与 Claude Code 统一调用同一个合同、Profile、原产品 Quality Loop 和 Judge。
- 产品 Repair/Migration/Revert 必须绑定 Quality Target/Claim 并先建立红基线；只读 Target 不能写产品代码。
- 产品写入范围必须精确到模块、包、测试目录或文件，拒绝 `services/**` 等根级通配符。
- 新增 `product-implementer` 唯一写入角色和产品级 Pre/Post/Stop 保护。
- 原 `scripts/quality_loop.py` 保留产品 Gate 权威，通过 `product_quality_bridge.py` 接入新 Change Contract，避免两套竞争裁判。
- `product-repair-loop` 现在可调用受限外部 Fixer 与外置 Trusted Judge，并把最终 `CONVERGED` Evidence 自动写回产品合同。
- 合同关闭阶段复用并重新校验已绑定当前源码的产品 Evidence，避免对已收敛 Target 重复执行造成假失败。
- 新增产品 Host/Portable Conformance、Product Contract、Scope 与 Security Profile。
- 修复点目录归一化缺陷，`.quality/**`、`.agents/**`、`.claude/**`、`.codex/**` 不会被错误去掉前导点。
- Skill release 现在同时包含架构收敛和 Evidence Contract 验证。
- 客服 Agent、Business Service、前端与共享业务合同保持逐字节不变。

## Skill 6.0.0 — Skill Engineering Control Plane

- 将通用代码优化治理从单一客服 Agent Skill 中拆出，新增 `skill-system/` 共享控制平面。
- 规则分为 HARD_INVARIANT、STRONG_DEFAULT、REFERENCE_PATTERN、WORKFLOW_DEFAULT 和 EXAMPLE_ONLY；新增 Architecture Variance。
- 新增 diagnosis、design、oracle-review、repair、migration、revert、certification 七种 Target，允许 NO_CODE_CHANGE_REQUIRED 与 REVERT_RECOMMENDED。
- 新增 `AGENTS.md`、`CLAUDE.md`、Codex `.agents/skills`、Claude `.claude/skills`、只读审查 Agent 与宿主 Hook。
- Skill-only Change Contract 默认禁止修改 `services/**`、`web/**` 和 `contracts/**`。
- Repair Loop 改为 Fixer 环境白名单，支持外置可信 Judge，定向重跑全部独立失败根。
- 修正进展判断：上游修复后新暴露的下游失败计为推进；无关失败替换仍判定为停滞。
- 修正 Issue 状态：未重跑、上游阻断和环境阻断不再自动标记 RESOLVED。
- 增加 Skill 静态、单元、宿主、安全、产品兼容和发布 Profile。
- 客服 Agent 产品生产代码保持不变。

## Unreleased — 可操作产品与真实模型闭环

- Quick 新增隔离双服务完整生命周期与真实 Chromium 桌面/390px 产品旅程；浏览器实际完成 Draft 输入、Authority 和 Receipt 可见性。
- release 新增真实 provider 两轮依赖查询 canary，必须穿过目标声明、完整 Graph、Business Observation、链式 ResultRef 与结构化 Answer Release。
- ResultRef 生命周期区分“跨轮已发布”与“同轮已许可成功观察”，既支持 sort→take→下游读取，又拒绝注入 Ledger 或未展示旧引用。
- Target Schema 升级为 mode/operator 判别联合，Runtime JSON Schema 校验补齐 oneOf/const/边界/数组基数。
- Planner、Verifier、Support 模型调用预算隔离，修复业务完成后 Answer Release 被预算饿死的问题。
- requirement Inventory/Profile/Claims 纳入上述系统不变量，并补齐 Quick→Integration→Product→Release 的累积 claim 覆盖。


## 20.6.1 — 项目级可运行闭环

- 新增独立 project requirement catalog，以及 Quick、Integration、Release 三个精确覆盖 profile。
- CI 拒绝把 repair transition claim 包装成 current-pass，并由 Judge 复验 source manifest fingerprint 与 claim 内容。
- repair orchestrator 在 fixer 前验证 target/policy/baseline，保护证据输入，定向 PASS 后才运行完整 Judge。
- Integration 同时执行 Agent 与 Business 测试；模块 execution selector 升级为逐能力真实 adapter 路由。
- Doctor 精确检查两个 uv lock 环境和前端 lock 身份；protected model smoke 校验精确响应、Goal 唯一性和生产 Goal 合同。
- Core 移除对 Composition Root 的反向 import，架构 Gate 增加 AST 依赖图与全 Core SCC 诊断。

## 20.6.1 — 可恢复认证与环境阻断语义

- 质量 Loop 采用 claim 直接证据、自动模式推导和可恢复阶段检查点。
- 修复环境阻断的依赖链传播，并让真实 FAIL 优先于同时出现的环境 BLOCKED。
- protected 认证在 PostgreSQL、双服务或真实模型环境缺失时只能返回 `BLOCKED_BY_ENVIRONMENT`。

## 20.6.1 — Claim 驱动的可认证质量 Loop

- 每条验收要求进入机器可读 claim manifest；控制器自动推导最高质量模式，低模式和定向回归不能关闭高模式声明。
- claim 的测试选择器必须在源码中存在，并且在本次运行的 JUnit 中真实执行通过；仅引用文件或历史日志不能形成 P1 证明。
- 本地 repair target 与不可变提交上的 protected certification target 分离；quick evidence 不能包装成 protected release。
- 删除 Business 通用查询中的 SQLite `PRAGMA` 依赖，资源可过滤字段由领域合同声明；异步 checkpoint 在没有原子存储事务时 fail-closed。
- protected artifact 强制 release evidence、CI commit/run identity 与 clean `npm ci` 构建。


## 20.5.0 — 可信质量证据与生产完整性

- 复审闭环：源码快照与 clean-release 共用锚定路径合同，`src/.../runtime`
  与 `tests/runtime` 纳入身份；旧 round-02 evidence 作废。
- protected release 实际启动 preprod Agent/Business，Agent、checkpoint、Business、
  RAG 和文档队列均使用 PostgreSQL，并开启 JWT、actor 签名、strict state 与 model verifier。
- checkpoint owner/token/expiry 守卫与 mutation 进入同一 PostgreSQL 事务；陈旧 token
  的物理写入被数据库拒绝。
- 文档 job 入队即固定 doc_id，所有 provider 重试幂等 upsert；release 内嵌 provenance
  并绑定确定性 evidence bundle、attestation、commit 与 workflow run identity。
- 修复 fresh PostgreSQL migration 重复列、strict Graph 节点合同缺项、SQLAlchemy
  Draft/Receipt JSON 序列化等 protected 链真实运行阻断。
- Skill 升级为 V5.10.0：定向 Gate 只能形成 `TARGETED_REGRESSION_PASSED` 诊断证据，任何最终收敛必须重新执行最低质量模式全部 required Gate。
- evidence 绑定源码快照、Gate 合同和逐文件哈希，并使用本地/CI 信任密钥签名；篡改、源码漂移或跨策略复用均阻断。
- Agent 与 Business Service 统一以 `APP_PROFILE` 选择运行边界；preprod/production 强制三个独立 verifier、actor signature 和强密钥。
- 会话锁增加 heartbeat、单调 fencing token 与持久化前所有权校验；过期 Worker 不得继续推进 checkpoint 或事务状态。
- 文档索引任务增加共享数据库后端、lease、worker/token、attempt、超时回收和陈旧完成拒绝。
- 新增白名单 clean-release builder 与完整性 verifier：重建前端、生成精确文件清单和 SHA256、绑定全量质量 evidence，并对最终 ZIP 解包复验。
- 新增对应反例测试，Skill、配置模板、运行代码、迁移、CI 与发布文档同步收敛。

## 20.4.0 — 显式目标绑定与真实修复 Loop

- Skill 升级为 V5.9.0：目标声明阶段不再猜工具名；每个业务调用必须通过 `goal_ids` 明确绑定单一语义目标。
- Workflow 在 dispatch 前强制执行目标归属和 `depends_on`；未绑定、错绑、多绑、前置未成功均不会签发 Permit 或调用业务端口。
- 终止回答增加 Workflow 完整性校验；一个同名工具调用不能再伪覆盖两个不同对象目标。
- Core 移除电商领域词和 `all_orders` 枚举硬编码；模块能力规则由 Composition Root 注入。
- 事务仓库进入生产图的显式依赖链，可提交动作集合改为实时注册视图；回答发布校验纳入统一模型调用预算。
- 14 条独立 Semantic Goal Oracle 改为核验实际 Step 绑定，84 条脚本套件明确降级为 Runtime Contract 证明；新增缺绑、换绑、依赖未完成/失败等变异反例。
- Quality Loop 增加 repair fingerprint：只改当前轮次或原样重跑不能推进；Quick 增加专门 adversarial Gate。
- development-workspace 的前端依赖 Gate 改为只读验证，不再用 `npm ci` 删除或重建用户保留的 `node_modules`。

## 20.3.1 — 八轮动态质量 Loop 与停滞重规划

- Skill 升级为 V5.8.0：总修复预算上限由 3 轮提高为 8 轮，满足 Gate 时立即提前收敛，不要求跑满。
- 质量控制器记录每轮失败 Gate、失败数量和签名；连续两轮没有可度量改善时输出 `ARCHITECTURE_REPLAN_REQUIRED`，停止堆补丁。
- `BLOCKED_BY_ENVIRONMENT` 不推进修复轮次；第 8 轮仍失败才输出 `STOPPED_MAX_REPAIRS`。
- evidence 增加 convergence 摘要，repair plan 在停滞时要求新建架构目标而不是原样重试。
- Python Gate 显式使用隔离验证解释器，保留跨平台开发 `.venv`；子 Gate 清除外层 pytest-cov bootstrap。
- 收敛统计只计算直接 FAIL，`SKIPPED_UPSTREAM_FAILURE` 单独记录；版本 Gate 新增 quality-loop-policy 检查。

## 20.3.0 — Goal Coverage 与语义回归对齐

- Skill 升级为 V5.7.0：每个 user turn 必须先声明 `TurnGoalPlan`；受保护环境使用独立 `GoalAlignmentVerifier` 检查漏意图，领域工具在目标声明通过前保持关闭。
- Workflow Runtime 升级为 `workflow-runtime@2.0`：Effect / Step 绑定声明目标，遗漏目标或 orphan Step 会阻止最终回答；L0/L1/L2 改为依据结构化目标、Effect 与目标基数分类，不再依赖中文动作关键词。
- 明确当前 `WorkflowPlan` 只负责本 user turn 的目标覆盖与执行编排，不再把 soft task board 描述为跨回合 Durable Workflow 或 Multi-Task Scheduler。
- 将对话验证拆成 84 个 Runtime Contract cases 与 14 个独立 Semantic Goal Oracle cases；12 个风险原型在 preproduction 逐 turn 验证真实模型目标声明。
- 修复内部协议工具成功后被最终回答证据检查误判为业务证据缺失的问题。
- 开发工作区 profile 按当前要求保留 `.env`、数据库、缓存、依赖和历史 evidence；正式 clean-release profile 与开发循环分离。

## Unreleased — 发布级 Workflow 与对话回归收敛

- Skill 升级为 V5.6.0：采用 Codex 标准 `name`/`description` 元数据，将质量 Loop 与对话回归的细节移入按需 references；目标、基线、三轮停止、前置证据和真实运行时断言成为可执行合同。
- Skill 升级为 V5.5.0：质量 Loop 必须有有效目标记录；ADR-021 固化 WorkflowPlan 的替代范围、收敛边界和 `SUBMISSION_UNKNOWN` 的同幂等键对账语义。
- 对话 catalog 由逐案执行合同驱动，发布 Gate 分层验证确定性图、双服务 smoke 与受保护环境的模型只读风险原型。
- 集成 Gate 现在强制要求 pgvector/Postgres URL，并实际验证向量写入、检索和租户可见性，不能只因容器启动而放行。
- 认证 SSE 改为由持有业务上下文的工作线程实时投递已投影事件；新增回归证明首个公共更新在图完成前即可送达。

- Skill 升级为 V5.4.0：加入 L0/L1/L2 WorkflowPlan 协议与 Conversation Regression Suite 协议。
- 多意图、集合写入与长流程以只具编排权限的 `WorkflowPlan / Task / Step` 表示；最终答案受必需 Step 的 Runtime 验证约束。
- 新增 84 个对话回归用例，并逐条执行确定性的 Runtime 边界测试；不会以 catalog 文本替代行为证明。

## 20.2.0 — Loop 工程质量闭环

- Skill 升级为 V5.2.0，新增 Loop 化执行协议：目标冻结、基线检查、受控修改、机器验证、失败纠偏、停止条件和 skipped 分类。
- 新增 `scripts/quality_loop.py` 和 `governance/quality-loop-policy.json`，统一调度 static / quick / release 三档质量 Loop。
- 新增模块垂直闭环、版本一致性、强上下文用例目录、展示投影边界、运行态清理五类可执行检查器。
- 新增强上下文回归用例目录，覆盖可见结果过滤、代词指代、纠正覆盖、无能力请求、多目标写操作澄清、咨询/提交区分、授权摘要防篡改和提交未知对账。
- 发布报告改为由质量 Loop 最终 evidence 同步生成，机器证据落在 `governance/evidence/v20.2.0/`。

## 20.1.0 — 配置交付完整性修复

- 补回 Agent、Business、Frontend、pgvector 部署四份无密钥 `.env.example` 模板；不再要求用户凭代码或猜测手工创建 `.env`。
- 新增统一配置说明，明确本地/预发布/生产、模型、RAG、数据库和前端代理配置。
- 将模型温度、超时、SDK 重试从源码硬编码迁移为 `MODEL_TEMPERATURE`、`MODEL_TIMEOUT_SECONDS`、`MODEL_MAX_RETRIES`。
- 修复 Business Service 启动脚本在读取 host/port/reload 前未加载 `.env` 的问题。
- 修复 Vite 配置阶段未读取前端 `.env`，导致 `VITE_AGENT_DEV_TARGET` 只在 shell 导出时生效的问题。
- 统一验收器新增配置模板覆盖检查，防止运行时代码新增环境变量后漏交付模板。

## 20.0.0 — 架构收敛与减法重构

- 用 `lifecycle/` 合并旧 Graph、Node、Loop 与根级生命周期合同。
- 用 `presentation/` 合并旧展示适配、展示合同、发布 Gate 与根级展示辅助。
- 将事务授权/交互、操作能力、目标解析、资格核验、审计记录归位到唯一 Owner。
- 将 `bootstrap/` 改名为 `composition/`，明确它是唯一允许安装具体模块的装配边界。
- 删除未被正式路径使用的 Core 合同和演示评价目录。
- Skill 从追加式规则集收敛为 V5.0 的五条硬规则与一个统一验收器。
- 删除多代治理配置与平行 Guard；发布只保留当前 `architecture-policy.json` 和当前证据。

