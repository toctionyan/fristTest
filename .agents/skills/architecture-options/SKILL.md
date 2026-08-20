---
name: architecture-options
description: 架构设计、重构或现有规则可能阻碍更优方案时使用；显式 `/arch` 或 `/agent-arch` 时必须使用。至少比较保守、演进与重构方案，区分硬不变量、强默认和参考模式。
---

# Codex adapter

显式 `/arch` 或 `/agent-arch` 时，先通过根 `skillctl.py dev-command` 的固定命令映射装载所需 Skill；命令后的正文全部作为用户辅助信息，不得根据正文关键词改选别的 Skill，也不得在 dispatcher 失败后自行旁路回答。

读取并遵循 `skill-system/skills/architecture-options/SKILL.md`。共享治理合同位于 `skill-system/core/`，当前规则由 `skill-system/registry/` 裁决。不要复制或重新解释核心规则。
