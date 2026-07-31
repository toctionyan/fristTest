# V20.17 B14b Grounded Projection Quarantine

## 问题

State Schema v2 的计划权威是 `frozen_plan_definition` 与 `plan_run`。`grounded_execution_plan` 只是二者的派生兼容投影，不拥有计划结构或执行进度。

B14a 后审计发现，checkpoint 迁移边界会原样保留 Schema v2 中已有的 `grounded_execution_plan`。部分 Runtime 消费者又优先读取该投影。因此，过期、损坏或伪造的投影可能与正式定义/运行证据冲突，形成事实上的第二套 Plan 权威。

## 修复边界

- 每次 State Schema v2 规范化都从 `frozen_plan_definition + plan_run` 重新派生 `grounded_execution_plan`；
- 没有完整正式权威对时，丢弃持久化的孤立投影；
- 不改变计划定义、执行轨迹或业务状态；
- 不恢复 `workflow_plan`，不新增第三套 Plan Owner；
- 正式定义或运行证据自身损坏时，投影失败关闭，不以旧投影兜底。

## 验收

- 伪造的投影 ID、状态、Goal、Step 与工具名均不能越过 checkpoint 规范化边界；
- 完整权威对可确定性重建兼容投影；
- 孤立投影被清空并写入迁移报告；
- B14a、State Schema v2、Plan Definition/Run 分离和全量回归保持通过。
