> **Current phase:** V20.17 B28. Repository onboarding authority is CLOSED_VERIFIED; WP-08 and WP-09 remain externally blocked.

# Customer Agent Workspace · V20.6 / Skill V6.7 · Governance V20.17 B28

这是客服 Agent 开发工作区。产品应用版本仍为 **20.6.1**，Architecture Skill 版本为 **6.7.0**。B28 新增仓库导入预检，并把手动集成诊断 Workflow 绑定到发布工具链权威；没有关闭 WP-08、WP-09 或生产发布。

## 当前 B28：仓库导入与诊断 Workflow 权威

- 未提供目标仓库元数据时返回 `BLOCKED_BY_ENVIRONMENT`，不会猜测或修改无关仓库；
- 非空仓库必须提供匹配的 Workspace Marker；公开仓库需要显式批准；
- protected `main`、`production-certification` Environment 和必要 Secret 名称分别校验；
- 真实 `.env`、缓存目录、符号链接和可变 Workflow 依赖会被拒绝；
- `integration-diagnostic.yml` 现与 release toolchain lock 使用同一 Python、Node、npm、uv、Action SHA 和 pgvector Digest。

## 当前 B26：WP-08 跨 GitHub Run 恢复权威

B26 不关闭 WP-08，也不修改客服产品逻辑。它修复 B25 “执行器支持 resume、但 GitHub Workflow 没有恢复上一 Run Artifact”的控制面缺口：

- `workflow_dispatch` 可显式指定 `resume_run_id` 与 `resume_run_attempt`，不会自动猜测历史 Run；
- 使用固定 SHA 的 `actions/download-artifact` 下载上一轮 WP-08 Artifact；
- `scripts/prepare_wp08_resume.py` 把下载内容视为不可信输入，拒绝符号链接、路径逃逸、错仓库、错提交、错 Run、源码漂移和被篡改的 Toolchain Evidence；
- 只跳过同一源码身份下已 PASS 的批次，BLOCKED/FAIL/TIMEOUT 批次会重试；
- WP-08 Toolchain Evidence 改由正式 `release_toolchain_contract.py` 产生，并锁定 `wp08-certification.yml / certify / protected main` 身份；
- 21 个聚焦/反例、107 个完整 Skill 测试和 119 个生产发布权威测试通过；独立 DiffReview 确认产品源码零变化；
- 当前连接的 GitHub 仓库中没有该工程，因此没有上传到无关仓库，也没有宣称真实 Workflow 已运行；`WP-08=BLOCKED`、`production_closed=false`。


## 当前 B25：WP-08 可恢复全栈认证执行器

B25 不关闭 WP-08，也不修改客服产品逻辑。它解决真实认证容易被单个环境缺口或长任务整体卡住的问题：

- 新增 `scripts/run_wp08_certification.py`，对受保护环境预检、PostgreSQL/pgvector 恢复、真实模型/RAG、浏览器全栈四个批次分别设置超时；
- 每批独立保存 stdout、stderr、结构化结果和原子状态；BLOCKED、FAIL、TIMEOUT 不会阻止后续批次；
- `--resume` 只跳过同一源码指纹下已 PASS 的批次，源码变化会拒绝旧检查点；
- 新增 `.github/workflows/wp08-certification.yml`，使用锁定工具链、受保护 Environment 和不可变 Action SHA，并始终上传证据；
- 11 个定向/反例、97 个 Skill 测试、51 个生产权威回归通过；当前容器实测四批均被独立记录为环境阻塞，没有停在 RUNNING；
- `STAGE-5/WP-08` 仍为 `BLOCKED`，`production_closed=false`。


## 当前 B24：Stage-5 质量工具链权威闭环

B24 没有关闭 WP-08，也没有修改客服语义、事务或业务行为。它修复了质量 CI 与发布工具链长期分叉的问题：

