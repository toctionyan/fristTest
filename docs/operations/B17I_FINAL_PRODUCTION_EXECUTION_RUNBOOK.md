# B17i 最终生产认证执行手册

本手册只负责把 B17i 候选放入真实 GitHub 仓库并执行现有 `production-certification-release`。它不允许用本地模拟、占位密钥或手工改 JSON 的方式生成 `production_closed`。

## 1. 仓库内容

解压交付 ZIP 后，把**最外层工程目录内部的全部内容**放到 GitHub 仓库根目录。仓库根目录必须直接看到 `.github/`、`services/`、`scripts/`、`governance/`、`README.md` 和 `VERSION`，不能再多包一层目录。

默认分支必须是 `main`。提交后保持工作树干净，不要在 Workflow 运行期间改写同一提交。

## 2. 保护 main

为 `main` 启用分支保护或 Repository Ruleset，至少要求：

- 禁止直接强推和删除；
- 只有受控合并才能更新 `main`；
- `main` 在 GitHub 上必须显示为 protected；
- 最终认证必须从 `main` 的当前提交手工触发 `workflow_dispatch`。

`release-admission` 会在无密钥 Job 中验证事件、分支、protected 标记和输入。无论 PASS、FAIL 或环境阻断，它都会上传 `production-release-admission-<run_id>-<attempt>`，内部必须只有脱敏的 `release-admission-result.json`。

## 3. 创建受保护 Environment

在仓库 Settings → Environments 中创建：

`production-certification`

建议配置 Required reviewers，并把允许部署的分支限制为 protected `main`。

添加三个 Environment secrets：

- `PRODUCTION_MODEL_API_KEY`：真实聊天模型 Provider 密钥；
- `PRODUCTION_EMBEDDING_API_KEY`：真实 Embedding Provider 密钥；
- `QUALITY_EVIDENCE_SIGNING_KEY`：至少 32 字节的随机签名密钥。

可选 Environment variable：

- `PRODUCTION_EMBEDDING_API_BASE`：默认 `https://api.openai.com/v1`。

不要把真实密钥写入 `.env`、仓库文件、Issue、PR、Workflow 输入或聊天记录。

## 4. 触发 Workflow

进入 Actions → `production-certification-release` → Run workflow，选择 `main`。

推荐的 DeepSeek 输入：

- `provider`: `deepseek`
- `model`: `deepseek-v4-flash` 或 `deepseek-v4-pro`
- `embedding_model`: `text-embedding-3-small`
- `embedding_dimension`: `1536`

不要再使用已被工程拒绝的 `deepseek-chat` 或 `deepseek-reasoner` 兼容别名。

使用 OpenAI 时，`provider` 选择 `openai`，`model` 填写当前账号实际可调用的官方 API 模型 ID；Embedding 模型和维度必须与真实响应一致。

## 5. 运行结果判断

先检查 `release-admission`：

- `PASS`：允许进入受保护 Environment；
- `FAIL`：分支、事件或输入非法，必须修正后新开一次 Run；
- `BLOCKED_BY_ENVIRONMENT`：GitHub 上下文缺失，不得继续。

随后检查 `protected-release`。失败时下载：

`production-certification-evidence-<run_id>-<attempt>`

其中应包含可用阶段的脱敏预检、工具链、Quality Loop 和发布控制结果。任何 `BLOCKED_BY_ENVIRONMENT` 或 `FAIL` 都不允许重命名为成功。

只有整个 Workflow 成功时才会出现：

`production-closed-<commit_sha>-<run_id>-<attempt>`

## 6. 最终关单验收

正式关单包必须同时满足：

1. `production-release-result.json` 状态为成功；
2. `production_closed` 为 `true`；
3. 源码包、证据包和 SHA256 文件齐全；
4. 证据中的 repository、commit、run ID、run attempt、工具链指纹、模型身份、数据库镜像身份全部属于同一次 Run；
5. ZIP 能安全解包，无路径穿越、重复路径、符号链接和 CRC 错误；
6. 没有 `.env`、数据库、缓存、密钥或运行态目录进入正式源码包。

若没有 `production-closed-*` Artifact，项目仍是阶段候选，不能宣称正式生产关单。
