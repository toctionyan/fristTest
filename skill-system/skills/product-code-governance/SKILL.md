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


## 架构 Baseline 规则

- `architecture-policy.json` 是当前项目快照，不是通用 Skill 硬规则。
- 普通 Repair 不得改变 Baseline。
- Migration 可使用 approved Policy Delta，使实际架构 Gate 接受本次精确新增/退休路径。
- Delta 不得改变业务权威、Evidence、Judge、配置事实或禁止相似能力替代。
- 产品认证完成后，通过独立 skill-only Migration 将 Delta 提升为新 Baseline；产品实现者不得自行提升。
