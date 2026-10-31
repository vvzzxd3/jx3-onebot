> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/project/2026-10-16-uv-run-no-build-backend.md`。

# 取消 setuptools 与单独 uv sync 的启动方式

- 所属模块：project（跨运行入口、依赖和开发流程）
- 建档日期：2026-10-16
- 最近更新：2026-10-16
- 状态：维护者要求停止；已撤销试验性代码与锁文件改动，原入口方案保留
- 需求来源：维护者直接要求“能不能取消掉 uv sync 和 setuptools，直接在项目根目录 uv run jx3_bot 启动”。
- 关联记录：[上次可安装包方案](<../../../REQ-20261016-uv-entry/legacy/project/2026-10-16-uv-entry-config-data-cleanup.md>)、[部署指南](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>)。

## 背景、事实与边界

当前 `pyproject.toml` 使用 setuptools 构建并注册 `jx3_bot` console script，uv 默认 `.venv` 中有已安装项目；`uv run jx3_bot` 会自动同步项目环境，因此不需要先手动 `uv sync`。但是完全删除项目的构建后端后，现有 console script 将不会由 uv 为当前项目安装，不能仅凭 `[project.scripts]` 假定无后端的 `uv run jx3_bot` 仍然可用。项目主体保留 `src/jx3_bot/`，模板读取方式和数据库状态不变。

目标：从用户的操作中移除单独 `uv sync`；如果技术上可行，从项目移除 setuptools（不换成另一个打包后端），但必须保证**干净环境**下在仓库根目录 `uv run jx3_bot` 仍能找到正确入口与依赖；不执行真正联网服务。非目标：改造 QQ／天气业务、读写私有生产配置／数据库、修改 Git 暂存区或改写先前阶段历史。若不能同时实现上述目标，应清楚说明限制而不是提供依赖旧 `.venv` 的假成功。

## 待验证方案

1. 核实 uv 的无构建后端项目环境行为，并在无旧脚本环境中检验命令发现；严禁用 `uv run jx3_bot` 裸命令启动 QQ。
2. 考虑仓库根目录可执行 `jx3_bot` 启动脚本和 `PYTHONPATH` 的 uv 项目环境配置；优先纯仓库配置方案，不依赖用户设置 PATH 或 shell 激活。检查进程工作目录、命令参数、天气模块资源和测试调用兼容性。
3. 依据实际验证修订入口、锁文件、README／部署／开发规则；审阅生成 `egg-info` 的处理及本地链接。保留生产配置和 SQLite 原状。

## 风险与验收

`uv run` 对普通命令只从执行环境及 `PATH` 查找；没有构建后端时 `[project.scripts]` 并不会变成可执行文件。旧 `.venv/bin/jx3_bot` 可能掩盖问题；必须使用隔离的临时 uv 项目环境或等价干净环境进行离线 `--help` 验证。没有打包后端意味着该仓库不再可通过标准 Python wheel 方式安装，这属于明确的功能取舍。依赖锁文件仍由 uv 维护，首次运行需下载缺失的依赖；天气真实请求和 QQ 连接均不在验收范围内。

## 实测结果与决策

对 `pyproject.toml` 临时设置 `[tool.uv] package = false`、移除 build-system 后，`uv.lock` 的项目源变为 `virtual = "."`，在独立的干净虚拟环境（`.cache/uv/launch-smoke-env`）执行 `uv run --locked --offline jx3_bot --help` **报 `Failed to spawn: jx3_bot`**。当前默认 `.venv` 残留前次 setuptools 安装的 `.venv/bin/jx3_bot`，同命令在该旧环境中成功是**假阳性**。根目录新增可执行启动脚本后，`uv run --locked --offline ./jx3_bot --help` 在干净环境成功，说明允许 `./` 则可去掉打包后端。但 `uv run jx3_bot` 不自动搜索当前目录；不应通过遗留环境、全局 PATH 污染或复制脚本进虚拟环境冒充需求完成。

询问取舍时，维护者明确回复“停止任务”，故立即终止改造。试验添加的根目录脚本已删除；`pyproject.toml` 已恢复 setuptools 后端和 console script，`uv.lock` 已离线恢复并通过 `uv lock --check --offline`。README／部署指引未改动，原有 `uv run jx3_bot` 方式继续有效；本次没有运行裸服务、读私有配置或数据库，也没有改 Git 暂存区。独立临时虚拟环境仅作为工作区内 uv 缓存保留，不用于现有启动。若未来继续，须由维护者重新确认命令与后端之间的取舍。

## 变更历史

| 日期 | 变化 | 依据 |
| --- | --- | --- |
| 2026-10-16 | 建档并记录目标、限制及验证门槛 | 维护者本轮直接要求 |
| 2026-10-16 | 用干净环境验证无后端方案失败；维护者要求停止后撤销试验改动 | 不让旧 `.venv` 掩盖启动错误；尊重停止指令 |
