---
name: product-code-governance
description: Use before any product-code diagnosis, design, repair, migration, revert, or certification. Enforces the portable skillctl Change Contract and shared product Quality Loop.
---

# Codex adapter

读取并遵循 `skill-system/skills/product-code-governance/SKILL.md`。

统一命令：

- `python3 -B skillctl.py product-init ...`
- `python3 -B skillctl.py product-baseline`
- `python3 -B skillctl.py product-verify --mode <contract-mode>`
- `python3 -B skillctl.py contract-verify --result CONVERGED`
- `python3 -B skillctl.py contract-close --result CONVERGED`

不要在 Codex 适配层复制或改变共享合同。
