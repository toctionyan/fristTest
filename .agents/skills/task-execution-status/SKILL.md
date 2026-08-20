---
name: task-execution-status
description: 当用户询问长任务进度、是否卡住、还要多久、失败恢复状态、是否需要人工介入或要求继续/恢复时使用；显式 `/status` 或 `/continue` 时必须使用。必须调用 canonical TaskRun status projection，不能直接根据最近 GitHub 对象猜整个任务状态。
---

# Codex adapter

显式 `/status` 或 `/continue` 时，先通过根 `skillctl.py dev-command` 固定路由到本 Skill；命令后的正文全部作为用户辅助信息。随后必须使用 `python3 -B skillctl.py task-status-project ...` 对 authoritative TaskRun 做状态投影并消费其 `execution-progress@1` 与 `skill-invocation-receipt@1`。`/continue` 必须先完成这一步，再根据 TaskRun 状态决定是否继续；不得重复启动健康的外部运行，也不得在 dispatcher 或 status projector 失败后自行根据 GitHub 对象猜状态。

读取并遵循 `skill-system/skills/task-execution-status/SKILL.md`。不要复制或重新解释状态权威规则。
