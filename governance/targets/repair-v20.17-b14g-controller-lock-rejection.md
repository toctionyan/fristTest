# 目标

- 目标 ID：repair-v20.17-b14g-controller-lock-rejection
- 变更标识：repair-v20.17-b14g-controller-lock-rejection
- 执行上下文：local-change
- 目标类型：repair

质量控制器正确拒绝并发或非空证据目录后，必须返回稳定的机器可读失败结果，不能在冲突处理分支中因未定义版本函数再次崩溃。

## 允许范围

- 允许变更路径：`scripts/quality_loop.py`
- 新增抽象记录：无

## 禁止范围

不得放宽独占锁、证据不可变性、可信签名或并发拒绝规则；不得写入被拒绝运行的证据目录。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14g-controller-lock-rejection.json`
- 验收 ID：`V20-17-B14G-CONTROLLER-LOCK-001`

同一反例必须在旧实现上因二次 NameError 失败，修复后返回 CONCURRENT_RUN_REJECTED、工作区版本和 quality-controller-lock 结构化结果；标准测试、架构与既有产品 Gate 不得下降。

## 基线

基线反例：并发锁拒绝已经生效，但 main() 的 QualityRunConflictError 分支调用不存在的 `_workspace_version`，导致正确的拒绝被二次 NameError 覆盖。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复控制器冲突结果构造，不修改锁语义。
