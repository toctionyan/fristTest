# V20.17 B17a — Production Certification Authority

## 目标

建立唯一生产认证权威：真实模型、owned PostgreSQL/pgvector、真实 Playwright 浏览器必须由同一个实时控制器执行，并绑定同一认证会话与同一源码指纹。任何独立绿灯、历史证据复用、会话不一致、环境阻断或认证期间源码变化都不能生成 production closed 结论。

## 新增项

- `scripts/production_certification_contract.py`：定义生产认证会话、三组件证据合同和最终 Bundle 校验。
- `scripts/verify_production_real_model_bundle.py`：将 B15 真实模型 Bundle 纳入生产会话。
- `scripts/verify_production_postgres_bundle.py`：在一个 owned pgvector 实例上执行迁移、pgvector、fencing、公开 HTTP 重启/并发/幂等恢复认证。
- `scripts/verify_production_browser_bundle.py`：执行配置模型的强上下文浏览器旅程与 campaign，并记录浏览器二进制指纹和版本。
- `scripts/verify_production_certification_bundle.py`：顺序实时启动三类认证；不读取历史证据路径。
- Release Quality Loop Gate：`production-certification-bundle`。

## 唯一职责

B17a 只裁决“当前源码快照是否同时拥有三类真实环境证据”。它不修改业务状态机、模型规划、事务协议或数据库实现，也不把静态/模型桩/SQLite 结果升级为真实生产认证。

## 替换或删除项

- Release 模式不再把 `preproduction-real-model-certification-bundle`、`configured-model-browser-conversation`、`configured-model-browser-campaign` 三份独立结果拼成最终权威。
- `clean-release-preflight` 改为依赖唯一 `production-certification-bundle`。
- 配置模型浏览器 Gate 保留在 Integration 作为诊断，不再独立裁决 Release。

## 删除证据

- `governance/quality-loop-policy.json` 中已删除独立 Release real-model Gate。
- 两个 configured-model browser Gate 已移除 `release` mode。
- Release 只存在一个生产认证入口，并且 CLI 不接受 `--evidence-in`、历史证据目录或组件 PASS 文件。

## 验证

- 旧 Release 策略在“独立三类绿灯不能形成生产 Bundle”的反例上失败。
- 修复后，同会话三组件可形成 `production-certification-bundle@1`。
- 不同 session、不同 workspace、浏览器/模型身份不一致、缺 pgvector、缺重启/并发证据、BLOCKED component、认证期间源码变更均 fail closed。
- 当前环境缺官方模型 Key、Docker/PostgreSQL 和 Playwright browser 时，真实执行返回 `BLOCKED_BY_ENVIRONMENT`，不得写 production closed。
