---
name: oracle-review
description: Claim、Requirement、测试 Oracle 或用户问题描述可能错误时使用。独立审查目标和测试，不修改候选实现。
---

# Oracle Review

- 保持只读。
- 只根据用户原始目标、硬不变量、Claim、Requirement、测试和当前行为判断。
- 合法输出包括 CLAIM_DISPUTED、ORACLE_DISPUTED、REQUIREMENT_AMBIGUOUS。
- 修订 Oracle 后作废旧 Baseline，并要求重新建立差距证据。
