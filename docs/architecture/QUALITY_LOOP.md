# V20.6 Claim-Driven 质量 Loop

V20.6 的质量 Loop 可定位失败、可定向回归、可停止，并要求每个失败轮次后出现新的范围内 repair fingerprint。一次命令只验证一次；修复发生在开发者或 Codex 的受控修改之间，只改轮次或原样重复命令不能伪造收敛。

## 目标

- 修改前先明确目标、范围和验收条件。
- 修改前用 `architecture-skill/templates/quality-loop-target.md` 冻结目标与边界，并运行带 `--baseline` 的检查。
- `允许范围` 中必须列出机器可读的 `允许变更路径`，并声明 `新增抽象记录`（或 `无`）。local baseline 会对源码/发布输入建快照；后续范围外变更、或新增生产源码却没有替换/删除记录，均会失败并要求新 target/new baseline。
- `验收条件` 必须声明 `最低质量模式`。baseline 只冻结源码身份；不能用一次 static 验证替代目标声明的 quick、integration 或 release 完成门槛。
- `目标类型=repair` 必须先得到红 baseline，并要求每条 `regression-transition` claim 从 `FAILED` 转为 `VERIFIED`；`certification` 用于不可变提交的当前状态认证。验收 ID 与 claim ID 必须完全一致。
- 修改后由 `scripts/quality_loop.py` 调度 Skill、架构、模块闭环、版本一致性、Runtime Contract Suite、独立 Semantic Goal Oracle Suite 与强上下文、服务测试、前端测试和构建。
- 每次命令将机器证据写入 `.quality/evidence/` 或 `QUALITY_EVIDENCE_DIR`，失败同时写入 `repair-plan.json`。
- 修复后使用 `--rerun-from <gate>` 回归该 gate 的依赖闭包及受影响下游；该结果只能作为定向证据，不能关闭目标。目标记录最多允许八次人工/Codex 修复轮次。

## 入口

```bash
make doctor
make bootstrap
make quality-baseline TARGET=/absolute/path/to/quality-loop-target.md

make quality TARGET=/absolute/path/to/quality-loop-target.md \
  BASELINE_EVIDENCE=/absolute/path/to/baseline-evidence
make quality-quick TARGET=/absolute/path/to/quality-loop-target.md \
  BASELINE_EVIDENCE=/absolute/path/to/baseline-evidence
make quality-integration TARGET=/absolute/path/to/quality-loop-target.md \
  BASELINE_EVIDENCE=/absolute/path/to/baseline-evidence
make release-check TARGET=/absolute/path/to/quality-loop-target.md \
  BASELINE_EVIDENCE=/absolute/path/to/baseline-evidence
```

需要自动推进多轮时，使用 `scripts/repair_loop.py --fix-command ...`。它生成稳定 Issue、调用外部 fixer、先跑最小依赖闭包再跑完整模式；`scripts/quality_loop.py` 始终是独立只读 Judge，修复器不能改写 evidence。

所有 Python gate 由控制器解析出的 Python 3.12 解释器执行；不要假设系统存在 `python` 或 `python3` 别名。

## 模式

- `static`：只跑静态收敛检查，适合小改动和本地 pre-commit。
- `quick`：静态检查 + Python 服务回归 + 前端依赖、Vitest 和构建，适合中等改动。
- `integration`：quick + 受控双服务、Postgres 与 HTTP smoke。
- `release`：integration + 受保护预发布环境的真实模型只读 smoke（基础连通性与 12 个独立 Goal Oracle 风险原型）。

`release` 不是把一次 integration PASS 与后续两个模型 Gate 拼接起来。GitHub Actions
的 integration job 只生成 local 诊断 evidence；受保护的 `preproduction` job 必须重新
检出同一提交、迁移 PostgreSQL、并以 `APP_PROFILE=preprod` 实际启动 Agent 与 Business。
该服务链启用 PostgreSQL Agent/checkpoint/Business/RAG/文档队列、JWT、actor signature、
严格状态合同和三个 model verifier，再在一次 `--mode release` 中从头执行全部 required
Gate。真实模型只读 Gate 使用 protected secret；完整服务回归使用确定性模型 endpoint，
两者都不能替代另一方。该 job 不复用前序 PASS，也不使用 `--rerun-from` 或
`--prior-evidence`。

