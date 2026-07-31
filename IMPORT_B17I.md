# B17i 完整源码导入

当前仓库已经安装一次性导入 Workflow。完整源码尚未恢复，当前仍不是 `production_closed`。

## 唯一需要的人工操作

1. 下载经过校验的文件 `b17i_source.tar.xz`。
2. 打开本仓库 `main` 分支根目录。
3. 选择 **Add file → Upload files**。
4. 上传文件，文件名必须保持为：

   `b17i_source.tar.xz`

5. Commit directly to `main`。

## 固定校验值

- 文件大小：971112 bytes
- SHA256：`1a191662c80fc6ab68d91b60e1ffdd0effe916f538acbea9fb052826dbc56e6d`
- 来源阶段：B17i
- 受管文件：1047
- 当前状态：`PHASE_CANDIDATE_ENVIRONMENT_EXECUTION_PENDING`
- `production_closed=false`

## 自动执行结果

上传到 `main` 后，`B17i Verified Source Import` Workflow 会自动：

1. 校验压缩包 SHA256；
2. 拒绝路径穿越、链接和设备文件；
3. 校验 `PHASE_CANDIDATE_MANIFEST.json`；
4. 逐一校验 1047 个受管文件的大小和 SHA256；
5. 用完整 B17i 源码替换临时 Bootstrap；
6. 删除上传的临时压缩包和一次性导入 Workflow；
7. 提交 `Import verified B17i source candidate` 到 `main`。

导入后仍需配置 `production-certification` GitHub Environment 和生产密钥，再手动运行正式发布 Workflow。只有正式 Artifact 明确包含 `production_closed=true` 才算最终关单。
