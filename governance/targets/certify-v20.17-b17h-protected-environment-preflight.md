# 目标

- 目标 ID：certify-v20.17-b17h-protected-environment-preflight
- 变更标识：certify-v20.17-b17h-protected-environment-preflight
- 执行上下文：local-change
- 目标类型：certification

关闭 B17g 受保护发布 Job 在安装完整 Python 依赖、前端依赖和 Chromium 之后，才发现生产密钥缺失、占位、端点错误或锁定基础工具版本不匹配的晚失败边界。B17h 必须在昂贵安装和任何模型调用之前运行标准库预检，并产生不含密钥值的可下载失败证据。

## 允许范围

- 允许变更路径：`.github/workflows/release.yml`, `deployment/ci/release-toolchain-lock.json`, `scripts/protected_environment_preflight.py`, `scripts/release_toolchain_contract.py`, `services/agent-service/tests/runtime/test_b17h_protected_environment_preflight.py`, `services/agent-service/tests/runtime/test_b17g_production_execution_readiness.py`, `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`, `docs/architecture/V20_17_B17H_PROTECTED_ENVIRONMENT_PREFLIGHT.md`, `docs/operations/B17H_PROTECTED_RELEASE_HANDOFF.md`, `governance/claims/certify-v20.17-b17h-protected-environment-preflight.json`, `governance/targets/certify-v20.17-b17h-protected-environment-preflight.md`, `governance/active-change.json`, `README.md`, `CHANGELOG.md`, `B17H_STAGE_SUMMARY.json`, `PHASE_CANDIDATE_NOTICE.md`, `PHASE_CANDIDATE_MANIFEST.json`, `release/MANIFEST.json`, `release/VALIDATION_REPORT.md`
- 新增抽象记录：`docs/architecture/V20_17_B17H_PROTECTED_ENVIRONMENT_PREFLIGHT.md`

## 禁止范围

不得修改客服 Agent 的语义理解、Prompt、Capability、事务协议、业务规则、数据库实现、模型路由或 RAG 行为；不得在日志、JSON、Artifact 或异常中输出密钥值；不得用本地模拟的 GitHub 环境、占位凭证或依赖无关测试冒充真实生产认证；不得生成 `production_closed`。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/certify-v20.17-b17h-protected-environment-preflight.json`
- 验收 ID：`V20-17-B17H-PROTECTED-ENVIRONMENT-PREFLIGHT-001`

受保护 Job 必须在 `uv sync`、`npm ci` 和 Playwright 安装之前运行 `protected-environment-preflight@1`。预检必须验证 GitHub 受保护运行身份、锁定 Python/Node/npm、Docker/Git、官方聊天端点、模型与 Provider 一致性、Embedding HTTPS 端点、模型与维度、聊天密钥、Embedding 密钥和至少 32 字节的 Evidence 签名密钥。缺失环境返回 `BLOCKED_BY_ENVIRONMENT`，非法/占位输入返回 `FAIL`；两类结果均不得暴露密钥，并必须进入 `always()` 上传的生产证据 Artifact。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复受保护环境预检、Workflow 顺序、供应链锁和对应反例，不修改客服产品运行时。

## 基线

红基线：B17g 在安装锁定 uv、Agent/Business 依赖、前端依赖和 Chromium 后，才由正式发布控制器发现生产密钥、端点或签名密钥问题。缺失 Environment 配置会浪费运行时间，而且没有独立、早期、可下载的脱敏预检证据。
