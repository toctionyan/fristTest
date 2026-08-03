# 目标

- 目标 ID：repair-v20.17-b21-stage3-oracle-closure
- 变更标识：repair-v20.17-b21-stage3-oracle-closure
- 执行上下文：local-change
- 目标类型：repair

修复阶段 3 聚合对抗性测试中的失效桥接 Oracle，使桥接测试调用当前权威测试名称并直接引用可导入的权威测试函数；不得修改产品运行时代码，也不得把缺少锁定依赖的用例伪装为通过。

## 允许范围

- 允许变更路径：`services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`
- 新增抽象记录：无

## 禁止范围

不得修改 `services/agent-service/src/**`、Business Service、前端、Skill 控制器或治理规则；不得删除、跳过、弱化断言或通过扩大 Mock 获得绿色结果。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b21-stage3-oracle-closure.json`
- 验收 ID：V20-17-B21-STAGE3-ORACLE-001

必须满足：

1. Permit 基线中的失效 B14c 桥接名称和两个 B17e 未定义加载辅助函数可稳定复现；
2. 修复后四个桥接测试直接通过；
3. 聚合文件所有非环境用例通过，剩余失败只能是已逐项登记的 `langgraph/langchain_core` 缺失；
4. 阶段 3 非环境聚焦套件、107 条既有强上下文 Oracle 与 550 条确定性 Campaign 全部通过；
5. 独立 DiffReview 证明只修改允许的测试文件；
6. 不声明真实模型、锁定 Python 3.12.13、PostgreSQL、浏览器或生产认证已经完成。

## 基线

红基线：使用 ChangePermit `490894b7daf872f1dc58862b872ee4e20481ab95ab2930b34d51ea4b6113c316` 签发时的允许文件快照。该快照 SHA256 为 `088014b6d148fd1228b30429537535a9c4ae61849ad69e71505b2b9222dbd57e`，包含可重复的桥接 Oracle 缺陷。

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只修复聚合测试桥接，不修改运行时产品源代码。
