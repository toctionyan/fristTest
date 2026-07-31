# 质量 Loop 合同

## Target

`quality-loop-target.md` 的 `# 目标` 必须声明：

- `目标 ID`：本轮不变的、可追踪 ID。
- `变更标识`：提交 SHA、PR 或明确 working-tree 标识。
- `执行上下文`：`local-change` 或 `ci`。
- `目标类型`：`diagnosis`、`design`、`oracle-review`、`repair`、`migration`、`revert` 或 `certification`。

`允许范围`、`禁止范围`、`验收条件`、`基线` 和 `修复轮次` 也必须有实际内容；保留模板 HTML 提示或占位符的记录无效。`允许范围` 必须另有一行 `允许变更路径：path/**, other/file`；`local-change` 不得用 `*` 或 `**` 覆盖整个工作区。它还必须声明 `新增抽象记录：相对路径.md` 或 `无`。`验收条件` 必须同时声明 `最低质量模式：static|quick|integration|release`、`声明清单：workspace-relative.json` 和 `验收 ID`；验收 ID 集合必须与 claim ID 集合完全相等。除 `当前轮次` 外，baseline 之后不得修改 target；claim manifest 也通过 fingerprint 冻结。

claim manifest 中每条 claim 必须有唯一 ID、statement、P0-P3 风险、Owner、`closure_requirement`、`required_mode`、`evidence_kind`、`required_gates` 和 `evidence_refs`。项目 certification 必须另选 `requirement_catalog` 与 `requirement_profile`，每个 claim 用 `requirement_ids` 映射，所有映射的并集必须与 profile 完全相等，且不得降低 catalog 声明的风险或模式。catalog 内容与 manifest 一起进入 target fingerprint 和 evidence。`repair`、`migration`、`revert` 的 closure 全部是 `regression-transition`，且每条 claim 必须从 baseline `FAILED` 转为本轮 `VERIFIED`；`diagnosis`、`design`、`oracle-review` 和 `certification` 可以使用 `current-pass`，不得为了满足流程制造无意义源码修改。证据引用只能是当前工作区内真实存在的安全相对路径，或绑定本 claim required Gate 的 `gate-log:<gate-id>`；Python 测试引用必须使用真实存在的 `path.py::test_name` 选择器，并在本次运行生成的 JUnit 中实际执行且通过。P0/P1 的 counterexample、integration 或 release-provenance claim 不得只引用说明文档。`evidence_kind` 必须与 Gate 类别和最低模式一致：counterexample 至少 quick，integration 至少 integration，release-provenance 必须 release 且绑定 release Gate。控制器从全部 claim 自动推导最低质量模式并验证 Gate 映射；手工最低模式低于推导结果时输入无效。

`repair`、`migration`、`revert` 的 `local-change` 先运行 `--baseline`，必须真实复现至少一个失败 claim；验证时没有实际范围内候选变化无效。`diagnosis`、`design` 和 `oracle-review` 可以零代码修改并返回 `NO_CODE_CHANGE_REQUIRED` 或 `ORACLE_REVIEW_REQUIRED`。baseline 记录源码/发布输入快照；验证时控制器比较快照，只允许白名单路径变化。新增生产 Python 文件时必须有完整的新抽象替换记录。`ci` 验证不可变提交，不伪造本地 baseline；生成的 CI claim manifest 应携带 source claim manifest 的 target ID 和 fingerprint，不能用单个笼统 PASS claim 抹掉修复声明身份。

## 轮次与重跑

初始 `当前轮次` 为 1。失败后控制器要求下一轮加一；最大 8。第八轮失败写 `STOPPED_MAX_REPAIRS`；连续两轮没有可度量改善时写 `ARCHITECTURE_REPLAN_REQUIRED`，不得继续堆局部补丁。环境阻断不消耗修复轮次。

架构重规划不能切断红证据血缘。承接 `ARCHITECTURE_REPLAN_REQUIRED` 的新 local target 在 `# 目标` 中必须成对声明 `重规划来源证据：.quality/evidence/<stopped-run>` 与 `重规划失败 Gate：<gate-id>`。控制器必须在任何 Gate 前验证旧 evidence 的 attestation 未被篡改、`run_kind=verification`、`decision=FAIL`、run summary 与 repair plan 都为 `ARCHITECTURE_REPLAN_REQUIRED`、指定 Gate 确实 FAIL 且出现在 repair plan、至少一个 claim FAILED，并拒绝后继 target 自引用。验证后的旧 target identity、失败 Gate 和 attestation fingerprint 必须进入新 run summary；旧 evidence 只证明问题来源，新 repair 仍要建立自己的红 baseline 并完成 claim 的红到绿转换。

`--rerun-from` 必须运行根 Gate 的全部依赖祖先、根 Gate 与其受影响下游。历史 PASS、旧 JUnit/coverage 或 `--prior-evidence` 均不得替代本次依赖执行；兼容参数一旦提供即由控制器拒绝。定向回归无论结果如何都不能关闭目标；repair orchestrator 只有在定向结果为 `TARGETED_REGRESSION_PASSED` 后才可启动完整 Judge，最终必须从当前源码重新运行最低质量模式的全部 required Gate。

