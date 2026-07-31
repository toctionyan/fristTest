# 对话回归合同 · 行为版

对话验证分为互不替代的证据层。对象名、目录名和当前实现字段不得成为通用验收结构；每层只证明自己的边界。

## 1. Runtime Contract Suite

固定模型脚本可以提供已经结构化的候选。Runtime Suite 证明的是：在候选与用户语义一致时，系统能否正确完成事实验证、目标绑定、能力证明、权限、事务和发布。

每个可执行 Turn 至少检查：

- 候选合同结构和原文证据；
- Target 身份、可见来源、scope 与基数；
- Capability 候选、精确 MatchProof 与 Permit；
- 当前任务计划对全部正式 Goal 的覆盖；
- Assessment、Interaction、Draft、Grant、Attempt、Receipt（案例涉及时）；
- 真实业务端口调用；
- Runtime Outcome 与用户可见发布；
- forbidden behavior 的“未发生”证据。

该套件不证明真实模型能够从开放语言完整识别上下文和多任务。

## 2. Capability Confusion Matrix

能力目录必须有表驱动正反例，至少覆盖：

- 查询、资格评估、咨询和副作用操作；
- 相近名称但业务效果不同；
- 前置支持能力不能冒充 Goal 完成能力；
- 系统未实现的 Requested Effect；
- 单对象、集合对象和基数不匹配；
- 权限禁止、部署不可用和真正不存在；
- 多 Goal 不得被一个宽泛 Tool 静默吞并。

语义相似度只允许召回候选。候选之外的能力、相近但身份不同的能力和 support-only 能力都不得获得正式 MatchProof。

## 3. Independent Semantic Oracle

每个高风险 Turn 必须有独立 Oracle，不能由模型候选、Tool 选择或最终 Workflow 反向生成。Oracle 至少描述：

- 用户独立要求的业务效果；
- 每个效果的对象；
- 条件、顺序和依赖；
- 用户原文证据；
- 必须禁止的相似目标、错误对象和额外副作用；
- 无法确定时应保留的歧义。

验证器比较：

```text
独立 Oracle
↔ 模型语义候选
↔ 已验证的正式语义依据
↔ 能力匹配与局部执行计划
↔ 最终用户可见结果
```

漏掉一个 Goal、增加用户未要求的 Goal、改变条件/顺序、相似能力替代或把前置步骤成功冒充最终目标完成，都必须失败。

## 4. 上下文关系与跨轮恢复

程序不得预先把用户语言强制分类成固定关系再交给模型。Context Projection 只提供权威事实：当前 Goal、Blocker、Interaction、Publication、Target population 和事务摘要。

模型可以在一轮中同时提出多个具体变化，例如：

- 解决一个旧 Blocker；
- 暂停另一个 Goal；
- 新建查询 Goal；
- 修改焦点；
- 保持已有 Goal 不变。

正式系统保存的是具体状态变化及其证据，不要求长期保存完整语言学分类。任何恢复、取消、替换或补充输入都必须引用真实 Goal/Blocker，并通过 scope、生命周期和事务状态验证。

必须成对验证：

- 挂起任务被正确补充并恢复；
- 用户放弃旧任务并提出新任务时，旧任务不会劫持；
- 用户一轮同时补充旧任务和新开任务时，两者均被保留；
- 多个活动 Interaction 下的“确认”不会自动选择最近一个；
- 跨 thread、actor、tenant 的 Goal、Target、Draft 和可见结果不泄漏。

## 5. 多任务与计划

同一句用户表达可能包含多个独立业务 Goal，也可能只有一个 Goal 但需要多个 Tool。测试必须区分：

- 用户 Goal 数量；
- Capability 数量；
- Tool/API 步骤数量；
- 集合 fan-out 数量。

计划证据至少检查：

- 每个 Goal 被覆盖或明确 unsupported；
- 用户明确条件和顺序被保留；
- Capability requires/produces 闭合；
- 静态前置条件和动态 Assessment 被区分；
- 独立查询可以安全并行；
- 冲突写操作不被危险并行；
- Execution Replan 不改变冻结 Goal。

## 6. Protected Real-model Smoke

受保护环境中的真实模型 Smoke 只测试开放语义、上下文关系、多 Goal、Target 候选、能力候选和歧义表达。默认只读，不创建副作用对象。它不能替代完整 Lifecycle、HTTP/SSE、数据库、浏览器和事务测试。

## 7. Configured-model Product Journey

来自真实用户的故障必须保留原始逐轮文本、顺序、thread、用户可见 Publication 和独立正负 Oracle，通过真实网页完成。浏览器测试必须验证：

- 最终回答非空且语义正确；
- required target 出现，forbidden siblings 不出现；
- 无能力不被相似能力替代；
- 多任务结果不遗漏；
- 刷新后 transcript 等价；
- 新 thread 隔离；
- Draft、授权和 Receipt 的用户可见状态真实。

HTTP 200、通用降级话术、空卡片或内部 Trace 都不能代替用户可见正确结果。

## 8. 写操作真实性

完整写操作必须在隔离环境中经过真实权威链：

```text
Verified semantic basis
→ Target/Capability proof
→ Assessment/Input
→ Draft/Authority
→ Attempt
→ Business Service
→ Receipt/Reconcile
→ Publication
```

不得用模型文本、mock 成功值或 Tool 返回 `ok=true` 冒充最终业务成功。
