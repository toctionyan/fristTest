# 当前架构概览

> 唯一当前架构权威见 [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md)。本页只提供目录级摘要。

## 边界

- **LLM**：理解最近对话、已见结果和用户纠正，只提出能力与参数候选。
- **lifecycle/**：唯一会话状态、Graph、Node、Loop、执行分类和最终路由。
- **transaction/**：唯一 Draft、Grant、Attempt、Receipt、对账和结构化交互边界。
- **presentation/**：唯一客户可见投影、合同与发布校验边界。
- **agent_modules/**：固定能力、领域集成、领域展示与模块测试的垂直闭环。
- **Business Service**：业务事实、权限、资格、状态机、幂等和最终写入的权威。

## 目录就是边界

- 只有 `composition/` 可以安装具体领域模块。
- 只有 `services/agent-service/runtime/` 与 `services/business-service/runtime/`
  是运行态产物根目录，不属于源码、测试夹具或发布物。路径分类必须使用这两个从
  工作区根锚定的完整前缀；目录名本身不能作为排除依据，因为
  `src/agent_core/runtime/` 是生产源码而 `tests/runtime/` 是测试源码。
- `governance/architecture-policy.json` 是唯一当前治理配置。
- 新增抽象必须替换或删除旧抽象；无替代关系时不新增。

详见 `CURRENT_ARCHITECTURE.md`、`TARGET_ARCHITECTURE.md`、`CONVERGENCE_MATRIX.md` 和 `architecture-skill/SKILL.md`。
