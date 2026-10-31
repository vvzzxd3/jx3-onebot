---
id: REQ-20261031-windows-support
title: Windows 运行兼容与依赖审计
status: archived
approval: approved
outcome: implemented-offline
modules: [project, runtime, qq-integration]
tags: [windows, dependencies, locking, asyncio, timezone]
created: 2026-10-31
updated: 2026-10-31
archived: 2026-10-31
relations: []
---

# Windows 运行兼容与依赖审计

## 需求与确认来源

- 需求摘要与来源：维护者要求检查项目在 Windows 无法运行的问题，并核查引入包是否存在系统限制。
- 明确批准事项：本次直接排查并修复可由离线测试验证的平台兼容问题。
- 当前事实：受支持入口在 `instance_lock` 中仅使用 `fcntl` 并明确拒绝 Windows；`run_service` 直接调用事件循环的 `add_signal_handler`，Windows 默认事件循环不支持；项目依赖中未声明 Windows 通常需要的 IANA 时区数据回退包。
- 待核实：尚无真实 Windows 主机与真实 QQ／天气接口联调环境，最终安装、网络和服务停止行为仍需在 Windows 实机验收。

## 目标、非目标与验收

- 目标：确认直接及传递依赖的平台可用性；为单实例锁、停止信号和 IANA 时区数据补齐 Windows 兼容；增加可在当前平台离线执行的回归测试；同步运行文档。
- 非目标：不进行真实 QQ／天气联调，不改变数据库原子边界、代理策略、多实例设计或业务功能。
- 验收条件：锁文件在 POSIX 保持原语义并具有 Windows 后端测试；不支持 `add_signal_handler` 时可回退到 `signal.signal`；锁文件包含 Windows 可安装的时区数据依赖；相关回归、编译和依赖检查通过，并执行完整离线测试以记录仓库现状。实机 Windows 验收单独标为未完成。

## 提案（proposal）

- 保留现有无第三方锁依赖的设计：POSIX 继续使用 `fcntl.flock`，Windows 使用标准库 `msvcrt.locking` 锁定锁文件首字节。
- 将服务停止信号注册封装为兼容上下文：优先使用 asyncio 事件循环信号处理，遇到 Windows 不支持时回退标准库 `signal.signal`，退出时恢复原处理器。
- 增加 `tzdata` 直接依赖，为 Windows 缺少系统 IANA tzdb 的常见环境提供 `ZoneInfo` 数据。
- 不因平台兼容引入 `portalocker` 等额外依赖；现有 httpx、websockets、python-dotenv、PyJWT、cryptography、requests 及其锁定传递依赖均有通用 wheel 或 Windows wheel，未发现它们主动排除 Windows。

## 决策与驳回（rejection）

| 对象 | 决定 | 原因与确认来源 | 适用条件 | 替代方向／重评条件 |
| --- | --- | --- | --- | --- |
| 新增第三方文件锁包 | 不采用 | 标准库可覆盖当前单文件、单实例锁需求，避免扩大供应链 | 当前锁语义不变 | 若后续需要网络文件系统或更复杂共享锁再评估 |

## 实施（implementation）

- [官方入口](<../../../../../src/jx3_bot/__main__.py>)：POSIX 保留 `fcntl.flock`；Windows 使用 `msvcrt.locking(..., LK_NBLCK, 1)` 锁定二进制锁文件首字节，退出时显式解锁并保证关闭句柄；保留数据库 canonicalize、公共服务／导入锁和硬链接拒绝语义。
- [官方入口](<../../../../../src/jx3_bot/__main__.py>)：新增停止信号上下文，优先使用事件循环信号 API；Windows 等不支持环境回退到 `signal.signal`，通过 `loop.call_soon_threadsafe(stop.set)` 通知循环并恢复旧处理器。
- [项目配置](<../../../../../pyproject.toml>)与[锁文件](<../../../../../uv.lock>)：新增并锁定平台无关的 `tzdata 2026.6`，保证 `ZoneInfo("Asia/Shanghai")` 及城市命名时区在无系统 tzdb 的 Windows／精简环境可用。
- [回归测试](<../../../../../tests/test_config_models.py>)：模拟 Windows `msvcrt` 后端和不支持 `add_signal_handler` 的事件循环，验证首字节锁定／释放、停止通知与处理器恢复；现有 POSIX 竞争和别名测试继续覆盖原语义。
- [回归测试](<../../../../../tests/test_regressions.py>)与[CLI测试](<../../../../../tests/test_chat_config_cli.py>)：符号链接／硬链接能力受 Windows 权限或文件系统限制时按实际能力跳过，不再把缺少创建权限误判为运行代码失败。
- [README](<../../../../../README.md>)、[部署指南](<../../../../../docs/qq-integration/2026-10-10-deployment.md>)与[开发规范](<../../../../../AGENTS.md>)：记录 Windows x64 支持边界、PowerShell 验证命令、NTFS ACL 要求、Windows ARM64 限制及实机未验收事实。
- 与原方案的偏差及原因：无；未采用第三方锁包，未改变数据库事务或业务行为。
- 已完成／未完成／阻塞：离线实现与审计完成；Windows 实机、真实 QQ／天气联调和 Windows CI 仍未完成，不阻塞本次离线交付。

