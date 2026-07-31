# V20.17 B17i 生产执行交接边界

B17i 不增加新的客服运行时抽象。它关闭两个执行交接缺口：

1. `release-admission` 失败时必须留下独立、可下载、结构化且无密钥的结果，不能只依赖易丢失的 Job 日志；
2. 最终操作者必须有一份唯一 Runbook，明确仓库根目录、protected `main`、`production-certification` Environment、三个密钥、Workflow 输入、三类 Artifact 和最终 `production_closed` 验收条件。

准入证据的权威仍是 `release-workflow-admission@1`。B17i 只为该既有合同增加原子 JSON 持久化和 `always()` Artifact 上传，不改变其判定逻辑。

当前环境没有可访问 GitHub 仓库，因此 B17i 仍不得生成正式关单。