- `.github/workflows/quality.yml` 与 `deployment/ci/release-toolchain-lock.json` 共享同一 runner、Python、Node、npm、uv、Action SHA 和 pgvector digest；
- uv 先从带 SHA-256 的锁文件安装，随后执行精确运行时校验，最后才同步项目依赖；
- 工具链证据进入 Quick/Integration Artifact；版本或供应链漂移返回 `BLOCKED_BY_ENVIRONMENT`；
- 5 个定向反例、31 个供应链回归和五个 Skill Profile 全部通过；537 个受保护产品文件没有变化；
- 当前容器仍不是锁定运行时，且没有 Docker/PostgreSQL、浏览器和真实提供商凭证，因此 `STAGE-5/WP-08` 仍为 `BLOCKED`，`production_closed=false`。

## 当前 B22：事务、多 Draft 与身份安全闭环

B22 完成总计划第 4 阶段（WP-06/WP-07）：`focused_draft_id` 成为唯一交互焦点权威，多个持久 Draft 可并存；Actor、Subject 与 Resource Scope 在认证、Agent、命令传输和 Business Service 中分离验证。Agent 非环境回归 63 passed（1 个 PostgreSQL integration 明确排除），Business Service 38 passed。锁定 LangGraph、真实 PostgreSQL、前端、浏览器和真实模型认证进入 STAGE-5/WP-08；`production_closed=false`。

## 当前 B21：上下文、多 Goal 与精确能力闭环

B21 完成总计划第 3 阶段（WP-04/WP-05）：

- 新增只读 `visible_referent_sets`，仅投影可见 ResultRef、最近同轮集合和连续最近分组，始终 `dispatchable=false`，不自动选目标；
- 多结果后的单数历史续问 fail closed；显式返回必须绑定当前原文字面标签，显式分组必须验证成员数和连续最近来源；
- 多 Goal 的 Task 依赖同时保留正式 Goal 依赖与执行步骤依赖，查询→写操作不会被错误并行；
- Capability 只按结构化 requested effect 精确匹配，未知效果进入 unsupported，不使用相似能力替代；
- 50/100/200/200 四组锁定 Campaign 共 550/550 通过，定向套件 620 passed、1 个 LangGraph 依赖用例明确 deselected；
- 第一次独立 DiffReview 拒绝把 Campaign 数据和共享验证器放在 `/tests/` 下；恢复 B20 基线、重新审批并签发新 Permit 后，第二次 DiffReview PASS；
- WP-04/WP-05 ClosureMatrix 为 `CLOSED_VERIFIED`，总账推进到 `STAGE-4`；真实模型、LangGraph 全生命周期和全栈认证仍归 WP-08，`production_closed=false`。

## 当前 B20：语义权威最终切换与旧链退出

B20 完成总计划的第 2 阶段（WP-03），对 Agent Service 的语义主链做受治理切换：

- 新 Turn 的 Runtime、能力验证、澄清、工具执行和回答释放只读取 `frozen_semantic_contract`、正式 Plan Definition/Run、Goal/Blocker 与受验证投影；
- `turn_goal_plan`、`workflow_plan`、`pending_clarification` 只允许在 `lifecycle/state_schema.py` 的一次性旧 checkpoint 迁移边界读取；
- 删除 Runtime 中的永久旧链读取、旧 writer、模糊能力修复和静默 fallback；
- 正常测试夹具迁移到正式语义合同和计划权威；旧字段只保留在迁移测试、负例或“输出不得含旧字段”的断言中；
- 独立 DiffReview 两次拒绝不完整候选后，第三次重放通过；被拒记录完整保留；
- WP-03 的八维 ClosureMatrix 为 `CLOSED_VERIFIED`，总账现已推进到 `STAGE-3`；
- 锁定 Python 3.12.13、LangChain/LangGraph、真实模型、PostgreSQL/pgvector 和浏览器全栈认证仍归 WP-08，`production_closed` 仍为 `false`。

关键状态命令：

```bash
python3 -B skillctl.py task-ledger-validate
python3 -B skillctl.py task-ledger-status
```

## B18 代码治理闭环（已完成）

B18 不修改客服语义、Capability、事务或业务事实。它把此前分散存在的角色、Change Contract 和 Quality Loop 串成不可绕过的受治理修复状态机：

