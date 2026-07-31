# Business Service

独立业务服务负责业务事实、权限、资格、状态机、幂等和最终写入；它不理解自然语言，也不保存 Agent 的 Draft/Grant/Attempt/Receipt。

```text
business_service/  API、领域应用、持久化与安全
scripts/           本地运行与完整性校验
tests/             业务服务测试
runtime/           本机业务数据库（不进入版本控制或发布包）
```

```bash
python -m pytest -q tests
python scripts/run_business_api.py
```

跨服务 API 合同位于工作区 `../../contracts/business-api.contract.json`。
## 配置

首次启动前执行：

```bash
cp .env.example .env
```

本地 Agent 与 Business Service 的 `BUSINESS_SERVICE_TOKEN` 必须相同。完整说明见工作区 `../../docs/operations/CONFIGURATION.md`。
