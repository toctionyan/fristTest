# V20.5 可信边界替换记录

## 新增项

- `runtime_profile.py`：两服务共享的 protected profile 语义，Business 不再以 `APP_ENV` 决定信任模式。
- 会话 lease heartbeat、单调 fencing token，以及与 PostgreSQL checkpoint mutation
  同事务的 owner/token/expiry 行锁校验。
- Business protected profile 的 PostgreSQL 权威持久化边界。
- 文档索引共享 SQLAlchemy 队列、固定目标 doc_id、幂等 RAG upsert、claim token、lease、attempt 与超时回收。
- clean-release allow-list builder、只读的最终树/ZIP 完整性 verifier、内嵌 provenance 与独立 evidence bundle 哈希。
- Quality evidence 源码快照、逐文件 attestation 与定向回归非完成状态。

## 唯一职责

这些新增项只处理质量证据身份、protected profile 启动安全、跨 Worker 所有权、文档任务恢复以及最终发布制品完整性；不拥有业务事实、目标解析、授权或客户可见投影。

## 替换或删除项

- 替换“`--rerun-from` 的部分 PASS 可提升为整体收敛”的旧判定。
- 替换仅按 target/policy JSON 复用历史 PASS 的旧证据模型。
- 替换 Business Service 独立读取 `APP_ENV` 的旧生产判断。
- 替换固定 300 秒且无续租、无 fencing 的会话锁。
- 替换本机 SQLite 文档队列永久停留 `INDEXING` 的领取模型。
- 替换手工维护且不校验的 release 清单，以及直接信任工作区旧 `dist` 的发布方式。

## 删除证据

- Business `.env.example`、配置文档与架构策略均已删除 `APP_ENV` 作为必需配置；运行代码明确忽略旧变量。
- 最终收敛分支不再读取定向 Gate 的 PASS 作为完成资格，反例验证其只能产生 `TARGETED_REGRESSION_PASSED`。
- 旧锁完成/释放路径必须携带 fencing token；旧文档 worker 完成路径必须携带 claim token。
- clean-release 复制规则排除旧 `dist`、`.env`、数据库、依赖和缓存，前端只由当前源码和 lockfile 重建。

## 验证

反例覆盖：部分 Gate 假绿、evidence 篡改与源码漂移、production profile 降级、三个 verifier 降级、陈旧 fencing token、相同 worker 名复用旧 claim token、任务 lease 过期回收、有界重试、清单新增/删除/篡改、验证器自身字节码污染以及 ZIP 解包复验。最终仍需完整 quick required Gate 全量通过，定向回归不能替代最终全量运行。
### Clean-release runtime 路径闭包

发布复制只排除服务根目录下的运行态 `services/agent-service/runtime` 与 `services/business-service/runtime`。`src/agent_core/runtime` 是生产源码，`tests/runtime` 是架构自证的一部分，必须进入制品。构建在生成元数据后、打 ZIP 前，必须针对 staged tree 执行 Skill、版本与架构自检；自检失败不能由文件哈希 PASS 覆盖。
