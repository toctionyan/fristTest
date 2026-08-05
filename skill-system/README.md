# Stable Skill and Product Governance 6.7

本目录把 Skill 治理、产品代码优化和架构基线迁移统一成一个宿主无关控制平面。当前 ChatGPT 代码环境、Codex 与 Claude Code 都调用根目录 `skillctl.py`，共享同一个 Change Contract、Quality Loop、Profile、Architecture Policy 和 deterministic Judge。



## 6.7 上下文、多 Goal 与精确能力闭环

B21 使用产品 Migration Permit 修复只读 ReferentSet、歧义 fail-closed、多 Goal Task 依赖和精确 requested-effect 能力边界；随后使用独立 Skill-only Permit 更新总账与发布身份。确定性 Campaign 与真实模型认证分开记录，550/550 不会被表述为 real-provider certification。

## 6.6 语义权威切换治理

B20 使用产品 Migration Permit 完成 Agent Runtime 旧语义链退出，再使用独立 Skill-only Repair Permit 更新任务总账和发布元数据。产品实现者不能修改自己的闭环状态；旧 checkpoint 兼容只存在于单点迁移器，不能回流为 Runtime 第二权威。



## 6.4 受治理修复闭环

对于 `repair`、`migration`、`revert`，控制平面现在强制以下链路，而不是让同一个 Agent 自己诊断、批准、修改并宣布完成：

```text
Failure Explorer（只读）
→ RootCauseProof
→ RepairPlan
→ Repair Plan Reviewer（只读、有否决权）
→ ChangePermit
→ skill/product implementer（唯一写入）
→ Diff Integrity Reviewer（只读、真实树重算）
→ Closure Arbiter（只读、八维证据）
→ deterministic verify / close
```

治理对象位于 `governance/repair-cases/<change-id>/`。`repair-permit` 在修改前冻结 baseline 和精确范围；`repair-diff-review` 在修改后重算差异；`repair-closure-record` 只允许完整证据闭环。Host Hook 会拒绝非 `implementing` 写入、无效 Permit、越权路径和未闭环停止。

## 6.3 架构真相与债务棘轮

- Architecture Gate 不再把已检测到的大规模依赖环显示为架构完全干净。
- 既有循环依赖通过 Project Baseline 登记为可审计债务，只允许缩小或清零。
- 新增、扩大、合并或未登记循环依赖立即失败。
- Quality Evidence 分别公开功能状态、架构状态与真实模型认证声明，避免确定性测试被误解为真实模型认证。

## 两类治理对象

### Skill/control-plane 变更

使用 `skill-only` Profile，产品源码保持只读。

### 产品代码变更

使用：

- `product-diagnosis`
- `product-design`
- `product-oracle-review`
- `product-repair`
- `product-migration`
- `product-revert`
- `product-certification`

Diagnosis、Design、Oracle Review 与 Certification 是只读；Repair、Migration、Revert 必须先有真实红基线或明确 transition baseline。

## 规则层次

1. `HARD_INVARIANT`：安全、权威、证据和真实性；不可偏离。
2. `STRONG_DEFAULT`：已验证的默认设计；可通过 Variance 偏离。
3. `REFERENCE_PATTERN`：职责或实现参考；不是固定对象名。
4. `PROJECT_BASELINE`：当前项目的目录、必需文件、Owner 和尺寸快照。
5. `WORKFLOW_DEFAULT`：流程默认值。
6. `EXAMPLE_ONLY`：帮助理解，不可作为 Gate。

## 产品主链

```text
用户目标
→ diagnosis / design / oracle review
→ Change Contract
→ baseline
→ 单一 implementer
→ Quality Loop
→ scope + adversarial review
→ deterministic verify
→ close
```

## 架构迁移闭环

```text
Project Architecture Baseline
        ↓
Architecture Decision（三方案）
        ↓
Architecture Variance
        ↓
Machine-readable Policy Delta
        ↓
Baseline + approved Delta = 本次有效 Gate
        ↓
只读 Shadow / Cutover / Rollback / Cleanup
        ↓
产品认证
        ↓
独立 skill-only Baseline Promotion
        ↓
新 Project Architecture Baseline + Promotion Record
```

预览本次有效策略：

```bash
python3 -B skillctl.py architecture-preview
```

架构迁移合同可绑定：

```bash
python3 -B skillctl.py product-init \
  --target-kind migration \
  --decision-record governance/decisions/<change>.json \
  --variance governance/variances/<change>.json \
  --architecture-policy-delta governance/architecture-deltas/<change>.json \
  --baseline-policy-id <current-policy-id> \
  ...
```

认证后，使用独立 `skill-only migration` 合同提升新基线：

```bash
python3 -B skillctl.py architecture-promote \
  --new-policy-id <new-policy-id> \
  --certification-evidence <verification.json>
```

## 权威边界

- `skillctl.py`：三个宿主共享的 Portable CLI。
- `contract.py`：范围、角色、Target 类型和完成条件。
- `architecture_policy.py`：Project Baseline、approved Delta 和 Promotion Record。
- `scripts/quality_loop.py`：稳定兼容入口；`scripts/quality_control/`：产品 Gate、Claim Evidence、环境执行、质量维度和收敛的单一职责实现。
- `trusted-judge/`：受保护认证的裁判实现。
- Host adapter：只负责发现和调用，不得改变合同语义。

## 关键原则

- Skill 规定必须证明什么，不规定最终类名、目录层数和节点数。
- 当前项目名称只由 Project Baseline 或 Architecture Decision 约束。
- Variance 必须真正影响 Judge；只有说明文档而 Gate 不读取，视为未闭环。
- 产品实现者不得修改 Skill、Judge、Evidence 或控制平面来获得通过。
- 临时 Shadow 和 Delta 必须有截止日期、切换、回滚、清理和最终 Promotion。

## 权威任务总账

`governance/task-ledger.json` 是所有跨阶段未完成事项的唯一状态源。

```bash
python3 -B skillctl.py task-ledger-validate
python3 -B skillctl.py task-ledger-status
```

Validator 会检查固定阶段/工作包、唯一 ID、依赖图、必做项、阻塞原因、Scope Decision 和证据引用。
