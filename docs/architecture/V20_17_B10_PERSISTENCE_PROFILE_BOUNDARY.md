# V20.17 B10：Persistence / Runtime Profile 边界

## 状态

B10 将部署 Profile 定义为 Kernel 的低层稳定配置合同。Persistence 直接使用该合同，Runtime 保留兼容导出但不再拥有第二套实现。

## 新增项

- `agent_core.kernel.profile`：APP_PROFILE 枚举、解析、诊断与 verifier mode 唯一实现。

## 唯一职责

Kernel 拥有 domain-neutral 部署 Profile 合同；Runtime 使用并兼容导出；Persistence 只依据该合同选择数据库默认值和租户保护规则。

## 替换或删除项

- `runtime.profile` 删除具体实现，改为显式 re-export；
- persistence 的两个直接导入切换到 kernel.profile；
- 不复制环境解析或保护模式判断。

## 删除证据

- persistence 不得导入 runtime；
- RuntimeProfile 及函数对象在 kernel/runtime 公共入口必须同一；
- 主 SCC 从 5 降到至多 4，persistence 和此前移出包保持在环外；
- 不修改债务基线。

## 验证

- Profile 对象身份、严格模式、错误输入与 verifier mode 回归；
- 数据库设置、线程租户保护和持久化回归；
- 累计 SCC 反例；
- 全量 Quick、生命周期和 Chromium。

## 明确不处理

- 不改变任何部署 Profile 语义；
- 不改变数据库、线程、事务或 Agent 逻辑；
- 剩余 4 包 SCC 由后续 Target 处理。
