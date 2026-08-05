# GitHub 失败自动治理与修复

该桥接把 GitHub Actions 与现有 `TaskRun`、独立 Quality Judge 和受限 Fixer 连接起来。部署到默认分支后，新的失败不再依赖截图或人工传递 Run ID。

## 自动链路

1. `quality`、`skill-self-validation` 或 `wp08-full-stack-certification` 结束且非成功；
2. `governed-ci-repair` 从 `workflow_run` 直接取得仓库、分支、提交、Run ID 和原 PR；
3. 下载原运行的 Artifact 与 Job 日志，并拒绝符号链接、越界路径和无诊断证据的盲目修复；
4. 生成脱敏 `failure-case.json`，建立与仓库、提交、Run ID、Run Attempt 和失败签名绑定的持久化 TaskRun；
5. 区分 `code_or_contract`、`environment`、`timeout`、`interrupted` 和 `production_diagnostic`；
6. 环境、密钥、网络、取消、Fork、未知问题及治理控制面问题只创建 GitHub Issue，不修改源码；
7. 只有同仓库、具有真实失败证据并能定位到 `services/`、`web/` 或 `contracts/` 现有文件的代码/合同失败才可进入自动修复；
8. 修复 Job 进入 `production-certification` Environment，复用已有模型变量和 Secret；
9. 受限 Fixer 只能修改冻结的精确文件集合，不得新增、删除或重命名文件，不得修改治理、Judge、Bridge、Secret 或通过 skip/xfail/弱断言降低测试要求；
10. 每轮修复形成独立 Git Commit，再由不持有模型密钥的验证环境执行 Quick Quality Loop 和确定性 Integration Quality Loop；
11. 确定性 Integration 只排除两个受保护的真实模型浏览器 Gate，这两个 Gate 仍由 WP-08 生产认证负责；
12. 最多执行 8 轮；相同失败签名连续出现两次、无源码变化、范围漂移或预算耗尽时立即停止并保留证据；
13. 验证通过后推送 `governed-repair/*` 分支并创建 Draft PR，不直接写 `main`、不自动合并；
14. 创建 Draft PR 后显式 dispatch 正常 `quality` 和 `skill-self-validation` 检查。正常 Quality 的 Static/Quick 仍运行；Integration 使用内部 `skip` profile，避免重复运行已经由 Repair Job 完成的确定性 Integration；
15. TaskRun 只有在源码变更、独立 Quick+Integration 通过并发布 Draft PR后才能完成。

## 配置权威

桥接复用 `production-certification` Environment：

- Secret：`PRODUCTION_MODEL_API_KEY`
- Variable：优先 `REAL_MODEL_CERTIFICATION_PROVIDER`、`OPENAI_MODEL`、`OPENAI_API_BASE`
- 兼容别名：`PRODUCTION_MODEL_PROVIDER`、`PRODUCTION_MODEL_ID`、`PRODUCTION_MODEL_API_BASE`

Provider 未显式配置时，只能根据官方 OpenAI 或 DeepSeek HTTPS Host 推断；非官方 Host、HTTP 地址和缺失配置都会阻断修复。

若 Environment 设置了 Required reviewers，模型修复 Job 会等待 Environment 批准；失败采集、分类、TaskRun 建立、证据上传和阻塞 Issue 创建仍会自动完成。

## 安全边界

- Fork PR 永远不能进入带 Secret 的修复 Job。
- 失败候选代码以无持久凭据方式检出；失败 Artifact、日志、源码注释和模型输出都按不可信数据处理。
- 模型密钥只存在于 Fixer Step；运行候选测试前会从环境中移除。
- 自动修复范围限于产品源码根，不能修改 `.github/`、`deployment/`、`scripts/`、`governance/`、`skill-system/`、Quality Judge、Repair Loop 或本桥接自身。
- 模型 Patch 必须通过精确 Allowlist、文件/行数预算、禁止弱化规则和 `git apply --check`。
- 正常 Push 和人工触发的 Quality 默认仍执行完整 Integration；`integration_profile=skip` 只由已完成确定性 Integration 的 Repair Bridge 内部 dispatch 使用。
- WP-08/WP-09 与 `production_closed` 不会被自动修复流程关闭。

## 部署与验证

`workflow_run` 只使用默认分支上的可信工作流定义，因此该桥接合并到 `main` 后才开始接管后续失败。合并前已经失败的旧 Run 不会被追溯执行；需要重新运行原工作流来产生新的受治理事件。

正式启用后应使用隔离的 Canary 分支制造一个可逆的产品代码失败，验证以下证据链：失败 Run → Ingest Artifact/TaskRun → 受限修复 → Quick+确定性 Integration → Draft PR → 正常 Status Checks。Canary PR 和分支不得合并到 `main`。
