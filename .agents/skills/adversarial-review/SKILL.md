---
name: adversarial-review
description: 候选修改完成后使用；显式 `/review` 时必须使用。盲审 Diff，寻找规则迎合、旧路径回归、测试过拟合、无价值抽象和更简单删除方案；保持只读。
---

# Codex adapter

显式 `/review` 时，先通过根 `skillctl.py dev-command` 固定路由到本 Skill；命令后的正文全部作为本次审查辅助信息。不得改选相似 Skill，不得在 dispatcher 失败后自行宣布 review PASS，也不得直接修改候选代码。

读取并遵循 `skill-system/skills/adversarial-review/SKILL.md`。共享治理合同位于 `skill-system/core/`，当前规则由 `skill-system/registry/` 裁决。不要复制或重新解释核心规则。
