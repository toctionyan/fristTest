# GitHub 失败自动治理与修复

该桥接把 GitHub Actions 与现有 `TaskRun`、独立 Quality Judge 和受限 Fixer 连接起来，目标是消除截图和手工传递 Run ID。

## 自动链路

1. `quality` 或 `wp08-full-stack-certification` 结束且非成功；
2. `governed-ci-repair` 从 `workflow_run` 直接取得仓库、分支、提交、Run ID 和原 PR；
3. 下载原运行的 Artifact 与 Job 日志，生成脱敏 `failure-case.json`；
4. 建立持久化 TaskRun，区分 `code_or_contract`、`environment`、`timeout` 和 `production_diagnostic`；
5. 环境、密钥、网络及生产外部阻塞只创建 GitHub Issue，不修改源码；
6. 同仓库的代码/合同失败进入 `production-certification` Environment，使用已有模型变量和 Secret；
7. 受限 Fixer 只能修改失败证据冻结的文件，不得修改治理、Judge、Bridge、Secret 或通过 skip/xfail 弱化测试；
8. 每次修复后由现有 Quick Quality Loop 独立验证，最多 8 轮；相同失败签名连续出现两次立即停止；
9. 验证通过后推送 `governed-repair/*` 分支并创建 Draft PR，不直接写 `main`、不自动合并；
10. TaskRun 只有在源码发生变更、独立验证通过并发布 Draft PR 后才能完成。

## 配置权威

桥接复用 `production-certification` Environment：

- Secret：`PRODUCTION_MODEL_API_KEY`
- Variable：优先 `REAL_MODEL_CERTIFICATION_PROVIDER`、`OPENAI_MODEL`、`OPENAI_API_BASE`
- 兼容别名：`PRODUCTION_MODEL_PROVIDER`、`PRODUCTION_MODEL_ID`、`PRODUCTION_MODEL_API_BASE`

若 Environment 设置了 Required reviewers，自动修复 Job 会等待 Environment 批准；失败采集、分类和 Issue 创建仍会自动完成。

## 安全边界

- Fork PR 永远不能进入带 Secret 的修复 Job。
- 日志、源码注释和模型输出均按不可信数据处理。
- 不修改 `quality_loop.py`、`repair_loop.py`、Skill 控制面、治理记录或本桥接自身。
- 不输出 Secret，不允许 HTTP 模型地址，不允许非官方 OpenAI/DeepSeek Host。
- WP-08/WP-09 与 `production_closed` 不会被自动修复流程关闭。