- 写入前必须有已复现 FailureCase、已证明 RootCauseProof、获批 RepairPlan 和 baseline-bound ChangePermit；
- 唯一实现者只有在 `implementing` 状态下才能写入，且每个路径同时受 Contract 与 Permit 双重约束；
- 独立只读 Diff Reviewer 从真实树计算差异，并检查范围、禁止模式和测试完整性；
- Closure Arbiter 只接受当前 Permit、当前 Diff、当前源码指纹绑定的八维证据；
- 最大循环次数、环境阻塞、缺失反例或陈旧证据均返回 BLOCKED/REJECT，不能假绿。

入口命令见 `AGENTS.md` 与 `skill-system/README.md`。治理记录格式见 `skill-system/schemas/repair-governance.schema.json`。

## B17i 生产执行状态（保持未关闭）

- B17i 为无密钥 `release-admission` 增加原子 JSON 和 `always()` Artifact，因此非法分支或输入也有独立可下载证据。
- 最终 GitHub 执行步骤见 `docs/operations/B17I_FINAL_PRODUCTION_EXECUTION_RUNBOOK.md`。

- `release-admission` 继续在无密钥 Job 中显式拒绝非法 trigger、分支和输入。
- 新增 `protected-environment-preflight@1`，在 `uv sync`、`npm ci` 和 Chromium 安装前验证精确基础工具、Docker、官方模型/Embedding 配置和三个生产 Secrets。
- 预检失败只输出脱敏原因与短指纹，不输出密钥值；结果由 `if: always()` 的生产证据 Artifact 上传。
- Release Action、Python/Node/npm/uv、依赖锁、Docker/pgvector、GitHub Run/Attempt 和干净 checkout 继续保持 B17e/B17f 的不可变绑定。
- 当前交付仍是 `PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING`：当前连接的 GitHub App 没有已安装账号或可访问仓库，且本地缺少受保护 GitHub Environment、精确锁定运行时、Docker 与生产密钥，因此不得生成 `production_closed`。

## 6.3 的核心闭环

- 架构收敛结果现在区分 `PASS`、`PASS_WITH_DEBT` 与 `FAIL`。
- 当前已知依赖强连通分量进入显式债务基线；新增或扩大依赖环会阻断，缩小会记录进展。
- Quality Evidence 单独公开功能、架构和真实模型认证维度；真实模型未执行时必须写明 `NOT_DECLARED`。
- Skill 6.3.0 最初的架构债务收敛阶段只修改 Skill、控制平面和治理基线；B17a–B17i 随后获批修改生产认证与发布控制 Harness，但仍未修改客服业务语义和事务行为。

## 6.2 的核心闭环

- 通用 Skill 只规定职责、权威、禁止行为和证据，不再把当前类名、目录或层数当成硬规则。
- `architecture-policy.json` 被明确为版本化 Project Architecture Baseline。
- Architecture Variance 现在可以绑定机器可读 Policy Delta；架构 Gate 实际使用“Baseline + approved Delta”，不再出现“文档允许偏离但 Judge 仍按旧白名单拒绝”。
- Migration 必须声明只读 Shadow、唯一正式 Owner、切换、回滚、旧链清理和截止日期。
- 认证后由独立 Skill-only Migration 将 Delta 提升为新 Baseline，并生成带 Evidence 哈希的 Promotion Record。
- 客服领域 Skill 新增统一语义、多任务 Goal、能力 Grounding、局部执行计划和参数权威边界，但不强制具体类名。

## 6.1 的目标

同一套治理环境可以：

1. 在当前 ChatGPT 代码环境中约束并验证产品代码优化；
2. 随工程目录带到 Codex；
3. 随工程目录带到 Claude Code；
4. 三个宿主共享同一个合同、范围、Quality Loop、Judge 和 Evidence，而不是各自维护不同提示词。

## 统一架构

```text
当前环境 / Codex / Claude Code
              ↓
        根目录 skillctl.py
              ↓
       Portable Change Contract
              ↓
Diagnosis / Design / Oracle Review
              ↓
Product Baseline + 单一 Implementer
              ↓
原产品 Quality Loop（static/quick/integration/release）
              ↓
Scope Review + Adversarial Review
              ↓
Deterministic Verify + Close
```

## 重要改进

