# V20.17 B17c — Production Release Control Closure

## 目标

修复 B17b 发布入口“结构看似完整、真实 CI 却无法关单”的控制面缺陷。B17c 不扩大产品运行时范围，只保证受保护发布在四个阶段均可被准确裁决：预检、Release Quality Loop、受保护打包、产物完整性验证。

## B17b 实际缺陷

1. `run_production_release.py` 检查 `production_certification.bundle_contract`，但 Quality Loop 的真实生产维度合同字段是 `production_certification.contract=production-certification-dimension@1`；Bundle 合同位于 `real_model_certification.bundle_contract`。因此真实 PASS Summary 仍会被拒绝。
2. CI Quality Loop 的完成状态是 `CI_VERIFIED`，B17b 只接受 `CONVERGED/CLOSED`，导致正式 GitHub workflow 无法生成 production closed。
3. 发布执行器导入 `agent_core.model_calls.real_model_identity` 时会先执行包级 `__init__`，被 LangChain/LangGraph 全量依赖绑死；环境损坏时无法先输出发布预检诊断。
4. 失败只打印一行 JSON，不保证生成可上传的控制结果；预检失败、质量失败和打包失败无法形成统一阶段记录。
5. workflow 上传了不存在的 `${runner.temp}/production-release-target.claims.json`，而实际生成文件位于 `.quality/targets/quality-target-release.claims.json`。
6. B17b 只检查“存在一个非 evidence ZIP”，未验证精确产物集合及 source ZIP 的 SHA256 sidecar。

## B17c 控制合同

唯一执行器升级为 `production-release-execution@2`，状态结构固定为：

- `BLOCKED_BY_ENVIRONMENT / preflight`：密钥、GitHub provenance、Docker/Node/npm 或锁定 Python 环境缺失。
- `BLOCKED_BY_ENVIRONMENT / quality_loop`：真实模型、owned PostgreSQL/pgvector 或浏览器环境阻断。
- `FAIL / quality_summary`：Summary 合同、CI 状态、生产 Bundle 或模型身份不一致。
- `FAIL / artifact_build`：protected clean-release 构建失败。
- `FAIL / artifact_validation`：产物集合、文件名或 SHA256 sidecar 不一致。
- `PASS / closed`：同一次 CI 运行的生产 Bundle、签名 evidence 和精确 content-addressed artifacts 均通过。

## 身份与证据绑定

发布预检只记录 provider、官方 endpoint、model 和凭据 SHA256 前 16 位，不记录 API Key 或 evidence signing key。Summary 中的 `production_certification.real_model_identity` 与 `real_model_certification.identity` 必须同时匹配预检身份；session ID 和 production workspace fingerprint 也必须在两个维度一致。

控制结果通过独立 `--result-path` 写入 workspace 外部目录。无论预检、质量、打包还是验证失败，workflow 都会上传该控制结果；只有 job 成功时才上传 production closed artifacts。

## 精确产物合同

成功目录只能包含：

- `customer_agent_workspace_v20_17_production_closed.zip`
- `customer_agent_workspace_v20_17_production_closed.zip.sha256`
- `customer_agent_workspace_v20_17_production_closed-quality-evidence.zip`

source ZIP 的 sidecar 文件名和 SHA256 必须与实际文件完全一致。多文件、缺文件、旧文件混入或 sidecar 被篡改均 fail closed。

## 边界

B17c 不修改客服 Agent 业务逻辑、Prompt、Capability、计划、事务状态、数据库实现、B17a 生产认证组件或真实浏览器旅程。真实生产关单仍需要受保护 GitHub Environment 中的官方模型 Secret、至少 32 字节 evidence signing key、Docker/owned pgvector 和 Playwright Chromium。

## 新增项

- `production-release-execution@2` 独立发布控制合同。
- workspace 外部 `production-release-result.json` 控制台账。
- Summary 真实字段、CI 状态、双维度身份一致性和精确产物集合反例。

## 唯一职责

B17c 只负责受保护发布的阶段裁决、失败记录和最终产物完整性，不拥有 Agent 语义、业务状态或环境组件真实性。

## 替换或删除项

- 替换 B17b 对 `production_certification.bundle_contract` 的错误检查。
- 删除虚构的 `CLOSED` CI 完成状态，改为真实 `CI_VERIFIED`。
- 替换错误的 `production-release-target.claims.json` 上传路径。
- 删除“任意非 evidence ZIP 即可关单”的宽松产物判断。

## 删除证据

- 新 workflow 不再引用不存在的 `${runner.temp}/production-release-target.claims.json`。
- 新执行器不再通过 `agent_core.model_calls.__init__` 加载预检身份合同。
- 旧字段伪装、身份漂移、产物 sidecar 篡改和 workspace 内控制结果路径均有失败反例。

## 验证

- B17b/B17c 定向回归全部通过。
- clean-release、Quality Loop 与发布证据相关架构测试通过。
- Architecture Gate 保持 `PASS / RESOLVED`。
- 当前本地环境缺锁定 Python 3.12.13 依赖、前端 `node_modules`、Docker 和非受管 Chromium，因此不得生成 production closed。
