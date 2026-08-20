---
name: release-certification
description: 候选冻结后使用；显式 `/cert` 时必须使用。通过外置可信 Judge 运行累积 Profile，校验版本、Evidence、宿主和产物身份；不进行修复。
---

# Claude Code adapter

显式 `/cert` 时，先通过根 `skillctl.py dev-command` 固定路由到本 Skill；命令后的正文全部作为认证辅助信息。dispatcher 失败时不得自行宣布认证通过，认证阶段也不得修改候选实现。

读取并遵循 `skill-system/skills/release-certification/SKILL.md`。共享治理合同位于 `skill-system/core/`，当前规则由 `skill-system/registry/` 裁决。不要复制或重新解释核心规则。
