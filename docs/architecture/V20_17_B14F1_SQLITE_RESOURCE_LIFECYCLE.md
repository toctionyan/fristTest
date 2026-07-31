# V20.17 B14f1 SQLite Resource Lifecycle

SQLite 连接由创建它的 Provider/Service/operation 明确拥有并关闭：缓存重置先 close 再 clear；Agent shutdown 关闭完整 StoreProvider 与 Checkpointer；DocumentService 关闭 job repository/object store；local sparse RAG 使用上下文管理器包围每次短连接操作。禁止用 warning filter 隐藏泄漏。

## B14f1a 补充：SQLite context manager 不负责 close

Python `sqlite3.Connection` 的 `with connection:` 只管理事务提交/回滚，不会关闭连接。
Preprod 诊断脚本与 B14e 对抗夹具统一改为 `contextlib.closing(sqlite3.connect(...))`，
并以连接关闭计数反例证明查询完成后资源已经释放。

## B14f1b 补充：生命周期解释器可移植性

完整生命周期 Canary 不再假定两个服务目录都存在 `.venv/bin/python`。
解释器按 `QUALITY_AGENT_PYTHON` / `QUALITY_BUSINESS_PYTHON`、项目内锁定虚拟环境、
当前执行解释器的顺序解析；无可用解释器时给出明确错误。

## B14f1c 补充：真实浏览器运行时解析

Browser Journey 由验证脚本解析 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`、
`CHROMIUM_EXECUTABLE_PATH` 或系统 Chromium，并将路径显式传入 Playwright。
浏览器诊断数据库查询同样使用 `contextlib.closing`，避免失败诊断路径再次泄漏连接。
