---
id: REQ-20261021-qq-ws-raw-payload
title: QQ WebSocket 原始 payload 与所有模式日志不截断不脱敏
status: archived
approval: approved
outcome: completed
modules: ["qq-integration"]
tags: ["qq-integration", "observability", "privacy"]
created: "2026-10-21"
updated: "2026-10-21"
archived: 2026-10-31
relations: [{"type": "follows-up", "target": "REQ-20261011-console-diagnostics"}]
---

# QQ WebSocket 原始 payload 与所有模式日志不截断不脱敏

## 来源、事实及范围

维护者最初指出 payload 级控制台 QQ WS 报文不符合官方原始结构，要求系统级日志打印原始数据。原先的 `qq.ws.payload` 仅输出白名单投影；网关在 JSON 解析后将 `d` 转为受限消息对象，聊天归档和业务事件也不是原始帧。[官方事件订阅文档](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html) 的网关信封包含 `op/d/s/t`（及可选顶层 `id`）；顶层事件 ID 不等同于 `d.id` 消息 ID。

2026-10-21 维护者继续明确：“不要截断”“什么模式下都不要截断吧，日志本来就是用来展示信息的”“脱敏也不要做，这些东西我都没有要求实现”。本阶段复用当前需求包，取消所有日志模式的日志层截断和自动脱敏，而非仅原始 payload 绕过。

目标：显式选择 `BOT_QQ_RECEIVE_LOG=payload` 时逐条输出完整收到的原始 WS 文本，包括畸形文本；所有模式不按字符数、引用条目数／深度或附件数截断，不自动替换凭证、URL 等值。非目标：新增网络请求／响应／Header 日志、持久化原始帧、为业务开放原始 `d`、改数据库保存策略或实际部署联调。默认 summary、级别、聊天与正文开关及 off 行为不变；summary 与 HTTP 仍是既有字段摘要，不代表完整报文。

## 方案、风险与决策

- 原始 WS 文本在网关业务归一化前传递给日志，`raw=` 使用完整 JSON 字符串单行编码，JSON 解码可还原原文，不重序列化原始对象。二进制输入采用 UTF-8 replacement 解码，不承诺非法二进制字节逐字节恢复。
- `safe` 仅转义换行及控制字符，不再调用 `redact` 或限制长度；普通格式器不再设 32768 字符上限，保留异常和堆栈信息并单行转义。event 字段、昵称及身份不再限长；未知事件类型与诊断 code 不再替换为 hidden／unknown。ProviderError.code 取消进入日志前的 40 字符截断。
- 引用内容使用迭代遍历，不限条目或深度；附件类型统计不限数量。仅防御合成循环引用，不对正常 JSON 引用限深。
- `BOT_LOG_MAX_PAYLOAD_CHARS` 废止并忽略，旧非整数／越界值不再因此导致启动失败；生产模板及启动配置日志移除该项。旧阶段的长度限制与仅 payload 不脱敏设计已被最新授权替代。
- `redact` 保留供数据库／业务独立保存策略使用；上游脱敏、截断、有界投影及聊天事务顺序不变。聊天日志不能恢复上游已脱敏、截断、删除或未保存的值；数据源自带截断标记不代表日志二次截断。隐藏正文开关仍有效。
- 日志可能暴露凭证、鉴权值、URL、OpenID、昵称及私人正文；payload 还保留 `message_scene.ext`、附件 URL 与未来未知字段。运维自行治理采集、访问与留存，不将真实敏感日志复制到需求包或文档。
- 1024 条有界队列及 `log.loss` 保留，慢输出仍可能丢整条日志；刷新超时、输出故障或强制终止也可能丢失。完整单条内容不等于无丢失审计，移除字符上限也不保证总字节内存有界。

## 实施与验证

代码调整：[日志实现](<../../../../../src/jx3_bot/observability.py>)、[启动入口](<../../../../../src/jx3_bot/__main__.py>)、[错误模型](<../../../../../src/jx3_bot/errors.py>)；合成回归：[日志测试](<../../../../../tests/test_observability.py>)、[错误日志测试](<../../../../../tests/test_error_observation.py>)。

同步[README](<../../../../../README.md>)、[开发规范](<../../../../../AGENTS.md>)、[部署指南](<../../../../../docs/qq-integration/2026-10-10-deployment.md>)、[生产模板](<../../../../../.env.production.example>)和[需求索引](<../../INDEX.md>)，明确最新授权及上游数据边界。

本阶段实际验证（均为合成数据、MockTransport／FakeSocket／临时数据库，不连接真实 QQ／天气接口）：

| 命令 | 结果 | 范围 |
| --- | --- | --- |
| `UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m unittest discover -s tests -p test_observability.py -q` | 43 tests，1.281s，OK，exit 0 | 三种模式、60000 字符完整日志／payload／异常堆栈、1200 深引用与条目／附件、上游持久化独立性 |
| `UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m unittest discover -s tests -q` | 254 tests，3.134s，OK，exit 0 | 全量离线回归，含错误 code 无截断测试 |
| `UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m compileall -q src/jx3_bot tests` | exit 0 | 静态编译 |
| `git diff --check` | exit 0 | 已跟踪差异空白检查；不覆盖未跟踪文件，另读代码／测试及当前文档核对 |

测试初次遇到兼容导出不存在导致导入失败，已改为实际 QQ 模型模块导入后重新通过。上一轮 36 项日志测试及 246 项全量测试仅对应历史限长实现，本轮以此表为准。

## 结论及关联

后续于 [REQ-20261011-console-diagnostics](<../REQ-20261011-console-diagnostics/index.md>)，不修改旧需求结论。本阶段已完成代码、回归测试与现行文档同步，等待获授权的收尾提交，未自动归档。未读取真实配置／密钥／生产数据库，未启动服务或真实连接 QQ／天气接口，未修改私有环境配置或提交；部署重启与真实联调尚未执行。
