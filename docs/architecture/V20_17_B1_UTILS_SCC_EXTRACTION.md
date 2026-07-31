# V20.17 B1：Utils 依赖环切口

## 新增项

- `agent_core.observability.flow_debug`：承接现有非权威 Graph Node 调试包装器。

## 唯一职责

- 在不改变节点业务结果的前提下，记录节点开始、结束、异常和状态合同验证信息。
- Trace 写入继续是 best-effort；该模块不能裁决业务事实、事务状态或展示结果。

## 替换或删除项

- 替换 `agent_core.utils.flow_debug` 的错误包归属。
- 删除旧文件 `services/agent-service/src/agent_core/utils/flow_debug.py`。
- `lifecycle.graph` 只把导入来源从 `utils` 改为 `observability`，包装器实现保持等价。

## 为什么不能继续放在 Utils

`flow_debug` 直接依赖 Lifecycle State Contract 和 Trace Storage，属于可观测性适配而不是依赖中立的通用工具。继续放在 `utils` 会形成 `utils -> lifecycle/storage`，同时 `lifecycle -> utils`，使通用工具包被卷入核心依赖 SCC。

## 删除证据

- 旧 Owner 文件必须不存在。
- `agent_core.utils` 内不得再导入 `agent_core.lifecycle` 或 `agent_core.storage`。
- Architecture Gate 必须在不修改债务基线的前提下，把主 SCC 从 14 个成员缩小到 13 个，`removed_members` 包含 `utils`。

## 验证

- `services/agent-service/tests/architecture/test_utils_scc_extraction.py`
- `architecture-convergence` Gate 显示 `PASS_WITH_DEBT / REDUCED`。
- 完整 Quick 回归保持 Runtime、Transaction、Frontend、Lifecycle Canary 和 Chromium Journey 通过。

## 未解决债务

本迁移只移除 `utils`。剩余 13 个核心包仍处于同一 SCC，继续由后续独立 Target 逐刀缩减；本记录不得被解释为依赖环已全部关闭。
