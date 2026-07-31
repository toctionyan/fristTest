# Stop Contract

允许的终态：

- `CONVERGED`
- `NO_CODE_CHANGE_REQUIRED`
- `BLOCKED_BY_ENVIRONMENT`
- `ORACLE_REVIEW_REQUIRED`
- `ARCHITECTURE_REPLAN_REQUIRED`
- `REVERT_RECOMMENDED`
- `STOPPED_MAX_REPAIRS`

`CONVERGED` 必须同时满足：范围、Claim、Required Profiles、Reviewer、当前 Evidence、Judge 身份和生产代码保护全部通过。

Stop Guard 还必须确认：

1. 合同状态为 `verified` 或 `closed`；
2. adversarial reviewer 与 deterministic release Judge 均为 PASS；
3. 验证证据文件及哈希仍然一致；
4. 当前 Skill 源码指纹与验证时相同；
5. 合同不能通过手工改状态绕过 `attest-review → verify → close`。

八轮只是资源上限，不是完成标准。无有效进展应提前停止；达到上限仍失败必须明确未收敛。
