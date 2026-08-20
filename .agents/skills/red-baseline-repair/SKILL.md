---
name: red-baseline-repair
description: 根因和 Oracle 已确认后使用；显式 `/repair` 时必须与 product-code-governance 一起使用。建立真实失败或差距基线，由唯一写入者做最小修复，定向诊断后再运行完整 Profile；不得修改裁判输入。
---

# Codex adapter

显式 `/repair` 时，先通过根 `skillctl.py dev-command` 固定装载本 Skill 与 `product-code-governance`；命令后的正文全部作为本次修复辅助信息。真实写入仍必须经过现有 `change-scope` Hook，dispatcher 失败时不得自行旁路修代码。

读取并遵循 `skill-system/skills/red-baseline-repair/SKILL.md`。共享治理合同位于 `skill-system/core/`，当前规则由 `skill-system/registry/` 裁决。不要复制或重新解释核心规则。
