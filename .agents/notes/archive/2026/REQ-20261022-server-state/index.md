---
id: REQ-20261022-server-state
title: 剑网3开服状态查询
status: archived
approval: approved
outcome: completed
modules: [api, qq-integration]
tags: [server-state, command, jx3box]
created: 2026-10-22
updated: 2026-10-29
archived: 2026-10-31
relations: []
---

# 剑网3开服状态查询

## 需求与确认来源

- 用户要求在 `src/jx3_bot/modules/api/` 实现“开服 服务器名称”，请求指定 JX3BOX 接口，按 `server_name` 匹配，依据布尔 `connect_state` 回复；开服时格式化秒级 `maintain_time`。
- 已检查工作区，存在大量既有暂存／未暂存／未跟踪内容；保留原状，不提交、不清理。API 目录现有探索脚本不属于自动发现的 handlers，保持不动，不复用其中凭据。
- 现有路由支持业务自动发现与显式服务注入；HTTP 使用已有 httpx 异步客户端，无须新增依赖。

## 目标、非目标与验收

- 目标：群普通消息、群@消息、单聊支持精确“开服”关键字及空格参数，遵循全局命令前缀；精确匹配服务器名称；区分已开服、未开服、未找到、参数错误、接口故障。
- 非目标：别名／模糊匹配、主动推送、定时轮询、数据库变更、天气额度变更、其他探索脚本整理、真实QQ部署。
- 验收：MockTransport 和合成QQ事件离线测试，覆盖网络／HTTP／JSON异常及响应字段边界；不调用真实QQ或天气接口。

## 提案（proposal）

- 新增 API 包标识、service 与 handlers；应用入口装配 ServerStateService，复用现有 HTTP 客户端生命周期与直连、超时、无重试策略。
- GET `https://spider2.jx3box.com/api/spider/server/server_state` 获取数组，在本地精确匹配 `server_name`，不猜测远端过滤参数。
- `connect_state` 严格按 JSON 布尔判定；true 的 `maintain_time` 按秒级 Unix 时间戳、Asia/Shanghai 转换为 `YYYY-MM-DD HH:mm:ss`，标注“维护时间”，不将其冒称开服时间。无效时间保留已开服事实并提示维护时间不可用。
- 风险：上游接口和字段语义来自用户样例，真实可达性／响应未验证；不把上游错误当未开服。北京时间为本次实现采用的明确约定。

## 决策与驳回（rejection）

无驳回。

## 实施（implementation）

- 新增 [API包](<../../../../../src/jx3_bot/modules/api/__init__.py>)、[开服服务](<../../../../../src/jx3_bot/modules/api/service.py>)、[业务处理器](<../../../../../src/jx3_bot/modules/api/handlers.py>)；通过 [入口](<../../../../../src/jx3_bot/__main__.py>) 显式注入共享异步HTTP客户端。
- 新增 [服务测试](<../../../../../tests/test_server_state.py>)，扩展 [路由测试](<../../../../../tests/test_routing.py>)；[README](<../../../../../README.md>) 同步命令、时区、接口及未联调边界。
- 关键决定：保留现有探索脚本不动；不新增依赖／配置／数据库；严格判定布尔值，坏时间不抹掉已开服状态；示例秒级时间戳 `1744238700` 的北京时间为 `2025-04-10 06:45:00`。
- 离线交付完成，无阻塞；尚未真实请求上游接口或启动QQ服务。

## 验证

所有 uv 命令均设置 `UV_CACHE_DIR="$PWD/.cache/uv"`，未读取生产凭据。

| 方法／实际执行命令 | 实际结果 | 未覆盖范围 |
| --- | --- | --- |
| `uv run --locked python -m unittest discover -s tests -p test_server_state.py -v` | 11项通过；最终全量另含畸形数组元素用例 | 真实上游可达性／响应 |
| `uv run --locked python -m unittest discover -s tests -p test_routing.py -v` | 协作测试执行12项通过，含新增3项路由测试 | 真实QQ事件与投递 |
| `uv run --locked python -m unittest discover -s tests -v` | 最终268项通过 | 部署／生产平台联调 |
| `uv run --locked python -m compileall -q src/jx3_bot tests` | 通过 | lint／类型检查未配置 |
| `uv pip check --python .venv/bin/python` | 17个包依赖一致 | 无依赖变更 |
| `git diff --check -- README.md src/jx3_bot tests .agents/notes` | 通过；仅检查Git已跟踪差异，新文件另已审阅 | 不代表审核其他既有变更 |

## 2026-10-29 返回格式调整

- 用户要求统一预处理状态与时间，开服时显示“开服时间”，维护时显示“维护中”和“维护时间”；两种状态均使用上游 `maintain_time`，无效时显示“不可用”。上游字段的实际语义未验证。保留既有空参数默认绝代天骄行为。
- 更新服务、离线测试及 README；不变更路由注册或真实平台配置。
- `UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m unittest discover -s tests -p test_server_state.py -v`：13 项通过。
- 同样运行 `-p test_routing.py`：12 项中 1 项失败（两个群 @ 子用例）；现有 handlers 的群 @ 注册已被注释，未在本次范围内修改。其余路由测试通过；未执行真实上游或 QQ 联调。

## 交接与收尾

- 下一步：如需上线，在授权环境验证上游接口、QQ群／私聊事件及回复；本次未启动真实服务。
- 现行使用说明已固化到README；约定范围离线交付完成。
- 提交状态：已获授权并纳入 0.1.0 收尾提交；保留 active，标记 ready-to-archive，不自动归档。
