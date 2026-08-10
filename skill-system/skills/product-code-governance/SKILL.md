---
name: product-code-governance
description: 在当前环境、Codex 或 Claude Code 中审计、设计、修复、迁移、回滚或认证产品代码时使用。通过同一个 skillctl、Change Contract、原 Quality Loop 和 deterministic Judge 约束范围与证据。
---

# Portable Product Code Governance

## 目标

让不同宿主共享同一条正式治理链，而不是各自解释一份提示词：

```text
用户目标
→ product diagnosis / design
→ product Change Contract
→ product-baseline
→ 单一 product-implementer
→ 原 Quality Loop Gate
→ scope-planner + adversarial-reviewer
→ deterministic contract-verify
→ contract-close
```

## 强制入口

1. 架构或复杂修改先做只读 diagnosis/design。
2. transition 修改先创建或选择真实 Quality Target 与 Claim。
3. 使用 `python3 -B skillctl.py product-init ...`，不能手写 active contract。
4. `product-repair`、`product-migration`、`product-revert` 必须先运行 `python3 -B skillctl.py product-baseline`。
5. 修改完成后必须运行 `python3 -B skillctl.py product-verify --mode <contract-mode>`；使用自动修复时可运行 `product-repair-loop`，它必须把最终 `CONVERGED` Evidence 写回合同。
6. 只有 `product-implementer` 可以写入，且仅写合同 `allowed_paths`。
7. Target、Claim、Policy、Baseline、Judge 与 Evidence 不属于产品实现者的可写范围。
8. 架构迁移如改变当前目录、必需文件或 Owner，必须绑定 Decision、Variance、Policy Delta 和当前 baseline policy id；产品实现者只能消费这些只读治理记录。
9. 完成链必须是：

```text
skillctl.py attest-review --role scope-planner ...
skillctl.py attest-review --role adversarial-reviewer ...
skillctl.py contract-verify --result ...
skillctl.py contract-close --result ...
```

## 范围原则

- 产品写入范围必须精确到模块、包、测试目录或具体文件。
- 禁止使用 `services/**`、`services/agent-service/**` 等根级写入通配符。
- Diagnosis、Design、Oracle Review 和 Certification 是只读 Target。
- 原产品 Quality Loop 继续裁决 static/quick/integration/release Gate；新控制平面只负责权限、合同、宿主一致性和最终证据绑定。

## 宿主无关

当前环境、Codex 和 Claude Code 都调用根目录 `skillctl.py`。宿主适配器不得复制或改变合同、Profile、Judge 与 Evidence 语义。

## 远程输入 Anti-Stall（WORKFLOW_DEFAULT）

复杂代码任务如果需要 GitHub、远程文档或其他 Connector 输入，必须优先减少远程调用链深度，而不是依赖失败后无限重试。这个默认值只约束**输入获取与任务执行节奏**，不得改变 Quality Loop Gate、Claim、repair-round、convergence、业务权威或写入授权语义。

1. **先冻结 Working Set，再执行**：执行前声明本 Atomic Step 所需的 `source + immutable ref + path/resource + required/optional`。未知资源允许先做有限 discovery；一旦 Working Set 冻结，不得在执行中无限“边想边搜、边搜边扩”。
2. **本地/快照优先**：同一 `source + immutable ref + path` 已有内容校验通过的 Snapshot 时，后续读取必须优先使用缓存，缓存命中消耗 0 次远程调用；ref 改变、内容校验失败或身份不完整必须视为 miss。
3. **Atomic Step 远程预算固定为 `max_remote_calls = 2`**：一次 primary，加上 primary 明确失败后至多一次不同路径的 fallback。timeout、503、明确 empty-result 或连接失败后，本 Step 的 primary circuit 立即打开；禁止同 Tool 原地重试。
4. **并发读取有硬上限 `max_parallel <= 4`**：只有互相独立、且位于兼容 source/ref 边界的读取可以同批；有依赖关系的资源必须等待前置完成，不能为了提速破坏依赖顺序。
5. **Fallback 用尽即停止当前 Atomic Step**：fallback 再失败时记录失败、checkpoint/report 并停止该 Step；不得继续临时寻找第三、第四条远程路径。required 输入失败时整个任务 fail closed；optional 输入可保留失败证据后继续不依赖它的工作。
6. **长任务按 Atomic Step 持久化进度**：每个可验证小阶段结束后保存 checkpoint；同一次中断恢复只能复用精确匹配的 target/policy/source snapshot/mode/selected closure/evidence，并重新验证已完成证据。历史 PASS 不能冒充同一次运行的 Resume frontier。
7. **实现参考而非第二主链**：`skill-system/controller/task_harness.py`、`working_set.py`、`snapshot_cache.py`、`bounded_batch.py`、`anti_stall.py` 和 `fallback_state.py` 是这个 Workflow Default 的可执行参考。宿主能注入 remote reader 时可直接复用；宿主不能把平台 Connector 交给仓库代码控制时，也必须遵守等价的 Working Set / Budget / Circuit / bounded fallback 节奏，不能声称仓库代码已经拦截平台级 Connector。

这个默认协议的目标是把“远程同步”与“本地分析”分开：尽量一次取得稳定 Working Set，后续 grep/AST/依赖分析/patch/test 在本地完成。若宿主能力不足以实现 batch/cache，应缩小 Atomic Step 并明确 checkpoint，而不是退化为开放式远程调用循环。


## 架构 Baseline 规则

- `architecture-policy.json` 是当前项目快照，不是通用 Skill 硬规则。
- 普通 Repair 不得改变 Baseline。
- Migration 可使用 approved Policy Delta，使实际架构 Gate 接受本次精确新增/退休路径。
- Delta 不得改变业务权威、Evidence、Judge、配置事实或禁止相似能力替代。
- 产品认证完成后，通过独立 skill-only Migration 将 Delta 提升为新 Baseline；产品实现者不得自行提升。
