# Skill 工程宪法

## 1. 真实目标优先

治理系统约束证明方式、权威边界和迁移纪律，不替模型预先决定具体类名、目录、节点数量、层数或框架。任何实现都应先证明解决了用户真实目标。

## 2. 行为优先于形状

Skill 可以定义职责、Owner、数据流、不变量、禁止行为和证据要求；项目基线可以定义当前目录和文件。通用 Skill 不得把当前项目的名称、目录或参考模式冒充跨项目硬规则。

## 3. 硬不变量

以下规则不可通过偏离记录绕过：

- 不伪造、复用或篡改 Evidence。
- 不通过降低测试、Claim、Oracle 或 Gate 获得通过。
- 实现者不能成为最终裁判。
- 环境缺失不能包装成代码成功。
- 未授权路径不得写入。
- 最终可裁决事实必须有唯一权威来源；缓存、索引和投影只能是派生数据。
- 不存在的能力不得由相近能力替代并声称成功。
- 候选、正式合同和最终业务结果不得混为同一权威对象。

## 4. 默认架构可以被挑战

唯一正式链、单一 Owner、避免新旧并存、模块垂直闭环属于强默认，而不是固定代码形状。偏离时必须提交 Architecture Decision、Architecture Variance 和必要的 Project Baseline Delta，证明硬不变量仍成立。

## 5. 先比较再冻结

架构任务在进入 Migration 前至少比较保守、演进和重构方案。Target 只在 Architecture Decision 形成后冻结。设计名词只是候选词汇，不能提前成为 Gate。

## 6. 单一写入者

同一 Change Contract 下只能有一个可写实现者。Planner、Oracle Reviewer、Adversarial Reviewer 和 Judge 均保持只读。

## 7. 可以不改，也可以回滚

正式结果包括 `NO_CODE_CHANGE_REQUIRED` 和 `REVERT_RECOMMENDED`。流程不得为了满足“有 Diff”制造无意义修改。

## 8. 当前证据

最终结论必须绑定当前 Change、源码身份、Judge 身份、执行环境、有效项目基线或批准 Delta、Gate 合同和未验证项。定向回归只能诊断，不能关闭整体目标。

## 9. 基线必须可迁移

项目目录、必需文件和 Owner 快照属于当前 Baseline。Migration 可以通过受审查 Delta 临时改变它；认证完成后必须提升为新 Baseline，并清理临时双链和过期 Delta。
