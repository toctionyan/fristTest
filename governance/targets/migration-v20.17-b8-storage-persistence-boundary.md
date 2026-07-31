# 目标

- 目标 ID：migration-v20.17-b8-storage-persistence-boundary
- 变更标识：portable-migration-v20.17-b8-storage-persistence-boundary
- 执行上下文：local-change
- 目标类型：migration

把具体 StoreProvider 构造、数据库配置读取和 SQLAlchemy 实现从 storage 抽象包迁入 persistence 实现包，删除 storage 对 persistence、observability 与 runtime 的反向依赖，使 storage 退出主 SCC，同时保持全部仓库协议、事务语义、数据库 Schema 与 B1-B7 累计依赖债务成果。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/storage/__init__.py`, `services/agent-service/src/agent_core/storage/factory.py`, `services/agent-service/src/agent_core/storage/settings.py`, `services/agent-service/src/agent_core/storage/sqlalchemy_provider.py`, `services/agent-service/src/agent_core/storage/models.py`, `services/agent-service/src/agent_core/storage/migrations/README.md`, `services/agent-service/src/agent_core/persistence/__init__.py`, `services/agent-service/src/agent_core/persistence/database_settings.py`, `services/agent-service/src/agent_core/persistence/store_provider.py`, `services/agent-service/src/agent_core/persistence/sqlalchemy_provider.py`, `services/agent-service/app/services/agent_service.py`, `services/agent-service/app/services/readiness_service.py`, `services/agent-service/src/agent_core/observability/flow_debug.py`, `services/agent-service/src/agent_core/transaction/coordinator.py`, `services/agent-service/migrations/agent_db/env.py`, `services/agent-service/migrations/agent_db/versions/0001_initial_agent_schema.py`, `services/agent-service/tests/support/conversation_case_runner.py`, `services/agent-service/tests/support/runtime_support.py`, `services/agent-service/tests/context/test_scenario_topology.py`, `services/agent-service/tests/context/test_conversation_regression_suite_execution.py`, `services/agent-service/tests/context/test_context_bundle_runtime.py`, `services/agent-service/tests/context/test_semantic_goal_coverage_suite_execution.py`, `services/agent-service/tests/runtime/test_workflow_runtime.py`, `services/agent-service/tests/runtime/test_unsupported_capability_surface_binding.py`, `services/agent-service/tests/transactions/test_transaction_protocol.py`, `services/agent-service/tests/transactions/test_transaction_storage.py`, `services/agent-service/tests/architecture/test_sqlalchemy_transaction_repository.py`, `services/agent-service/tests/architecture/test_storage_persistence_boundary_scc.py`, `docs/architecture/V20_17_B8_STORAGE_PERSISTENCE_BOUNDARY.md`
- 新增抽象记录：docs/architecture/V20_17_B8_STORAGE_PERSISTENCE_BOUNDARY.md

## 禁止范围

不得修改 StoreProvider、TransactionScope、仓库方法、数据库表结构、事务状态机、幂等/授权、线程所有权、Trace 内容、Agent Loop、State Schema、Business Service、质量策略或依赖债务基线；不得用 importlib、动态 Service Locator 或兼容代理隐藏 storage 对 persistence 的运行时依赖；不得保留第二套 Provider 实现。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/migration-v20.17-b8-storage-persistence-boundary.json`
- 验收 ID：`STORAGE-PERSISTENCE-BOUNDARY-SCC-001`

storage 只包含端口、协议和值合同，不再导入 persistence、observability 或 runtime；具体 Provider、数据库设置和 SQLAlchemy 表定义只有 persistence 一份实现；全部生产、迁移和测试调用方使用新唯一入口；主 SCC 从 7 降到至多 6；storage、context、modules、kernel、resources、ledger、rag、utils 均保持退出。

## 基线

旧基线由 storage.factory/sqlalchemy_provider/settings 直接导入 persistence、observability 与 runtime，并在 storage 抽象包内拥有具体 Provider 实现，主 SCC 为 7；新的边界反例失败，B1-B7 累计回归继续通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复 Provider 所有权、调用方导入与迁移入口；若数据库行为变化、出现双实现或没有可度量依赖改善，停止并重新规划。
