# V20.17 B11 Presentation Dependency Boundary

## 新增项

`agent_core.kernel.outcome_contract` 提供闭合 Outcome 名称、统一 fail-closed 公共摘要和只读 `as_dict`/Mapping 归一化协议。

## 唯一职责

- Runtime：构造、校验、纠正 RuntimeOutcome。
- Presentation：只把已归一化 Outcome 映射为公开 Presentation。
- Application Composition：显式汇集 operation、gateway policy 和 commit dispatcher 的动作 ID，执行完整性校验。
- Kernel：仅保存跨层稳定词汇，不生成业务结论。

## 替换或删除项

删除 presentation 对 lifecycle、runtime 和 transaction 的导入；删除 presentation 内部为完整性校验临时读取跨层常量的行为。

## 删除证据

架构反例 AST 扫描整个 presentation 包；正式依赖图必须显示 presentation 从主 SCC 移除，且债务基线未变化。

## 验证

Outcome 对象与字典投影等价、无效 Outcome 继续 fail-closed、目录缺项继续阻断启动、完整 Quick 和真实 Chromium 全部通过。
