---
name: oracle-review
description: Claim、Requirement、测试 Oracle 或用户问题描述可能错误时使用；显式 `/oracle` 时必须使用。独立审查目标和测试，不修改候选实现。
---

# Claude Code adapter

显式 `/oracle` 时，先通过根 `skillctl.py dev-command` 固定路由到本 Skill；命令后的正文全部作为用户辅助信息，不得改选相似 Review Skill，也不得在 dispatcher 失败后自行旁路回答。

读取并遵循 `skill-system/skills/oracle-review/SKILL.md`。共享治理合同位于 `skill-system/core/`，当前规则由 `skill-system/registry/` 裁决。不要复制或重新解释核心规则。
