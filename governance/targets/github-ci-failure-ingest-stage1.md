# 目标

- 目标 ID：github-ci-failure-ingest-stage1
- 变更标识：github-ci-failure-ingest-stage1
- 执行上下文：local-change
- 目标类型：feature

建立 GitHub Actions 失败事件到治理控制面的只读接收桥，使 `quality` 与 `wp08-full-stack-certification` 失败后自动取得 Run ID、提交 SHA、日志、Artifact 和 PR 文件元数据，不再依赖人工截图或手工复制 Run ID。

## 允许范围

- `.github/workflows/governed-ci-failure-ingest.yml`
- `scripts/github_failure_ingest.py`
- `skill-system/tests/test_github_failure_ingest.py`
- `skill-system/tests/test_governed_ci_failure_ingest_workflow.py`
- `docs/operations/GITHUB_CI_FAILURE_INGEST_STAGE1.md`
- `governance/claims/github-ci-failure-ingest-stage1.json`
- `governance/targets/github-ci-failure-ingest-stage1.md`

## 禁止范围

- 不修改 Agent、Business Service 或前端运行时代码；
- 不读取 `production-certification` Environment Secrets；
- 不执行下载的日志或 Artifact；
- 不推送自动修复分支；
- 不创建自动修复 PR；
- 不合并 `main`；
- 不声明 WP-08、WP-09 或生产关闭完成。

## 验收条件

- 声明清单：`governance/claims/github-ci-failure-ingest-stage1.json`
- workflow_run 能接收两个目标工作流的失败结果；
- 日志和 Artifact 有读取上限、符号链接隔离和密钥脱敏；
- TaskRun 绑定 repository、workflow、run ID、attempt、commit SHA 与 failure signature；
- 只有同仓库、明确失败 Gate、明确证据路径的代码/合同问题进入 `REPAIR_READY`；
- 环境、超时、取消、审批、runner、fork 和未知错误均 fail-closed；
- 单元测试、静态合同测试与现有 Quick 质量门禁全部通过。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复 Stage-1 接收、分类、证据与 TaskRun 合同，不实现自动 Fixer。
