# ADR

只有改变跨模块或跨领域长期边界的决策才新增 ADR。正常新增业务能力必须只修改对应 `AgentModule` 与 Business Service 领域实现，不应修改 Kernel。
