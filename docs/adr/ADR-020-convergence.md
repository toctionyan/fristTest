# ADR-020：从追加式治理转为收敛式架构

- 状态：Accepted
- 决策：不再通过新增 Runtime、Facade、Guard 或历史兼容层来处理局部问题。所有新增抽象必须替换或删除一个已有抽象；没有替代关系则拒绝。
- 结果：Lifecycle、Presentation、Transaction 成为唯一边界；Skill 只有五条硬规则和一个统一验收器。
