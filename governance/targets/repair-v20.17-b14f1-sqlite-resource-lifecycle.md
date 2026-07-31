# 目标

- 目标 ID：repair-v20.17-b14f1-sqlite-resource-lifecycle
- 变更标识：portable-repair-v20.17-b14f1-sqlite-resource-lifecycle
- 执行上下文：local-change
- 目标类型：repair

关闭标准回归中暴露的 SQLite ResourceWarning。StoreProvider 缓存重置必须先关闭旧 Provider；Agent 服务关闭必须释放完整 StoreProvider 与 Checkpointer；DocumentService 和本地稀疏 RAG 必须显式关闭自己打开的资源。

## 允许范围

- 允许变更路径：`services/agent-service/src/agent_core/persistence/store_provider.py`, `services/agent-service/src/agent_core/rag/providers/local_sparse_provider.py`, `services/agent-service/app/services/agent_service.py`, `services/agent-service/app/services/document_service.py`, `services/agent-service/app/main.py`, `scripts/verify_full_lifecycle_canary.py`, `scripts/verify_product_browser_journey.py`, `services/agent-service/frontend/e2e/product_journey.mjs`, `services/agent-service/frontend/e2e/strong_context_journey.mjs`, `services/agent-service/frontend/e2e/strong_context_campaign_journey.mjs`, `services/agent-service/scripts/verify_preprod_full_lifecycle.py`, `services/agent-service/tests/runtime/test_b14e1_compatibility_projection_exit.py`, `services/agent-service/tests/runtime/test_b14f1_sqlite_resource_lifecycle.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B14F1_SQLITE_RESOURCE_LIFECYCLE.md`
- 新增抽象记录：无

## 禁止范围

不得改变业务事实、事务状态机、Plan 权威、RAG 访问控制、数据库 Schema 或 State Schema；不得把连接泄漏隐藏为 warning filter；不得新增跨包依赖循环。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b14f1-sqlite-resource-lifecycle.json`
- 验收 ID：`V20-17-B14F1-SQLITE-RESOURCE-001`, `V20-17-B14F1B-CANARY-PYTHON-001`, `V20-17-B14F1C-BROWSER-RUNTIME-001`

资源生命周期反例必须从失败转为通过；Agent 标准全量套件不得再产生 SQLite `ResourceWarning: unclosed database`；架构保持 PASS / RESOLVED / 0 cycles。完整生命周期脚本必须从质量环境变量、项目虚拟环境或当前解释器中解析可用 Python，不得硬依赖项目内 `.venv`。真实浏览器旅程必须按“声明路径 → Playwright 锁版本地浏览器 → 系统 Chromium”解析可执行文件并传给 Playwright；受托管策略阻断时必须返回环境阻断，不得误判为产品失败或绕过系统策略。

## 基线

红基线：B14e 标准 Agent 测试 680 项通过但产生 4 条 SQLite unclosed database ResourceWarning（总计 5 条 warning，其中 1 条为 ZIP 反例的预期 UserWarning）。缓存重置和服务关闭路径没有释放全部资源，本地 RAG 打开临时 SQLite Store 后也没有显式关闭。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复资源所有权和关闭边界，不使用 warning ignore/filter 伪造清零。
