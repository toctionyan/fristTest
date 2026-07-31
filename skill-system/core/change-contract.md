# Change Contract

每次可写变更必须有机器可读合同，至少包含：

- `change_id`
- `target_kind`
- `goal`
- `allowed_paths`
- `forbidden_paths`
- `invariants`
- `required_profiles`
- `writer_role`
- `review_roles`
- `review_attestations`
- `decision_record`
- `variance_records`
- `architecture_policy_delta`（改变项目 Baseline 时）
- `baseline_policy_id`（改变项目 Baseline 时）
- `verification`
- `status`

## Target 类型

- `diagnosis`：只诊断，可零修改；
- `design`：比较方案并形成决策，可只改治理文档；
- `oracle-review`：审查 Requirement、Claim 和测试，不改实现；
- `repair`：确认根因后从红到绿，不改变正式架构形状；
- `migration`：受控迁移，允许只读 Shadow 和有时限兼容层；
- `revert`：回到最后可信状态；
- `certification`：验证不可变候选，不进行修复。

只有 `repair`、`migration`、`revert` 要求实际候选变化。`diagnosis` 和 `oracle-review` 可以输出 `NO_CODE_CHANGE_REQUIRED`。

## 架构迁移附加要求

改变当前目录、必需文件、Owner 或旧路径时，Migration 必须同时绑定：

- 三方案 Architecture Decision；
- 至少一个 Architecture Variance；
- 与当前 `policy_id` 匹配的 Architecture Policy Delta；
- 红基线或可量化差距；
- Shadow/Cutover/Rollback/Cleanup 验收；
- 最终 Baseline Promotion 计划。

## 状态与写入权

合同只能通过 `change_contract_cli.py` 进入：

```text
draft → approved → implementing → review → verified → closed
```

- 实现者不能直接修改合同、审查记录或验证证据；
- Review 记录必须绑定仓库内证据及 SHA256；
- `verify` 必须运行合同声明的全部 Profile，并记录当前源码和有效 Architecture Policy 身份；
- `close` 只接受已经验证且源码未漂移的合同。
