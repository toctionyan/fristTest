# 项目架构基线与迁移合同

## 基线定位

`governance/architecture-policy.json` 是当前项目版本的架构快照，不是通用 Skill 的固定代码形状。它可以约束当前 Repair，但 Architecture Migration 可以在严格证据下改变目录、必需路径、Owner 和尺寸边界。

## 迁移所需记录

架构迁移必须绑定：

1. Architecture Decision：保守、演进、重构三方案比较；
2. Architecture Variance：说明当前 Baseline 为什么阻碍更优方案；
3. Architecture Policy Delta：只声明本次允许增加、退休或变更的项目基线字段；
4. Change Contract：精确产品写入范围、唯一实现者和 required Profiles；
5. 红基线或可量化差距；
6. Shadow、切换、回滚、清理和认证证据。

## Delta 限制

Delta 只能修改白名单内的项目形状字段，例如 required paths、forbidden paths、允许职责目录、根级模块、line limits 和明确 Owner。它不能修改业务权威、安全、Evidence、Quality Judge、配置事实或能力禁止替代等硬不变量。

## Cutover

Shadow 必须只读。任何时刻只能有一个正式写入或裁决 Owner。切换记录必须声明：

- 当前正式 Owner；
- 目标正式 Owner；
- 切换证据；
- 回滚条件；
- 旧路径删除条件；
- 截止日期。

## Baseline Promotion

产品迁移和认证完成后，由独立 Skill-only Migration 合并 Delta，生成新 `policy_id` 和 Promotion Record。临时 Delta 未被合并、旧正式路径未删除或证据未绑定当前源码时，不得宣布架构收敛。
