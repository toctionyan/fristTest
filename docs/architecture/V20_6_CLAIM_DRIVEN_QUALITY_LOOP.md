# V20.6 Claim-Driven Quality Loop

## 新增项

- 机器可读质量 claim manifest，逐条声明风险、最低模式、证据 Gate 和证据引用。
- repair target 与 protected certification target 分离。
- repair target 要求红 baseline、真实范围内修改与逐 claim 红转绿；CI 延续 source claim 身份。
- 独立 repair orchestrator 只编排 Issue/fixer/回归，质量控制器保留最终 Judge 权。
- protected artifact 与普通 candidate artifact 分级。

## 唯一职责

质量控制器只根据冻结 target、claim manifest 和 Gate 结果判定声明是否被直接证明；它不猜测未声明的完成度。

## 替换或删除项

替换“人工最低模式 + 全 Gate PASS 即收敛”的完成语义。保留定向回归，但它只能作为诊断证据。

## 删除证据

- 低于 claim 所需模式的结果标记为 `INSUFFICIENT_MODE`。
- 未映射 Gate、未知 Gate、低声明模式在 Gate 执行前即失败。
- protected artifact 不再接受 quick/local/npm existing-node-modules 证据。

## 验证

见 `test_quality_loop_controller.py`、`test_quality_loop_governance.py`、`test_clean_release_integrity.py`、Business PostgreSQL 方言反例和 fencing 反例。

## 直接证据合同

- 文件引用必须是当前源码快照中的安全相对路径。
- Python 反例必须引用真实存在的 pytest selector，并在本轮 JUnit 中执行通过。
- `gate-log:<id>` 只能引用该 claim 的 required Gate，且该 Gate 必须本轮 PASS。
- P0/P1 不允许用文档存在、历史日志或未执行选择器代替直接证据。
- evidence kind、Gate 类别和 required mode 必须匹配，控制器在执行 Gate 前 fail-closed。
