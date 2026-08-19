---
name: task-execution-status
description: 当用户询问长任务进度、是否卡住、还要多久、失败恢复状态、是否需要人工介入或要求继续/恢复时使用。必须调用 canonical TaskRun status projection，不能直接根据最近 GitHub 对象猜整个任务状态。
---

# Codex adapter

读取并遵循 `skill-system/skills/task-execution-status/SKILL.md`。状态查询使用 `python3 -B skillctl.py task-status-project ...`，并消费其 `execution-progress@1` 与 `skill-invocation-receipt@1`；不要复制或重新解释状态权威规则。
