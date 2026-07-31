
# Oracle Review Contract

实现者发现 Claim、Requirement 或测试 Oracle 可能错误时，不得自行修改它们，也不得继续强行适配。

合法状态：

- `CLAIM_DISPUTED`
- `ORACLE_DISPUTED`
- `REQUIREMENT_AMBIGUOUS`
- `ORACLE_REVIEW_REQUIRED`

Oracle Reviewer 只读取用户原始目标、硬不变量、Claim、Requirement、测试和当前行为。修订 Oracle 后，旧 Baseline 自动失效，并必须重新建立差距证据。
