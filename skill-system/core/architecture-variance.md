# Architecture Variance Record

当 `STRONG_DEFAULT`、`REFERENCE_PATTERN` 或当前 `PROJECT_BASELINE` 阻碍更优方案时，提交偏离记录：

- `variance_id`
- `affected_rule`
- `current_problem`
- `proposed_deviation`
- `why_better`
- `preserved_hard_invariants`
- `new_risks`
- `required_evidence`
- `rollback_plan`
- `expiry_or_review_date`
- `policy_delta`（需要改变项目 Baseline 时）

`HARD_INVARIANT` 不允许偏离。偏离记录必须由只读 Reviewer 审查，并由 Judge 校验所需证据。

Variance 不能只是一份解释文档。凡改变目录白名单、必需文件、旧路径退休、Owner 或尺寸边界，必须绑定机器可读 Architecture Policy Delta；Judge 使用“Baseline + approved Delta”生成本次有效策略。
