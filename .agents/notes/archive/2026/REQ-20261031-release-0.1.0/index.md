---
id: REQ-20261031-release-0.1.0
title: 整理代码并提交 0.1.0 版本
status: archived
approval: approved
outcome: completed
modules: [project, release, security, docs]
tags: [release, version, git, cleanup]
created: 2026-10-31
updated: 2026-10-31
archived: 2026-10-31
relations: [{type: related-to, target: REQ-20261016-official-only}, {type: related-to, target: REQ-20261019-notes-lifecycle}]
---

# 整理代码并提交 0.1.0 版本

## 需求与确认来源

- 2026-10-31 维护者要求“整理代码，提交0.1.0版本”，明确授权本次整理、版本收尾与 Git 提交。
- 当前 `pyproject.toml` 已声明 0.1.0，但累计实现、删除、文档和测试尚未形成新提交；旧索引与工作树错位，不能直接提交。
- 审计发现一个未启用实验文件含硬编码访问凭据；不在记录中保存凭据值，按已泄露处理并提示维护者撤销／轮换。

## 目标、非目标与验收

- 目标：整理 0.1.0 的代码、依赖、忽略规则、发布说明和需求归档；安全重建暂存区；运行离线测试与静态编译检查；创建版本提交及 `v0.1.0` 标签。
- 非目标：真实 QQ／天气／JX3BOX 联调、生产部署、恢复已移除的旧功能、多实例实现或改写 Git 历史。
- 验收：提交不含私有环境、PEM、数据库、日志或硬编码凭据；版本与文档一致；离线测试、编译和依赖检查通过；工作区干净；提交和标签指向同一发布版本。

## 提案（proposal）

- 以当前官方服务工作树为发布事实，删除旧框架及泄密实验文件，移除仅供该实验使用的 `requests` 依赖并刷新锁文件。
- 增加更新日志和显式版本说明，补强根目录私有环境及 PEM 忽略规则。
- 按需求记录规范，将所有 `ready-to-archive` 包在本次获授权收尾提交中归档；未完成的多实例需求继续留在 active。
- 不改写历史。历史或旧索引中出现过的真实凭据不能靠本次删除消除，维护者仍须撤销／轮换。

## 决策与驳回（rejection）

无。

## 实施（implementation）

- 已完成：删除含硬编码凭据的未启用实验文件及未经发布审阅的原始响应／图片／提示材料，移除 `requests` 依赖并刷新 `uv.lock`；补强 `.gitignore`；增加 `CHANGELOG.md`、README 版本入口、包版本常量和 CLI `--version`。
- 已修复：开服查询时间格式、群 @ 路由注册、完整第三方响应日志，并同步 README、部署指南和路线图的功能边界。
- 待收尾：归档、暂存区安全复核、提交与标签。

## 验证

| 方法／实际执行命令 | 实际结果 | 未覆盖范围 |
| --- | --- | --- |
| `UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m unittest discover -s tests -q` | 272 项通过 | 不进行真实外部服务联调 |
| `UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m compileall -q src/jx3_bot tests` | 通过 | 语法编译不替代类型检查 |
| `UV_CACHE_DIR="$PWD/.cache/uv" uv pip check --python .venv/bin/python` | 18 个包兼容 | 仅当前 macOS 环境 |
| `git diff --check && git diff --cached --check` | 通过 | 不验证业务行为 |
| `UV_CACHE_DIR="$PWD/.cache/uv" uv build` 并检查 wheel／sdist 清单 | 成功生成 0.1.0 wheel 与 sdist；wheel 仅含运行包及天气模板，未包含实验素材 | 未向公共包仓库上传 |
| 暂存路径与凭据模式审计、本地 Markdown 链接检查 | 无私有环境／PEM／数据库新增；未发现非测试凭据模式；本地链接均可解析 | 不替代历史对象审计或凭据轮换 |
| `UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked jx3_bot --version` | 输出 `jx3_bot 0.1.0` | 不启动服务 |

## 关联与拆分记录

- 关联官方单入口清理与需求记录生命周期；本包只记录发布整理，不重新激活已结束需求。

## 交接与收尾

- 下一步：提交并创建 `v0.1.0` 标签；后续另行安排真实联调、部署验收及凭据历史治理。
- 已固化的现行规范／正式文档：README、CHANGELOG、AGENTS、docs。
- 最终结论及未验证范围：0.1.0 离线发布范围完成；未进行真实 QQ／天气／JX3BOX 联调、生产切换或 Windows 实机验收。
- 提交状态：已获授权并纳入 0.1.0 收尾提交。
- 归档检查：本包及其他已结束包随本次收尾提交整体归档；多实例需求保留 active。

## 关键更新

| 日期 | 实质变化及依据 |
| --- | --- |
| 2026-10-31 | 建立 0.1.0 发布需求；维护者明确授权整理并提交。 |
| 2026-10-31 | 发布前审计发现暂存区错位及硬编码凭据实验文件，先实施安全清理，不直接沿用旧索引提交。 |
| 2026-10-31 | 修复发布审阅发现的开服查询与路由问题；全量 272 项测试、编译和依赖检查通过，进入收尾归档。 |
