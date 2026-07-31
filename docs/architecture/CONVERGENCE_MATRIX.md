# V20 收敛矩阵

| 旧位置 / 旧概念 | 处理 | 当前唯一 Owner | 删除或替换证据 |
|---|---|---|---|
| `graphs/` + `nodes/` + `loop/` | 合并 | `agent_core/lifecycle/` | 旧三目录不存在；Graph、Node、Loop 都在 lifecycle |
| 根级 `state.py`、`agent_loop_protocol.py` | 合并 | `agent_core/lifecycle/` | State 与协议同属生命周期 |
| `presentation.py` + `presentation_contracts/` + `presentation_adapters/` + 根级展示辅助 | 合并 | `agent_core/presentation/` | 旧平行展示路径不存在 |
| 根级 `action_gateway.py`、`interaction_contract.py` | 合并 | `agent_core/transaction/authority.py`、`interaction.py` | 事务授权/交互没有根级副本 |
| 根级 `operation_capability.py`、`target_resolution.py`、`capabilities.py` | 归位 | `operations/`、`resources/`、`kernel/` | 合同按 Owner 归位 |
| `bootstrap/` | 改名 | `composition/` | 只有 composition 允许导入具体模块 |
| `assessments/` | 合并 | `operations/assessment*.py` | 资格核验是操作前只读证据 |
| `evaluation/` | 移出 Core | `app/services/evaluation_cases.py` | Console 演示数据不是 Kernel |
| V3/V4 多守卫与多治理配置 | 删除 | `architecture-skill/scripts/verify_convergence.py` + `governance/architecture-policy.json` | 单一验收器与单一当前策略 |
| 隐式 `.env` 依赖、模型硬编码、Vite/Business Runner 配置加载不一致 | 收敛 | 四份 `.env.example` + `config.py` + `CONFIGURATION.md` | 模板覆盖运行时变量；模型设置外置；各启动入口显式加载本地 `.env` |
| 反复重跑同一 gate、版本库内可变 evidence、口头目标 | 替换 | `quality_loop.py` + `quality-loop-policy.json` + `quality-loop-target.md` | 单次 DAG 验证、evidence 外置、repair-plan 定向回归；local baseline 对源码快照，范围外路径与无替换记录的新生产源码会拒绝验证 |
| 多意图的隐式连续工具调用、仅 JSON 的强上下文清单 | 收敛 | `lifecycle/workflow_*.py` + schema-v2 Conversation Regression Suite | L0/L1/L2 只记录编排步骤；84 个 case 逐 user turn 驱动真实 Lifecycle Graph，并断言 Trace、Permit、Workflow、Draft、BusinessPort 与公开结果；候选 JSON 不再自证 |
| 未正式暴露的图流式能力 | 收敛 | 认证的 `POST /api/chat/stream` + `ConversationTurnService.stream` | 普通用户仅接收 `start`、公共更新、`result`、`end`；内部图更新只在既有 debug 权限下可见 |

## 新增抽象规则

新增抽象必须填写 `architecture-skill/templates/new-abstraction-record.md`，明确“替换或删除什么”。`local-change` target 还要冻结 `允许变更路径` 和 `新增抽象记录`；baseline 后新增生产 Python 文件没有该记录时，控制器拒绝验证。没有替换或删除项，默认拒绝新增。
