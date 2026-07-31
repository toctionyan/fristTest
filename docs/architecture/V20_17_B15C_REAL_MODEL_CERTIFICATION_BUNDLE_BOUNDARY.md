# V20.17 B15c：真实模型认证汇总边界

## 目标

把 B15a smoke、B15b1 语义原型和 B15b2 完整生命周期三类真实模型认证绑定成一次不可拼接、不可重放的最终认证。三类组件必须在同一个随机 session、同一个工作区指纹和同一个官方 Provider 身份下现场执行。

## 新增项

新增 `real_model_certification_bundle` 单一认证汇总模块和 `verify_real_model_certification_bundle.py` 控制器。控制器先验证官方 Provider 身份，再计算便携工作区 SHA-256，生成随机 session ID，并直接启动 smoke、semantic、lifecycle 三个组件。三个组件通过受保护环境字段返回 session、工作区和身份摘要证明。

## 唯一职责

该控制器只裁决“这三类真实模型证据是否来自同一次现场认证”。Provider 身份仍由 `real_model_identity` 裁决；语义正确性仍由独立 Goal Oracle 裁决；完整生命周期仍由公开 HTTP Harness 裁决。汇总控制器不重做这些判断，也不新增模型调用通道。

## 替换或删除项

替换“人工收集三份独立 PASS JSON 后即可宣称真实模型已认证”的分散流程。最终认证入口不接受历史证据文件、旧 evidence path、外部 PASS JSON 或混合模型结果。独立脚本仍可用于阶段诊断，但 `mode=standalone` 的结果不能升级为最终 bundle。

## 删除证据

- 旧树不存在汇总模块，新增对抗桥在正式红基线上失败。
- session ID 不同、工作区指纹不同、Provider/模型/凭据指纹不同均被拒绝。
- 缺少 smoke、semantic 或 lifecycle 任一组件时被拒绝。
- semantic 原型覆盖不足、生命周期未逐轮认证、只读认证产生事务写入时被拒绝。
- 缺少真实 API key 时在启动任何组件前返回 `BLOCKED_BY_ENVIRONMENT`，`components_started=0`。

## 验证

1. 定向反例覆盖三组件匹配、身份错配、session 重放、工作区错配、组件缺失和覆盖不足。
2. 对抗桥纳入标准 runtime counterexamples，旧 Baseline 声明为 FAILED，修复后同一声明必须 VERIFIED。
3. B15a/B15b1/B15b2 相关回归全部执行，确保新增 session 证明不削弱各组件原有身份与响应认证。
4. 当前无真实 Key 环境只验证 fail-closed 控制器；真实 Provider 最终 PASS 仍需在持有官方凭据的合格环境现场执行。
