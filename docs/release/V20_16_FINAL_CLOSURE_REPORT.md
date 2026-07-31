# V20.16 State Schema v2 最终闭环报告

## 最终结论

V20.16 State Schema v2、旧 checkpoint 一次性迁移、控制平面测试合同、Schema v2 测试夹具、计划反例、前端、完整产品生命周期及 clean-release 打包器已完成闭环。

最终状态：

- Product Certification Contract：`closed / CONVERGED`
- Scope Planner、Adversarial Reviewer、Release Judge：全部 `PASS`
- 最终发布树中央 Quick：要求 `18/18 PASS`、`5/5 Claims VERIFIED`
- clean-release 结构烟测：`PASS`
- 发布包不包含 `.venv`、`node_modules`、缓存、数据库、日志或 `.env`

最终发布树证据目录：

`.quality/product-code/certify-v20.16-continuation-final/release-final-quick-20260728T100000Z`

## 当前测试口径

| 验证层 | 预期最终结果 |
|---|---:|
| Agent Service 标准测试 | 631 passed，0 failed |
| Business Service 标准测试 | 28 passed，0 failed |
| 对抗性运行时反例 | 106 passed |
| 系统性运行反例 | 17 passed |
| 前端 Vitest | 6 files，28 tests passed |
| 前端生产构建 | 1599 modules transformed，PASS |
| Python 覆盖率最低实测 | 72.15%，基线 56.14% |
| 前端行覆盖率 | 54.32%，基线 26.27% |
| 完整 HTTP 生命周期 | PASS |
| 真实 Chromium 桌面/移动端旅程 | PASS |
| clean-release stage/zip | PASS |

完整 HTTP 生命周期覆盖登录、聊天、Draft、补充输入、授权、提交、Receipt 和 SSE。

真实 Chromium 旅程覆盖订单选择、线程导航、Pending 恢复、事务提交、Receipt 展示、终态标题、可访问聊天、历史一致性、390×844 移动端布局和无横向溢出。

## 本次继续阶段关闭的问题

1. 控制平面自测绑定旧错误文案，临时 Repair Orchestrator 夹具遗漏新依赖。
2. 双线程 Schema v2 测试缺少 `requested_effect`。
3. Repair Loop 测试没有产生真实范围内候选修改。
4. “最便宜订单退款资格”计划缺少正式完成路径。
5. 前端依赖在线源不可用；使用与当前 lockfile 完全一致的历史依赖并补齐相同版本 Linux 原生可选包完成当前执行。
6. 确定性模型夹具仍使用旧 Goal Schema，造成连续 `GOAL_DECLARATION_INVALID` 和 `LoopBudgetExhausted`。
7. Chromium 宿主策略阻断本机地址；认证时临时放行本机 URL，结束后原策略恢复。
8. clean-release 源复制漏掉 Skill 控制面和 Codex/Claude 宿主适配；现已修复并新增回归测试。

## 范围与证据处理

原 migration Target 的范围保持冻结。跨范围缺陷分别保留 Decision、红证据和绿证据，没有通过扩大旧 Target 或重写旧基线制造假绿。

- 原 migration 历史：`governance/closed-changes/migration-v20.16-state-schema-v2-legacy-cutover.json`
- Product certification 历史：`governance/closed-changes/certify-v20.16-continuation-final.product-certification.json`
- clean-release 修复：`governance/decisions/repair-v20.16-clean-release-control-plane-copy.json`

## 不属于本阶段的内容

本次是 Quick 级当前源码与候选发布包认证，不等同于正式 Release/Preproduction 认证。真实外部模型、正式 PostgreSQL/pgvector、正式部署环境、并发迁移压力和 V20.17 旧迁移代码删除属于后续阶段。