## Gate

策略每一步声明 `id`、Owner、依赖、适用 modes、`blocking_level`、环境前置、`repair_playbook` 与 `rerun_contract=dependency_closure_then_downstream`。超时、环境、测试/合同和架构失败分别进入 repair plan；证据包含 stdout/stderr 路径与可复制的定向回归命令。

每个 Gate 必须运行在独立进程组中，stdout/stderr 落入控制器拥有的文件，不能让后台后代进程通过继承管道拖住已退出的父命令。父命令结束后必须回收其进程组；超时先终止、再强制清理。任何残留 Worker 都不得继续污染下一 Gate、修改工作区或制造假超时。

控制器必须向每个 Gate 注入本轮 `QUALITY_EVIDENCE_DIR`、`QUALITY_LOOP_MODE` 与 `QUALITY_GATE_ID`。编译、覆盖率、浏览器截图等派生产物只能写入该 evidence 边界；例如前端 Gate 必须构建到 `evidence/artifacts/frontend-dist`，不能改写受治理的工作区 `frontend/dist`。运行结束的 workspace fingerprint 发生任何变化都必须增加 `controller-workspace-immutability` 失败，不能把生成物加入忽略表伪装成只读。

控制器只能执行策略、保存 evidence 和生成 repair plan。它不编辑源码、target 或策略，也不自动重跑失败 Gate。独立 `repair_loop.py` 必须在 fixer 前校验 attestation、target identity、policy fingerprint 与失败 claim，把 Judge 结果生成稳定 Issue，调用显式外部 fixer，并验证 fixer 未改 target/policy/baseline，随后请求定向与完整 Judge；fixer 不得写 evidence，最终判定仍只来自 `quality_loop.py`。 受保护认证还必须通过 `SKILL_TRUSTED_JUDGE_ROOT` 使用工作区外的只读 Judge；本地同工作区运行只能标记 `workspace-fallback`，不能声称物理信任隔离。外部 Fixer 只继承白名单环境变量，日志和源码注释按不可信数据处理。

项目级 requirement catalog v2 必须绑定独立 Product Capability Inventory。所有 Inventory ID 必须被 requirement 无遗漏覆盖；P0/P1 requirement 必须声明 invariant、failure_class、counterexample 与 mutation。认证 profile 严格按 `project-quick ⊂ project-integration ⊂ project-product ⊂ project-release` 累积，控制器必须对删除低层 requirement 的变异自检失败。

运行前用 `workspace_doctor.py` 只读执行两个 `uv sync --all-groups --locked --check --offline`，并将前端直接依赖的已安装版本与 `package-lock.json` 精确比较；缺失或漂移先运行带 `--locked` 的显式 bootstrap 或人工配置，不能在 Gate 内隐式安装依赖。Integration 必须执行 Agent 与 Business 两套 `-m integration`，选中的 selector 必须出现在本次 JUnit。

本地 Integration 可通过 `make quality-integration-managed` 显式请求托管环境。该入口必须拥有并最终清理临时 pgvector、Agent 和 Business 进程，向原始只读 Controller 传入准确 URL/Token；不得修改质量策略或把环境编排结果直接当作 PASS。用于公共 HTTP Gate 的 deterministic provider identity 在调用 Controller 前必须清除，让 configured-model Gate 重新从真实配置加载 provider；否则属于模型身份伪证。Docker/镜像不可用返回环境阻断，不能降级到 SQLite 冒充 PostgreSQL。

修复与认证是不同目标：transition target 关闭代码级反例；certification target 在不可变提交、真实 PostgreSQL、protected preproduction、真实模型和 clean artifact 上关闭运行/发布 claim。缺少认证环境不得降低 repair target 的 claim 等级，也不得把本地 quick evidence 命名为 protected release。

涉及客户对话、上下文、展示或前端消息链的 repair target，Quick 至少包含真实 Chromium 确定性产品旅程，Integration 至少包含由 Gate 自行启动服务的 configured-model Chromium 强上下文旅程。逐轮证据必须同时检查客户可见非空、实体/范围、业务意图、禁止替代、内部错误脱敏和刷新历史等价；HTTP 200、Graph 完成、tool trace 非空或单轮问候都不能关闭产品 claim。对应 P0/P1 claim 必须声明能杀死“空 notice block”“语义断言退化为非空”“真实模型浏览器 Gate 被删除”的 Mutation。

实体/范围 Oracle 不能只有 `required target`：点名唯一成员的回合还必须自动或显式列出 `forbidden siblings`。一个包含目标成员但同时泄露兄弟成员的宽集合必须判失败；同一失败类还要有 Runtime Permit 级反例，确保模型换一种说法或前端换一种渲染后仍不能绕过。

