# Stage 2B1：可信 Typed Goal Evidence Ingress

## 决策

将可信 Typed Goal 输入证据、评估时间和 issuer 校验器作为应用组合层的显式运行时依赖，接入真实的 pretool shadow 调用。

## 边界

- checkpoint、用户输入和模型输出不是 trust root。
- resolver 的输入形状采用 closed-world 校验，缺失、异常或校验器不完整时 fail-closed。
- raw evidence 不进入 observability metadata。
- 本阶段只影响 Typed Goal shadow coverage，不改变 legacy selection、Permit、dispatch、事务写入或 production closure。

## 回滚

移除 runtime dependency seam、resolver forwarding 和对应回归测试即可；由于本阶段没有执行权提升，不需要事务或业务数据回滚。

## 新增项

- 应用组合层的可信证据 resolver。
- Lifecycle 到 pretool shadow 的显式依赖传递。

## 唯一职责

该记录只定义 Stage 2B1 的可信 ingress 边界，不拥有业务事实、能力选择、Permit 或事务执行权威。

## 替换或删除项

替换真实 pretool shadow 调用中缺少可信 evidence ingress 的空依赖路径；不删除 legacy selection 或执行链。

## 删除证据

只有在后续受治理迁移确认所有调用方完成切换后，才可删除旧的 shadow-only ingress 兼容路径。

## 验证

通过 resolver fail-closed、runtime dependency admission、pretool-to-typed-coverage forwarding 和 Agent/Business Product Quick 回归验证。
