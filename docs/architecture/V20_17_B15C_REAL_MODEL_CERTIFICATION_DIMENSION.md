# V20.17 B15c：真实模型认证最终维度

## 目标

让 Quality Loop 只在同一次 release 运行中完成真实模型 base smoke、语义原型、完整生命周期、配置模型浏览器会话和固定种子 Campaign，并确认三份 Provider 身份一致后，才把 `quality_dimensions.real_model_certification` 标记为 PASS。

## 红基线

现有 `_quality_dimensions` 无论 release 运行中的真实模型 Gate 是否全部通过，都固定输出 `NOT_DECLARED`。因此系统既无法形成真实认证结论，也没有一个统一位置拒绝 Gate 跳过、环境阻断或 Provider 身份分裂。

## 新增项

新增真实模型认证维度聚合器。它只消费当前 release run 的 Gate 结果和结构化输出，不读取历史 PASS，不发起额外模型调用，也不复制 Quality Loop 的依赖调度。

## 唯一职责

聚合器只判断当前 release 证据是否足以声明真实模型认证。模型身份验证由 B15a 所有，语义响应认证由 B15b1 所有，完整生命周期调用轨迹认证由 B15b2 所有，浏览器会话和 Campaign 仍由各自 Gate 所有。

## 替换或删除项

替换 `_quality_dimensions` 中永久写死 `NOT_DECLARED` 的占位实现。删除“任一真实模型 Gate 单独 PASS 即可推断整体认证”以及“不同 Provider/模型的三份证据可以拼接”的可能。

## 删除证据

- quick/integration 模式仍只能输出 NOT_DECLARED。
- release 模式缺少任一 required real-model Gate、存在 FAIL/SKIPPED 或身份元数据缺失时输出 FAIL。
- 任一 Gate 为 BLOCKED_BY_ENVIRONMENT 时输出 BLOCKED_BY_ENVIRONMENT。
- 三个身份认证 Gate 的 provider、endpoint、model 或凭据指纹不一致时输出 FAIL。
- 只有五个 Gate 全 PASS 且身份一致时输出 PASS，并仅公开非敏感身份摘要。

## 验证

1. 定向反例覆盖合法 release PASS、quick 禁止提升、环境阻断、Gate 跳过、缺失 Gate、缺失身份和身份不一致。
2. 对抗桥纳入标准运行时反例，旧固定 NOT_DECLARED 实现正式 Baseline 必须 FAILED，修复后同一声明必须 VERIFIED。
3. 当前环境没有真实 Key 和 Playwright，因此代码声明可验证，但真实模型最终认证状态仍必须保持 BLOCKED_BY_ENVIRONMENT/NOT_DECLARED，不能生成生产认证结论。
