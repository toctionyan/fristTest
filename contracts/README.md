# 跨服务合同

- `business-api.contract.json`：机器可读、唯一权威 API 合同。
- `BUSINESS_API_GUIDE.md`：领域安全、身份、幂等与状态机的当前说明。

Agent 与 Business Service 都不得复制此合同到各自服务目录；修改合同必须同步更新两端兼容性测试和 release 清单。