## Claim 证据闭环

- target 的自然语言验收不能直接决定完成；每项验收必须进入 claim manifest。
- target 验收 ID 和 claim ID 是完全相等的集合；CI 生成目标保留 source manifest 的 target ID 与 fingerprint。
- 每条 claim 有 Owner 和 closure requirement；repair claim 没有红到绿转换时，即使当前 Gate 通过也不能完成。
- 控制器以全部 claim 的最高 `required_mode` 推导最低执行模式，手工声明只能提高、不能降低。
- `path.py::test_name` 不因源码中存在就算证据；该 testcase 必须出现在本次 evidence 目录的 JUnit 中并通过。
- `gate-log:<id>` 只能绑定该 claim 的 required Gate，并要求该 Gate 在本轮 PASS。
- bare source path 只证明当前源码合同存在且已进入 workspace snapshot，不能单独证明 P0/P1 运行结论。
- Gate 全绿但任一 claim 为 `NOT_EXECUTED`、`INSUFFICIENT_MODE`、`BLOCKED_BY_ENVIRONMENT` 或 `FAILED` 时，`completion_eligible=false`。

## 判定

- `PASS`：本次选中的步骤通过；只有未使用 `--rerun-from`、完整执行当前模式全部 required Gate、且模式达到 target 的 `最低质量模式` 时，才具备完成资格。
- `FAIL`：required 步骤失败，必须修复后重跑。
- `BLOCKED_BY_ENVIRONMENT`：选中的环境或凭据缺失导致无法验证；所有模式均返回非零，发布绝不允许带边界通过。

历史发布证据保留在 `governance/evidence/`；新 evidence 默认落在未跟踪的
`.quality/evidence/`。每份 evidence 必须签名覆盖全部证据文件，并绑定当前质量策略、
完整 Gate 集与源码快照。clean-release 同时生成确定性的 evidence bundle，在工程 ZIP
内嵌 attestation、run summary、workspace snapshot，并记录 bundle/attestation SHA256、
commit SHA、workflow run ID/attempt；任一身份缺失或哈希不匹配都会阻断发布。

预发布原型逐 user turn 调用真实模型，只检查其 `declare_turn_goals` 是否覆盖独立 Goal
Oracle；它不构建 Lifecycle Graph、不创建 Draft、也不向 Business Service 发起读写。
HTTP/SSE 与事务完整链在 disposable 数据上验证；release job 的这条完整链本身也必须
运行在 preprod profile，不能再由 local profile 服务冒充。


## 开发工作区与正式发布

当前 development-workspace profile 按用户要求保留 `.env`、本地数据库、缓存、依赖和历史 evidence；这些内容不触发本轮架构失败。正式外部分发必须使用独立 clean-release profile 和白名单打包，不能把开发工作区 Gate 与发布清洁 Gate 混为一谈。

## 环境阻断传播

下游 Gate 因环境 Gate 阻断而未执行时，claim 状态沿依赖链保持 `BLOCKED_BY_ENVIRONMENT`；如果依赖链同时包含真实 Gate `FAIL`，则为 `FAILED`。控制器必须保存 `environment_blocked_gates`，不得把未认证误报为代码缺陷，也不得把两者任一视为完成。

Gate 在执行前可通过 policy 的环境声明形成阻断；执行过程中才发现的网络、凭据、余额或外部 Provider 不可用，必须同时返回退出码 78，并在 stdout 最后一条 JSON 证据中给出 `status=BLOCKED_BY_ENVIRONMENT` 与非空 `reason`。裸退出码 78 仍按真实失败处理，避免任意测试借环境状态隐藏代码缺陷。
