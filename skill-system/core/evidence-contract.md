
# Evidence Contract

最终证据必须绑定：

- Change ID 和 Target 类型；
- 当前提交或工作树指纹；
- Change Contract 指纹；
- Policy 与 Profile 指纹；
- Judge 版本、位置和指纹；
- 执行命令、时间、环境摘要和退出码；
- Gate 结果、Issue 状态和未验证项；
- Reviewer 结论；
- 客服生产代码是否发生变化。

旧 Evidence、复制的 JUnit 或只跑定向依赖闭包都不能关闭当前目标。
