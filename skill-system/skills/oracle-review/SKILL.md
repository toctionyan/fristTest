---
name: oracle-review
description: Claim、Requirement、测试 Oracle 或用户问题描述可能错误时使用。独立审查目标和测试，不修改候选实现。
---

# Oracle Review

- 保持只读。
- 只根据用户原始目标、硬不变量、Claim、Requirement、测试和当前行为判断。
- 合法输出包括 CLAIM_DISPUTED、ORACLE_DISPUTED、REQUIREMENT_AMBIGUOUS。
- 修订 Oracle 后作废旧 Baseline，并要求重新建立差距证据。


## 强制触发条件

出现以下任一情况，必须先输出只读 Oracle Review，不能直接进入 Repair：

- 真实模型完成了用户效果，但内部 Goal 数量、ID、依赖图或 Tool 顺序与测试预设不同；
- 失败只能通过增加自然语言分类规则、关键词规则或 Prompt 特例来消除；
- 测试要求唯一内部表示，但用户目标允许多种安全可执行表示；
- Runtime、Verifier 与 Oracle 对同一语义关系给出相互冲突的要求。

Oracle Review 必须分别判断：用户效果是否遗漏、是否捏造、对象是否错误、安全不变量是否破坏，以及差异是否仅属于内部表示。仅内部表示不同必须输出 `ORACLE_DISPUTED`，不得修改运行时迎合测试。
