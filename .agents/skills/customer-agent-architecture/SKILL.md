---
name: customer-agent-architecture
description: 仅在修改客服 Agent 的上下文、能力路由、Workflow、事务、RuntimeOutcome 或 Business 权威边界时使用；显式 `/agent-arch` 时必须与 architecture-options 一起使用。它是领域参考架构，不负责通用 Skill 控制流程。
---

# Codex adapter

显式 `/agent-arch` 时，先通过根 `skillctl.py dev-command` 固定装载本 Skill 与 `architecture-options`；命令后的正文全部作为用户辅助信息。不得把本领域 Skill 提升成通用 Harness Owner，也不得在 dispatcher 失败后自行旁路架构流程。

读取并遵循 `skill-system/skills/customer-agent-architecture/SKILL.md`。共享治理合同位于 `skill-system/core/`，当前规则由 `skill-system/registry/` 裁决。不要复制或重新解释核心规则。
