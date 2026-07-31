---
name: customer-agent-architecture
version: 6.3.0
description: 客服 Agent 领域架构 Skill。约束开放语义、上下文、多任务规划、能力落地、事务与业务权威边界；不规定最终类名、目录层级、节点数量或框架形状。
---

# 客服 Agent 领域架构

Skill 版本：6.3.0

## 定位

本 Skill 规定**必须由谁裁决、必须证明什么、什么不能被偷换**，不规定最终代码必须使用哪些类名、目录名、层数或节点数。当前项目结构只属于 Project Architecture Baseline；架构迁移可以通过 Architecture Decision、Variance 和受审查 Policy Delta 改变它。

通用 Change Contract、范围、Review、Evidence、Trusted Judge 和宿主适配位于 `skill-system/`。

## HARD_INVARIANT

1. **业务权威**：业务最终事实由 Business Service 裁决；模型只提出候选、计划和对话。
2. **单一开放语义 Owner**：用户语言、上下文关系、指代、纠正、多任务关系由一个受约束的语义编译角色统一提出。程序不得使用关键词、默认枚举、工具失败或最近调用重新解释用户语言。
3. **Goal 不迁就能力**：用户真实业务目标不得为了匹配已有 Tool、Skill 或相似能力而被改写；不存在的能力必须明确 unsupported、阻断或人工接管，禁止相似能力替代。
4. **候选不等于权威**：模型提出的 Goal、Target、能力和参数都必须先经过事实、身份、权限和合同验证；副作用不能直接消费未验证候选。
5. **事务真实性**：授权、提交、Attempt、Receipt 和恢复由明确事务权威裁决；没有 Receipt 或等价最终证据不得声称成功。
6. **派生数据只读**：缓存、索引、摘要、Task Board、UI 焦点和 Projection 不得反向覆盖权威状态。
7. **证据不可降级**：不得通过放宽 Oracle、删除反例、降低 Profile、修改 Judge 或复用陈旧 Evidence 获得通过。
8. **架构债务只减不增**：已登记的依赖环必须通过可审计棘轮逐步缩小；新增、扩大或合并循环依赖必须失败，功能全绿不得被表述为架构已收敛。

## STRONG_DEFAULT

1. **语义拆解与执行规划分离**：先保留用户业务 Goal，再根据当前 Capability Contract 展开具体 Tool/API 步骤。一个 Goal 可以映射一个或多个 Tool；多个 Goal 也可以由一个经证明的复合能力覆盖。
2. **渐进披露**：每轮只提供权威核心上下文和简短能力索引；模型按需读取历史对象、完整能力合同和业务 Playbook，不一次注入全部历史和工具。
3. **局部可行计划**：不要求预建覆盖全系统的完整能力图。只为本次已冻结 Goal 搜索候选能力、展开必要前置条件并生成可证明闭合的局部计划。
4. **验证不改写**：Verifier、MatchProof 和 Plan Validator 只能接受、拒绝、返回阻断或要求澄清，不能替换 Goal、Target 或 Requested Effect。
5. **冻结后不重释**：执行可以因缺少输入、动态 Assessment 或环境失败重规划步骤，但不得改变已经冻结的用户 Goal；改变语义只能由新用户消息或受控语义重编译产生。
6. **一个正式裁决链**：内部可以并行读取、探索和审查，但同一事实和副作用只有一个正式 Owner。Shadow 路径必须只读、有切换条件、回滚条件、清理条件和截止日期。
7. **行为优先于形状**：Gate 优先验证 Owner、数据流、禁止替代、输入输出闭合、旧链退出和结果证据；类名、目录和节点名只能来自当前项目基线或 Architecture Decision。

## REFERENCE_PATTERN

以下是职责角色，不是强制命名：

- 权威上下文投影；
- 模型语义候选；
- 已验证且冻结的本轮语义依据；
- 用户业务 Goal 图；
- Target 身份与来源证明；
- Capability 索引、完整合同与 MatchProof；
- 本任务局部执行计划及确定性验证；
- Goal 生命周期、Blocker 与 UI Focus；
- Assessment、Draft、Grant、Attempt、Receipt；
- Evidence-backed Publication。

当前项目中的 `TurnGoalPlan`、`WorkflowPlan`、`pending_clarification`、`ContextPack` 等名称只属于当前 Baseline Vocabulary。替换时必须证明职责覆盖、权威边界、迁移、回滚、旧读写路径删除和测试证据。

## 语义与多任务边界

- 大模型先根据用户原话和权威上下文拆分独立业务效果、对象、条件、顺序、依赖和歧义；此阶段不按接口数量拆 Tool。
- 系统再按 Goal 的业务效果检索 Capability。语义相似度只可召回候选，正式匹配必须比较能力身份、对象类型、基数、权限、部署状态和禁止替代约束。
- 用户语言参数由模型提出并绑定原文证据；对象、用户、租户、Assessment、版本、授权、幂等键和摘要由权威系统提供；真实 Tool 参数由固定 Adapter 从规范命令生成。
- Planner 可以提出局部 DAG；程序验证每个输入来源、用户顺序、能力前置条件、事务顺序、循环依赖、并行冲突和全部 Goal 覆盖。

## 合法偏离与项目基线

- `HARD_INVARIANT` 不允许偏离。
- `STRONG_DEFAULT` 和 `REFERENCE_PATTERN` 可以通过 Architecture Variance 偏离。
- 当前目录白名单、必需文件和 Owner 路径是 `project-architecture-baseline`，不是通用 Skill 真理。
- 需要改变 Baseline 时，Migration Contract 必须绑定 Architecture Decision、Variance、Architecture Policy Delta、红基线或可量化差距、Shadow/Cutover/cleanup 证据。
- Migration 完成并认证后，通过独立的 Skill-only Baseline Promotion 将 Delta 合并为新 Baseline；临时 Delta 不得永久存在。

## 领域验证

验证至少区分：

1. Runtime Contract：给定正确候选时，程序边界是否正确；
2. Independent Semantic Oracle：模型是否完整理解用户 Goal、关系、对象和条件；
3. Capability Grounding：无能力识别、精确匹配和禁止相似替代；
4. Plan Closure：输入输出来源、依赖、用户顺序、事务顺序和并发安全；
5. Real-model Smoke：真实模型在只读语义/规划模式中的表现；
6. Product Journey：真实网页、服务、数据库、授权和 Receipt 闭环。

这些证据不能互相替代。质量结果必须分别公开功能状态、架构状态与真实模型认证状态；确定性 Runtime 全绿不能自动升级为 Real-model Certified。

## 完成边界

客服领域变更必须经过通用控制平面的 Change Contract、单一写入者、独立 Review、当前源码 Evidence 和确定性 Judge。本 Skill 自身不能宣布收敛；环境缺失必须返回 `BLOCKED_BY_ENVIRONMENT`。
