
# 新增抽象记录

- 记录 ID：`ABSTRACTION-YYYYMMDD-name`
- Change ID：`change-id`
- 新抽象：`path-or-symbol`
- 规则等级：`STRONG_DEFAULT` 或 `REFERENCE_PATTERN`

## 唯一职责

说明该抽象只拥有哪一个边界职责。

## 净收益

比较新增概念、依赖和维护成本与删除、隔离或扩展收益。真正新增能力不强制虚构一对一替换。

## 替换、删除或不适用说明

- 替换/删除项：
- 若没有旧项可替换，说明为何属于真正新增能力：
- 兼容层截止时间与删除条件：

## 不能并入现有 Owner 的原因

说明并入现有 Owner 是否会造成职责污染、依赖反转或 God File。

## 验证与回滚

- 必须证据：
- 复杂度指标：
- 回滚方案：