用户报告的真实网页对话故障必须按原始文本和顺序加入 configured-model Chromium 回归，并至少增加一个同能力边界的表达变体。固定模型 catalog 只允许显式 `executable_case_ids` 计入证据；语义与候选工具不一致、只验证脚本自洽或重复覆盖同一失败类型的案例不得贡献 PASS 数量。可以裁剪这些假覆盖，但 Capability Contract、混淆矩阵、Gate 拒绝、故障注入、Mutation、长序列、跨线程与网页层不得被削弱。

裁剪不是简单减少案例：catalog 总量可以作为场景库存，只有风险加权、语义一致且执行真实 Graph 的子集计入 runtime evidence。configured-model Chromium 权重必须提高到能覆盖客户原链路、改写表达、相似能力、无能力、跨域否定和上下文切换；但它不替代低层确定性拒绝证据。任何万能能力拆分必须新增“异域高分知识不得释放”和“未知相似请求不得误路由”的反例/Mutation。

Loop 的对话判定顺序为：原始网页反例红灯 → 唯一 Owner 修复 → 能力混淆/Permit 定向回归 → 确定性 Graph 与浏览器全量 → configured-model Chromium 原链路与表达变体。真实 provider 的认证、余额、配额或网络失败是环境阻断，保留红灯且不消耗修复轮次；不得改用 deterministic provider、API 脚本或放宽 DOM Oracle 宣称完成。

强上下文 Campaign 的闭环顺序为：保存原始种子的逐轮红灯产物 → 按最早失败边界聚类（Goal/Capability、Target/ResultRef、Permit、Business、Answer Release、Projection、History）→ 选择共同 Owner 修复 → 重跑该失败类的低层反例 → 原种子 20×10 网页复测 → 未见种子 20×10 网页复测。不得按最终通用降级文案把所有失败归成“模型回答不好”；归因必须定位首次破坏语义的边界。原种子与新种子的阈值、零容忍失败类和证据字段以[对话回归合同](conversation-regression-contract.md)为准。

Campaign 证据必须写入本轮 `QUALITY_EVIDENCE_DIR`，包含不可变 definition（seed、场景顺序、Oracle）、逐轮报告、汇总、刷新对比和安全诊断；配置模型的 token/cache 统计也应保存。已经由确定性 MatchProof 完整证明的只读回合应跳过二次 Answer Release 语义裁判，避免不必要 token 消耗和随机否决，但缺少精确证明时不得为了省 token 跳过校验。

涉及澄清的 failure class 必须按跨轮状态机而不是单句回复归因。required Gate 至少检查 `Goal declared → clarification pause → checkpoint persisted → next-turn disposition → original goal resumed or explicitly retired → final goal coverage`；若只断言澄清文案出现，claim 保持 FAILED。每个恢复正例都要有放弃/新请求反例，Mutation 删除 checkpoint 持久化、删除 `continuation_of` 类型锁或让旧 goal 劫持新请求时必须被杀死。

Agent Loop 的“成功”必须区分 Step 完成与 Goal 完成。对话类 P0/P1 target 至少保留一个成功前置读取但目标仍 `PENDING` 的反例，并验证只有 Capability Contract 的 `goal_completion_types` 覆盖目标类型时，成功 Step 才能推进 Goal Coverage。浏览器还要断言完成目标的正式展示语义可见，前置对象卡非空不能替代政策、资格或办理结论。

release 的真实模型 gate 只能在 protected preproduction 运行。该 job 实际启动的 Agent
与 Business 也必须是 `APP_PROFILE=preprod`，并使用 PostgreSQL Agent/checkpoint/Business
持久化、签名 actor、严格状态合同和 model verifier；仅给控制器设置 preprod 无效。
缺少 Node、服务、数据库或预发布密钥返回 `BLOCKED_BY_ENVIRONMENT` 且进程非零。
base smoke 只接受精确 `model-smoke-ok`；目标 prototype 必须拒绝重复 Goal ID，并通过生产 `validate_goal_declaration` 与独立 Goal Alignment，而不是仅做字段/数量比较。
## Clean-release 证据边界

发布构建必须从白名单复制源码，删除旧前端 `dist` 后按锁文件重建，生成非空
`file_count`、精确 `FILE_LIST.txt` 和逐文件 `SHA256SUMS.txt`，并对最终 ZIP 解包后执行
同一校验。源码快照与打包共用唯一锚定路径合同；不能因为目录名为 `runtime` 就排除
`src/.../runtime` 或 `tests/runtime`。工程 ZIP 内嵌 evidence attestation、run summary 和
workspace snapshot，manifest 记录确定性 evidence bundle SHA256、attestation SHA256、
commit SHA、workflow run ID/attempt；可编辑的单独 summary 或无法定位唯一 bundle
没有发布授权力。

### 环境阻断的传递语义

环境 Gate 缺失时，其下游 `SKIPPED_UPSTREAM_FAILURE` 只在全部失败依赖均可追溯到环境阻断时解释为 `BLOCKED_BY_ENVIRONMENT`。只要依赖链包含真实 `FAIL`，对应 claim 必须保持 `FAILED`。两种状态都不得获得完成资格。
