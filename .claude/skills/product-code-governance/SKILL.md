---
name: product-code-governance
description: 在产品代码诊断、设计、修复、迁移、回滚或认证前使用；显式 `/diagnose` 或 `/repair` 时必须使用。调用统一 skillctl、Change Contract 与产品 Quality Loop。
---

# Claude Code adapter

显式 `/diagnose` 或 `/repair` 时，先通过根 `skillctl.py dev-command` 的固定映射装载本 Skill；命令后的正文全部作为用户辅助信息。`/diagnose` 保持只读；`/repair` 进入受控修复，但真实写入仍必须经过现有 Change Contract / ChangePermit / `change-scope` Hook。dispatcher 失败时不得自行旁路进入产品修改。

读取并遵循 `skill-system/skills/product-code-governance/SKILL.md`。

统一命令：

- `python3 -B skillctl.py product-init ...`
- `python3 -B skillctl.py product-baseline`
- `python3 -B skillctl.py product-verify --mode <contract-mode>`
- `python3 -B skillctl.py contract-verify --result CONVERGED`
- `python3 -B skillctl.py contract-close --result CONVERGED`

不要在 Claude 适配层复制或改变共享合同。
