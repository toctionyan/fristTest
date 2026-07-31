---
name: adversarial-review
description: 候选修改完成后使用。盲审 Diff，寻找规则迎合、旧路径回归、测试过拟合、无价值抽象和更简单删除方案；保持只读。
---

# Adversarial Review

优先检查：

- 是否修改测试或裁判迎合实现；
- 是否保留两个正式权威；
- 是否只修公开样本；
- 是否增加 Adapter/Registry 而不删除旧抽象；
- 是否存在零修改或回滚更合理的方案；
- 是否超出 Change Contract；
- 是否用历史 Evidence 或未重跑 Issue 宣称完成。

输出结构化阻断项和证据，不直接修改代码。
