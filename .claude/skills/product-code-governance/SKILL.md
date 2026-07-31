---
name: product-code-governance
description: 在产品代码诊断、设计、修复、迁移、回滚或认证前使用。调用统一 skillctl、Change Contract 与产品 Quality Loop。
---

# Claude Code adapter

读取并遵循 `skill-system/skills/product-code-governance/SKILL.md`。

统一命令：

- `python3 -B skillctl.py product-init ...`
- `python3 -B skillctl.py product-baseline`
- `python3 -B skillctl.py product-verify --mode <contract-mode>`
- `python3 -B skillctl.py contract-verify --result CONVERGED`
- `python3 -B skillctl.py contract-close --result CONVERGED`

不要在 Claude 适配层复制或改变共享合同。