## 依赖审计结论

- Windows x64＋CPython 3.12：`httpx`、`websockets`、`python-dotenv`、`PyJWT`、`cryptography`、`requests` 及其锁定传递依赖均有 Windows wheel 或平台无关 wheel；禁止源码构建的 `uv` 交叉解析成功。
- Windows ARM64：`cryptography 48.0.1` 没有 `win_arm64` wheel；`--no-build` 交叉解析明确失败。源码构建还需要 Rust、OpenSSL 和 Visual C/C 编译环境，且不属于当前开箱支持范围。
- `cffi`、`charset-normalizer` 有 Windows ARM64 wheel；`websockets` 可使用平台无关 wheel。锁文件没有排除 Windows 的 `sys_platform`／`platform_machine` 标记。
- `setuptools` 是构建隔离依赖而非 Windows 专属限制；严格离线构建仍需预缓存，与本次 Windows 运行阻断不同。
- 项目自身原有的 `fcntl`、asyncio 信号 API 与缺失 `tzdata` 才是 Windows x64 的三项直接阻断，现已在代码和依赖中处理。

## 验证

| 方法／实际执行命令 | 实际结果 | 未覆盖范围 |
| --- | --- | --- |
| `uv lock` | 成功，解析 18 个包，新增 `tzdata 2026.6` | Windows 实机安装 |
| `uv sync --locked --dry-run --no-install-project --no-build --python-platform x86_64-pc-windows-msvc --python 3.12` | 成功；Windows x64 锁定依赖均有二进制／通用发行物 | 仅静态交叉解析，不执行 Windows 代码 |
| 同命令改为 `aarch64-pc-windows-msvc` | 按预期失败：`cryptography 48.0.1` 无二进制发行物 | 未尝试复杂的 Windows ARM64 源码构建 |
| 6 项锁、信号、符号链接／硬链接定向回归 | 6 项通过 | `msvcrt` 为模拟后端，非真实 NTFS 竞争 |
| `uv run --locked python -m unittest discover -s tests -v` | 执行 272 项；Windows 相关新增／既有回归通过；仓库现有开服业务与路由共 10 项失败，失败文件为 `test_server_state.py`／`test_routing.py`，与本次未修改的开服实现输出约定有关 | 完整套件尚非全绿；不把既有开服失败冒称本次通过 |
| `uv run --locked python -m compileall -q src/jx3_bot tests` | 通过 | 不替代 Windows 解释器编译 |
| `uv pip check --python .venv/bin/python` | 18 个包一致，全部兼容 | 当前主机环境 |
| `uv run --locked python -c "from zoneinfo import ZoneInfo; ..."` | `ZoneInfo('Asia/Shanghai')` 成功，`tzdata 2026.6` 可导入 | Windows 系统 tzdb 回退路径需实机确认 |

## 关联与拆分记录

- 小需求，仅维护本主记录。

## 交接与收尾

- 下一步：在 Windows x64＋Python 3.12 实机执行部署指南中的 `uv sync --locked --no-build`、完整测试、CLI smoke 和模拟网络的服务启动／停止；另行修复当前开服业务／路由 10 项既有失败。若计划支持 Windows ARM64，需更换具有 ARM64 wheel 的密码学版本或正式维护可复现源码构建链。
- 已固化的现行规范／正式文档：[README](<../../../../../README.md>)、[部署指南](<../../../../../docs/qq-integration/2026-10-10-deployment.md>)和[AGENTS](<../../../../../AGENTS.md>)已同步支持边界、实机待验证、ACL 与 ARM64 限制。
- 最终结论及未验证范围：Windows x64 的依赖层和已知代码阻断已完成离线修复；Windows ARM64 不属于开箱支持，Windows 实机文件锁、Proactor 停止行为、NTFS 路径／ACL、QQ／天气网络仍未验证。
- 提交状态：已获授权并纳入 0.1.0 收尾提交，未提交。
- 归档检查：离线实施完成，保留在 active 并标记 `ready-to-archive`；未经授权不提交、不归档。

## 关键更新

| 日期 | 实质变化及依据 |
| --- | --- |
| 2026-10-31 | 建档；确认首要阻塞来自项目自身 `fcntl` 限制与 Windows asyncio 信号差异，依赖侧需补 `tzdata`。 |
| 2026-10-31 | 完成 `msvcrt` 锁、信号回退、`tzdata`、能力型链接测试、Windows 文档和依赖交叉解析；Windows x64 静态依赖验证成功，ARM64 因 `cryptography` 无 wheel 明确排除；完整套件记录 10 项与本需求无关的既有开服失败，状态转 `ready-to-archive`。 |
