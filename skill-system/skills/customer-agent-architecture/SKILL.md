---
name: customer-agent-architecture
description: 仅在修改客服 Agent 的开放语义、上下文、多任务规划、能力落地、事务、Publication 或 Business 权威边界时使用。它约束职责和证据，不规定类名、目录层级或节点数量。
---

# Customer Agent Architecture

读取并遵守 `architecture-skill/SKILL.md`。

- 用户语言和上下文关系只有一个语义 Owner；程序验证事实，不重新分类语言。
- 用户 Goal 不得为匹配现有 Tool 而被改写；能力映射必须经过精确 MatchProof。
- 语义 Goal 与 Tool/API 执行步骤分离；按当前任务生成局部闭合计划，不预建全局万能能力图。
- 当前类名和目录属于 Project Architecture Baseline；架构迁移通过 Decision、Variance、Policy Delta 和 Baseline Promotion 完成。
- 通用范围、Repair、Evidence、Hook、Reviewer 和 Judge 由 `skill-system/` 控制平面负责。
