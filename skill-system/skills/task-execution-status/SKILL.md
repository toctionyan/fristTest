---
name: task-execution-status
description: 当用户询问“现在跑到哪了”“卡住了吗”“还要多久”“失败后是否还在恢复”“是否需要我操作”“能否继续/恢复”或要求展示长任务整体进度时使用。必须从 authoritative TaskRun 和 durable execution evidence 生成 execution-progress@1，禁止直接根据最近一个 PR、merge 或 CI run 猜整个任务状态。
---

# Task Execution Status

## 目标

把“查询进度”变成一个可证明的只读执行路径，而不是宿主临时查看几个 GitHub 对象后自行总结。

## 强制入口

支持仓库内 CLI 的宿主必须使用：

```bash
python3 -B skillctl.py task-status-project \
  --task-run <authoritative-task-run.json> \
  --invocation-id <unique-id> \
  [--github-jobs <jobs.json>] \
  [--github-steps <steps.json>] \
  [--quality-results <quality.json>] \
  [--run-id <id>] [--workflow <name>] [--head-sha <sha>]
```

该入口必须：

1. 读取 canonical `scripts/render_task_progress.py`；
2. 以 TaskRun 为生命周期 Owner；
3. 把 GitHub workflow/job/step、Quality result、Failure/Repair evidence 作为证据而不是第二 Owner；
4. 生成 `execution-progress@1`；
5. 同时生成 `skill-invocation-receipt@1`，绑定本 Skill 的当前 SHA256、TaskRun id、入口和确定性输出摘要；
6. 返回 `rendered_text`，宿主不得绕过 projection 自己升级整体状态。

## HARD_INVARIANT

- 单个 Tool、PR、merge、CI run、Stage 或最近一次成功动作不能单独宣布整个 Task 完成。
- 如果存在 TaskRun，只有 Completion Contract、最终 `COMPLETED/COMPLETED` checkpoint 和 required stages 一致时才能宣布 whole-task completion。
- expected child 尚未真实出现时必须保持 `PENDING/WAITING_FOR_EXPECTED_CHILD` 语义，不能表述为“CI 正在跑”。
- `RECOVERING` 只能来自与当前恢复动作绑定的 durable executor `RUNNING` evidence；仅有授权必须显示 `RECOVERY_READY`。
- 历史失败 Attempt 不能因为后续 GREEN 而消失；必须区分 recovered 与 unresolved。
- 状态回答必须明确是否需要用户介入；普通可恢复 RED 不等于 Human Gate。
- 没有有效 `skill-invocation-receipt@1` 时，宿主不得声称“已按本 Skill 执行状态投影”。

## 最低展示合同

存在相应证据时，输出至少包含：

- 整体状态；
- N/M；
- required stages；
- 当前 stage；
- 历史失败与已恢复失败；
- 当前未解决失败；
- Recovery READY/RUNNING；
- 是否需要用户介入与 blocker；
- 产品判定与执行/传输判定。

## 宿主边界

`.agents` / `.claude` adapter 和仓库 Hook 可以证明 Codex/Claude 类支持宿主已加载并调用本 Skill 的仓库入口。仓库代码**不能**自行拦截外部 ChatGPT 产品层 GitHub Connector，也不能仅凭本文件存在就声称当前 ChatGPT 对话已经调用本 Skill。外部宿主若不能执行仓库 CLI，必须显式标记为 host-integration-unverified，而不能伪造 invocation receipt。
