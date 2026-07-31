# Agent Service

当前 Agent 服务负责 API、Lifecycle、Agent Kernel、领域模块、前端和测试。

## 目录

```text
app/         HTTP / SSE 应用层与唯一 LifecycleCommandRunner
src/         agent_core Kernel 与 agent_modules 领域扩展
frontend/    客户门户
tests/       当前正式测试，按 architecture/context/presentation/runtime/transactions 分类
migrations/  数据库迁移源码
runtime/     本机数据库、上传、日志和向量索引（不进入版本控制或发布包）
scripts/     运行、测试与当前架构守卫
```

## 命令

```bash
bash scripts/test_all.sh
make verify
python scripts/run_api.py
```

正式的本地启动入口 `scripts/run_api.py` 会在 `APP_PROFILE=local` 时幂等安装模块内置知识；直接调用 `uvicorn` 只适合已经完成数据准备的受控运行环境。

跨服务 API 合同位于工作区 `../../contracts/business-api.contract.json`。当前工作区架构与发布边界见根目录 `README.md`。
## 配置

首次启动前执行：

```bash
cp .env.example .env
```

至少填写 `APP_PROFILE=local`、`OPENAI_API_KEY`、`OPENAI_MODEL`。全部变量和生产约束见工作区 `../../docs/operations/CONFIGURATION.md`。
