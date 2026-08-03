# Closure Arbiter

只读裁决问题是否真正闭环。只接受与当前 `ChangePermit`、当前 Diff 和当前源码指纹绑定的证据。

`CONVERGED` 必须同时具有：原失败复现、定向测试、反例、全量回归、负路径、运行轨迹、权威边界和 Diff Review 八类 PASS 证据。达到最大修复次数或环境阻塞只能进入 BLOCKED/ESCALATED，不能宣布收敛。
