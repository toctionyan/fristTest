# Customer Agent Workspace · V20.6 / Skill V6.3 · Governance V20.17 B17i

这是客服 Agent 开发工作区。产品应用版本仍为 **20.6.1**，架构与发布治理阶段为 **V20.17 B17i**，Architecture Skill 版本为 **6.3.0**。

B17a–B17i 修改了生产认证 Harness、发布控制器、供应链、CI 运行身份与受保护环境预检合同；没有改动客服语义理解、Prompt、Capability、事务协议、业务规则、模型路由或 RAG 业务行为。

## 当前 B17i 状态

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
- 新 Change Contract 负责权限和完成条件；原 `quality_loop.py` 继续负责产品 Gate 与 Claim Evidence，不保留两套竞争裁判。
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
