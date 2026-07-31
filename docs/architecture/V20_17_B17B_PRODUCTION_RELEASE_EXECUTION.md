# V20.17 B17b — Production Release Execution

## 目标

将 B17a 的唯一生产认证权威接入一个可实际运行的受保护发布入口。正式发布必须在不可变 GitHub commit 上，以官方模型密钥、owned PostgreSQL/pgvector 和锁版本 Playwright Chromium 实时执行完整 Release Quality Loop；只有同一次运行的 `production-certification-bundle@1` 与全部代码 Gate 均 PASS，才允许构建并上传 protected clean-release。

## 新增项

- `scripts/run_production_release.py`：唯一生产发布执行器，顺序运行 Release Quality Loop、验证生产 Bundle 维度、再构建 protected clean-release。
- `.github/workflows/release.yml`：仅使用官方模型 Secret 的正式生产认证工作流。
- `.github/workflows/integration-diagnostic.yml`：保留确定性模型桩诊断，但禁止执行 Release 模式或构建发布包。
- B17b 反例：验证旧独立 Gate、模型桩发布路径、非 PASS Summary 和缺失 CI provenance 均 fail closed。

## 唯一职责

B17b 只负责外部执行环境和发布顺序，不修改 Agent 业务语义、事务协议、数据库实现或浏览器旅程内容。组件真实性仍由 B17a 合同裁决。

## 替换或删除项

- 原 `release.yml` 中的 deterministic model stub protected-release 路径退出正式发布职责。
- 确定性模型工作流迁移为 `integration-diagnostic.yml`，只能运行 Integration 诊断。
- CI Release Claim 不再引用旧的独立 real-model/browser Gate，只引用唯一 `production-certification-bundle`。

## 删除证据

- 正式 `release.yml` 不包含 `deterministic-ci-key`、`tests.integration.model_stub` 或直接调用 `build_clean_release.py`。
- 只有 `run_production_release.py` 能在生产工作流中触发 protected artifact 构建。
- `integration-diagnostic.yml` 不包含 `--mode release`、`build_clean_release.py` 或 `protected-release`。

## 验证

- 旧 B17a 包在 B17b 六条反例上 6/6 失败。
- 修复后 workflow、CI Claim、执行计划、非 PASS Summary 拒绝和 CI provenance 校验全部转绿。
- 缺官方模型 Key、签名 Key、Docker、Node/npm、GitHub commit/run identity、生产 Bundle PASS 中任一项时，不构建 protected artifact。
- 执行计划和结果只记录模型凭据指纹，不输出模型 Key 或证据签名 Key。