- 新控制平面不再只治理 Skill 自身，也能正式治理产品代码。
- 产品任务分为 diagnosis、design、oracle-review、repair、migration、revert、certification。
- 产品写入必须精确到模块或文件，拒绝 `services/**` 等根级写入通配符。
- Repair、Migration 和 Revert 必须先运行真实红基线，不能直接开始修改。
- 新 Change Contract 负责权限和完成条件；`quality_loop.py` 兼容入口与 `quality_control/` 模块共同负责产品 Gate 与 Claim Evidence，不保留两套竞争裁判。
- `product-implementer` 是唯一产品写入者；Planner、Oracle Reviewer、Adversarial Reviewer 和 Judge 保持只读。
- 当前环境、Codex 与 Claude Code 的薄适配器都调用 `skillctl.py`，Host 不能降低 Profile 或改变证据语义。
- 修复了点目录路径归一化问题，`.quality/**`、`.agents/**`、`.claude/**`、`.codex/**` 不再可能因错误去掉前导点而绕过保护。

## 产品代码优化流程

### 1. 准备 Target 与 Claim

可使用已有 Target，也可以生成初始模板：

```bash
python3 -B skillctl.py product-scaffold \
  --change-id repair-context-001 \
  --target-kind repair \
  --goal "修复一个可复现的上下文错误" \
  --allow 'services/agent-service/src/customer_agent/context/**' \
  --allow 'services/agent-service/tests/context/**' \
  --minimum-mode quick \
  --claim-id CONTEXT-REPAIR-001 \
  --claim-statement "The failing context behavior is corrected without introducing a second authority." \
  --required-gate python-test-suites \
  --evidence-ref 'services/agent-service/tests/context/test_example.py::test_regression' \
  --owner 'agent context runtime'
```

模板仍需由 Planner 补齐准确测试和验收语义，不能把占位 Claim 当作证据。

### 2. 创建产品合同

```bash
python3 -B skillctl.py product-init \
  --change-id repair-context-001 \
  --target-kind repair \
  --goal "修复一个可复现的上下文错误" \
  --allow 'services/agent-service/src/customer_agent/context/**' \
  --allow 'services/agent-service/tests/context/**' \
  --affected-module agent-context \
  --minimum-mode quick \
  --quality-target governance/targets/repair-context-001.md \
  --approve
```

### 3. 建立红基线并开始修改

```bash
python3 -B skillctl.py product-baseline
python3 -B skillctl.py contract-begin
```

### 4. 生成产品验证证据

手工或当前环境完成修改后：

```bash
python3 -B skillctl.py product-verify --mode quick
```

接入 Codex、Claude Code 或其他外部 Fixer 时，也可以让控制器自动运行：

```bash
python3 -B skillctl.py product-repair-loop \
  --trusted-judge-root <external-judge> \
  --require-external-judge \
  <fixer-command>
```

`product-repair-loop` 收敛后会把最终完整 Evidence 和当前源码指纹写回合同。

### 5. 审查、验证和关闭

```bash
python3 -B skillctl.py attest-review --role scope-planner --decision PASS --evidence <scope-review.json>
python3 -B skillctl.py attest-review --role adversarial-reviewer --decision PASS --evidence <review.json>
python3 -B skillctl.py contract-verify --result CONVERGED
python3 -B skillctl.py contract-close --result CONVERGED
```

## Codex 和 Claude Code

必须从工程根目录启动，并保留：

```text
AGENTS.md
CLAUDE.md
skillctl.py
skill-system/**
architecture-skill/**
governance/**
scripts/**
.agents/**
.claude/**
.codex/**
```

Codex 使用 `.agents/skills/` 与 `.codex/agents/`；Claude Code 使用 `.claude/skills/`、`.claude/agents/` 和 Hook。两者都只能调用共享治理内核。

## 验证入口

```bash
make skill-quality
make skill-release
python3 -B skillctl.py profiles
```

产品完整发布仍使用原 PostgreSQL、双服务、浏览器、真实模型和 clean-release Gate。当前环境缺少的外部服务必须返回 `BLOCKED_BY_ENVIRONMENT`，不能冒充通过。

## B28 repository onboarding

Before importing this candidate into GitHub, run `scripts/repository_onboarding_preflight.py` with target repository metadata. The contract refuses unrelated nonempty repositories and missing protected-environment prerequisites.
