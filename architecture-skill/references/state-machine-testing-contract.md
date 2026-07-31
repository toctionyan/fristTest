# 状态机与序列测试合同 · 行为版

固定案例验证已知业务原型；状态机和属性测试验证同类事件组合。二者不能互相替代。

## Scenario Topology

Case 可以声明 actor、tenant、session 和多个 thread alias。Runner 必须创建真实独立的 checkpoint identity，并分别维护消息、Goal lifecycle、Blocker、Interaction、Target、Draft、Grant、Attempt、Receipt 和 Publication。

在用户文本中写“线程 A/B”不算 topology。测试证据必须记录实际 identity，并验证交错序列的轮次、对象和事务互不泄漏。

## Generated Sequences

高风险状态机至少生成：

- 1–100 轮；
- 查询、目标切换、纠正、补充输入、暂停、恢复、取消、Draft、授权和异常；
- 单 thread 与多 thread；
- 正常、过期、跨 scope、重复、乱序和并发事件；
- 多个未完成 Goal 和多个 Interaction；
- 工具失败、Assessment 变化和环境阻断后的 Execution Replan。

## 必须保持的不变量

- 用户语言关系只由语义候选提出，程序不使用关键词重新分类；
- 正式 Goal 的改变必须来自新语义合同或已验证的具体状态变化；
- 已关闭、取消或 superseded 的 Goal 不得无证据复活；
- Blocker 只能解决其绑定 Goal 的缺失条件；
- Focus 不是业务事实，不能压过用户明确表达；
- Execution Replan 不得改变冻结 Requested Effect；
- 能力不存在不得替换成相似能力；
- 没有 Authority 不提交，没有 Receipt 不成功；
- 旧正式 Owner 在 cutover 后不得继续写入或裁决。

失败序列必须收缩或保存为稳定 replay，并升级成回归案例。测试不比较固定文案，而比较权威对象、状态迁移、调用记录和用户可见结果。
