---
name: release-certification
description: 候选冻结后使用。通过外置可信 Judge 运行累积 Profile，校验版本、Evidence、宿主和产物身份；不进行修复。
---

# Release Certification

- 候选必须是不可变 commit 或明确工作树指纹。
- 受保护认证要求 `SKILL_TRUSTED_JUDGE_ROOT` 指向工作区外的只读 Judge。
- 验证 Skill Profile、Host Conformance、Security 和 Project Compatibility。
- 不允许在认证阶段修代码；失败返回修复、环境阻断、Oracle 审查或回滚建议。
