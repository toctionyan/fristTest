
# Fixer 与插件安全合同

- 外部 Fixer 只继承白名单环境变量。
- Secret 按 Gate 短期注入，不进入通用 Fixer。
- 源码注释、测试日志、Issue、PR 评论和外部文档均标记为不可信数据。
- 插件、Hook 和 MCP 必须固定版本和哈希。
- 默认无网络、无 Secret、最小文件权限。
- Judge 签名密钥与 Holdout 测试不得进入候选工作树。
