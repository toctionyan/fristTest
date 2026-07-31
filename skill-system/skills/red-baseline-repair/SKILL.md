---
name: red-baseline-repair
description: 根因和 Oracle 已确认后使用。建立真实失败或差距基线，由唯一写入者做最小修复，定向诊断后再运行完整 Profile；不得修改裁判输入。
---

# Red Baseline Repair

1. 仅处理 approved Change Contract。
2. 修复前验证 Claim 与 Oracle 没有争议。
3. 只有 skill-implementer 可写。
4. 禁止修改 Target、Policy、Baseline、Evidence 和可信 Judge。
5. 定向重跑所有独立失败根，而不是只取第一个失败 Gate。
6. Issue 只有被完整 Judge 实际执行并通过后才能解决。
7. 新暴露下游失败属于进展，不得误判为停滞。
