---
name: change-scope
description: 在任何可写变更前使用。生成或验证 Change Contract，确定 Target 类型、允许/禁止路径、硬不变量、Profile 与唯一写入者；不修改实现。
---

# Change Scope

1. 读取 `skill-system/core/change-contract.md` 和规则 Registry。
2. 先判断是 diagnosis、design、oracle-review、repair、migration、revert 还是 certification。
3. 对架构任务，要求 Architecture Decision 后才能批准可写合同。
4. 用 `change_contract_cli.py` 创建合同，不手写扩大范围。
5. Skill-only 变更必须禁止 `services/**`、`web/**`、`contracts/**`。
6. 输出合同路径和仍不确定的边界，不修改实现。
