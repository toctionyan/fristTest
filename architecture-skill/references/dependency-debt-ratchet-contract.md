# 依赖债务棘轮合同

## 目标

架构 Gate 必须区分“当前没有新增问题”与“当前架构已经干净”。已存在的循环依赖可以作为有 Owner、有目标、有复审日期的暂存债务，但不能成为永久豁免。

## 判定

- 当前强连通分量与基线完全相同：`PASS_WITH_DEBT / UNCHANGED`。
- 当前分量是某个基线分量的严格子集，或基线分量已消失：`PASS_WITH_DEBT / REDUCED` 或 `PASS / RESOLVED`。
- 当前分量包含基线之外的新成员、多个基线分量合并，或出现未登记分量：`FAIL / VIOLATION`。
- 当前没有循环依赖：`PASS / RESOLVED`。

## 约束

1. 基线分量必须声明唯一 ID、成员、Owner、清零目标和复审日期。
2. 基线分量必须互不重叠，避免一个新环同时匹配多个豁免。
3. 依赖债务只能缩小，不能扩大；新循环必须先失败，再通过正式 Architecture Decision 处理。
4. 顶层 Gate 的进程退出码继续兼容现有 Quality Loop，但结构化结果必须同时公开 `architecture_status` 与 `architecture_debt_status`。
5. 功能测试通过不能覆盖架构债务；确定性 Runtime 通过也不能被描述为真实模型认证。
