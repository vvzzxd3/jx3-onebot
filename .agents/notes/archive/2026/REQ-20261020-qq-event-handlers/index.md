---
id: REQ-20261020-qq-event-handlers
title: QQ 消息事件封装与业务处理装饰器
status: archived
approval: approved
outcome: implemented-offline
modules: [qq-integration, core, weather, chat-history]
tags: [qq-event, business-handlers, matching, architecture]
created: 2026-10-20
updated: 2026-10-20
archived: 2026-10-31
relations: [{type: follows-up, target: REQ-20261011-matcher-templates}, {type: related-to, target: REQ-20261013-chat-history}, {type: related-to, target: REQ-20261011-console-diagnostics}]
---

# QQ 消息事件封装与业务处理装饰器

## 需求与确认来源

- 2026-10-20 维护者提出：QQ 官方 WS 基于事件，要求明确事件封装，按事件区分控制台日志，尤其让新增业务只需函数上的装饰器及参数即可处理群聊、单聊与群 @ 消息。借鉴 [NoneBot Matcher](https://nonebot.dev/docs/tutorial/matcher)、[Handler](https://nonebot.dev/docs/tutorial/handler)、[事件信息注入](https://nonebot.dev/docs/tutorial/event-data)，但项目仍直接接入 QQ 官方 API，不恢复 NoneBot/OneBot。
- 同日维护者明确：`GROUP_AT_MESSAGE_CREATE` 是群 @ 机器人事件，收到的 `content` 已去掉 @ 机器人前缀；单聊不存在 `AT_ME`。业务侧直接采用 `@on_group`、`@on_private`，不暴露内部 `scene`；群 `at_me` 默认 `False`，仅显式 `True` 匹配 `GROUP_AT_MESSAGE_CREATE`，`False` 匹配 `GROUP_MESSAGE_CREATE`，无第三种取值。`command`／`fullmatch` 改为 `match`：完整关键字可单独出现（`args=""`），或接空白＋非空参数（后段传入 `args`）；可配置／留空 `command_prefix`。同一函数可叠加群聊与私聊装饰器，首次命中该函数后不再检测它的其他装饰器；业务不必填写 `name`。启动时须自动发现并注册业务模块；重启前未触发的业务不补执行，无参数指令仍须交给处理函数以供未来使用默认值。实施验收后固化到开发指导。
- 实施前事实：网关帧由[网关](<../../../../../src/jx3_bot/gateway.py>)接收，消息由[QQ 事件解析](<../../../../../src/jx3_bot/integrations/qq/events.py>)处理，旧业务规则由[匹配器](<../../../../../src/jx3_bot/matching.py>)执行；[存储](<../../../../../src/jx3_bot/store.py>)在同一事务中写入聊天记录、inbox 任务以及网关会话 ID／接收序号；实施前尚无所需业务装饰器。维护者的一次真实运行记录中，同一 trace 只有一条 `GROUP_MESSAGE_CREATE` 与一次 `accepted`；此样本未见第二种事件或重复提交，不可据此推断所有消息的推送规律。

## 目标、非目标与验收

- 目标（已获维护者“开始实施方案”授权并离线实现）：QQ 接入事件分类和安全投影、群聊／单聊装饰器、仅群适用且默认 `False` 的 `at_me`、`match`／`args`／`command_prefix`、启动自动发现注册及受限业务服务注入；新业务无需逐个修改入口，可单独离线测试；固化开发指导。
- 非目标：日志改造，恢复 NoneBot／OneBot 依赖、多平台兼容、主动消息、运行时热加载、改变聊天历史／预算／数据库绑定、凭空支持未实现 QQ 事件，或把未联调当成已验证。
- 验收建议：带参／无参天气指令的 `args`、前缀及关键字词边界、同一函数多个装饰器只执行一次、两类群事件分别按 `at_me=True/False` 匹配、私聊、启动自动发现、优先级与阻断、重启前旧 inbox 业务终结而不重放、已写入 outbox 的回复记录按原状态与发送限制处理、聊天记录／任务／网关会话 ID 和接收序号同事务写入以及落库失败时序号不前进。

## 类别材料

| 类别 | 记录 |
| --- | --- |
| proposal（保留获批准前的设计原文） | [详细架构与 API 提案](<proposal.md>) |

## 当前决策、风险与待确认

- 已明确：群 @ 由事件 `GROUP_AT_MESSAGE_CREATE` 判定；单聊没有 `AT_ME`；业务方使用 `@on_group`/`@on_private`，内部 `scene` 不作为业务侧必填参数。
- 已实施：群 `at_me` 默认 `False`、无需业务 `name`；`match` 无参指令传 `args=""`，后接空白和参数时传该参数；前缀可配置／留空；多装饰器单函数仅首次命中；启动扫描可信业务包、显式注入服务。重启时旧 inbox 业务不补执行，不因此删除已写入 outbox 的回复记录、已归档聊天记录，也不重置保存的网关会话 ID／接收序号；细节见[开发指导](<../../../../../AGENTS.md>)与[现行路由](<../../../../../src/jx3_bot/routing.py>)。
- 待验证事实：维护者提供的一次真实运行摘录只出现一种群消息事件与一次 `accepted`，不代表平台总规律；不因推测修改匹配、去重或消息发送。新增专用诊断日志已按维护者要求撤回，日志改造不属于本方案。
- 非消息事件的新增业务能力需要另行设计安全持久化投影，不把未知原始 `d` 直接交给处理函数。

## 决策与驳回（rejection）

无已确认驳回；评审中方案比较不冒称维护者驳回。

## 实施（implementation）

- 曾临时添加群消息专用收帧诊断行及三项专用合成测试；维护者提供一次真实运行观察后，明确要求**连同测试和提案中的日志设计一并撤回**。已在[观察器](<../../../../../src/jx3_bot/observability.py>)撤回专用日志，并在[日志测试](<../../../../../tests/test_observability.py>)移除这三项测试；既有 `qq.ws.receive`、消息处理、去重与日志能力保持不变。未修改装饰器、匹配、持久化、正式文档、依赖、数据库或私有配置；未授权启动、上线、提交或归档。
- 维护者随后明确“开始实施方案”；实现[业务路由](<../../../../../src/jx3_bot/routing.py>)与安全[群／私聊事件投影](<../../../../../src/jx3_bot/integrations/qq/models.py>)，自动发现可信 `modules/*/handlers.py` 并按应用构建独立注册表，仅 `Inject` 声明的注册服务可进入处理函数。天气三个命令迁入[业务装饰器](<../../../../../src/jx3_bot/modules/weather/handlers.py>)；[入口](<../../../../../src/jx3_bot/__main__.py>)以 `discover_routes` 加载，[应用](<../../../../../src/jx3_bot/application.py>)传入同次入站的已清理 `ReceivedMessage`，旧兼容注册表仍可调用。
- [存储恢复](<../../../../../src/jx3_bot/store.py>)在单个事务中将旧 `pending`／`working`／`unmatched` inbox 置 `expired` 并清空任务 payload；不重新执行启动前未触发或已开始未完成的业务。已写入的聊天记录、网关会话 ID／接收序号及 outbox 回复记录不因此删除或重置；是否发送仍受原状态、有效期限及重试限制约束，`sending→uncertain`，不自动重发。既有运行中 FIFO／去重与5分钟回复规则仍保留，已退役的同 ID 普通群事件不因后续 AT 事件复活。
- 增加 `BOT_COMMAND_PREFIX` 配置（默认空，最长16非空白可打印字符）；更新[用户说明](<../../../../../README.md>)、[部署说明](<../../../../../docs/qq-integration/2026-10-10-deployment.md>)、两份无凭据环境模板与[开发指导](<../../../../../AGENTS.md>)。

## 验证

| 方法／实际执行命令 | 实际结果 | 未覆盖范围 |
| --- | --- | --- |
| 阅读现有代码、测试、现行文档与工作区状态；核对本地 Markdown 链接 | 修订评审稿并确认链接存在，未读取凭据、私有数据库或启动服务 | 未执行真实 QQ 联调或天气请求 |
| 历史：`UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m unittest discover -s tests -p 'test_observability.py' -v`、`... -p 'test_gateway.py' -v` | 临时诊断代码曾通过日志 38 项、网关 12 项；之后按维护者要求撤回，不将该测试数量冒称撤回后的结果 | 合成 WS 帧，不代表真实 QQ 推送行为 |
| 历史：`UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m unittest discover -s tests -q`；`UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m compileall -q src/jx3_bot tests` | 撤回前 237 项通过、编译检查通过；随后移除三项专用日志测试 | 未连接 QQ 或天气供应商 |
| 撤回后：`UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m unittest discover -s tests -q`；`UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m compileall -q src/jx3_bot tests` | 234 项通过；编译检查通过。只撤回本轮新加的日志与三项对应测试，现有收帧日志仍在 | 离线回归，不验证真实 QQ 行为或未来的装饰器设计 |
| 本轮实施后：`UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m unittest discover -s tests -q`；`UV_CACHE_DIR="$PWD/.cache/uv" uv run --locked python -m compileall -q src/jx3_bot tests` | 全量 **245 项通过**，编译检查通过；另有天气路由自动发现及分发、前缀/无参/事件类型、旧 inbox 退役及 outbox 状态的合成场景回归。`git diff --check` 无错误 | 仅离线合成测试；没有启动真实 QQ、调用天气供应商或验证平台投递规律 |

## 关联与交接

- `follows-up` → [已结束的通用匹配需求](<../REQ-20261011-matcher-templates/index.md>)（只表示新增独立需求的来源，不继承历史正文的约束）。
- `related-to` → [聊天数据库](<../REQ-20261013-chat-history/index.md>)、[控制台诊断](<../REQ-20261011-console-diagnostics/index.md>)（仍以现行代码/规范为事实依据）。
- 现有边界：天气参数为安全起见仍按业务规则限制 80 字及禁用字符；新通用路由对超过 1024 字或包含 NUL 的参数直接不匹配，而旧匹配器会产生 invalid 回复，属于有意保守收紧，不扩大业务接收面；可在后续需求中根据真实需要讨论错误提示。非消息事件的安全 DTO、业务侧通用 validator、默认参数值仍未实现，也未纳入本次要求。
- 当前结论：装饰器、自动发现/限定注入、天气迁移及重启任务处理均已离线实现并通过全量测试；群消息专用诊断日志／测试保持撤回。真实 QQ 投递规律仍未知，正式联调／上线另需授权。提交状态：已获授权并纳入 0.1.0 收尾提交；需求包标记 ready-to-archive，**未提交、未归档**。

## 关键更新

| 日期 | 实质变化及依据 |
| --- | --- |
| 2026-10-20 | 根据维护者明确的业务装饰器目标与群 @/单聊语义，建立单一需求包和详细评审稿；标记设计建议与平台待验证事项。 |
| 2026-10-20 | 维护者纠正：不得把未经验证的两类事件推送关系命名为项目语义；`on_group.at_me` 用布尔值严格筛选相应群事件。曾授权临时收帧日志以提供实测，不改去重或匹配行为。 |
| 2026-10-20 | 维护者提供单个真实样本：仅一次普通群事件、一次 accepted，未见重复；随后明确群 `at_me=False` 为默认、`match` 与配置前缀、叠加装饰器、无需 `name`、启动自动发现／注册，并要求实施后固化开发指导；专用诊断日志、测试与方案章节全部撤回。 |
| 2026-10-20 | 维护者明确重启后不补执行先前未触发的业务；同时 `match` 须接受无参数完整指令，传入空 `args` 以便未来业务使用默认参数。提案删除跨重启路由兼容要求，明确不因清理旧 inbox 任务而删除已写入 outbox 的回复记录或聊天记录，也不重置网关会话 ID／接收序号；当时仅更新待评审设计，未实施运行改动。 |
| 2026-10-20 | 维护者批准“开始实施方案”。实施 QQ 事件投影、装饰器/发现/注入、天气迁移和启动 inbox 退役，保留 outbox 安全语义；全量离线 245 项通过并完成语法编译、现行文档同步。未连接 QQ、未提交或归档。 |
| 2026-10-20 | 维护者指出先前总结中的合并称呼不清楚；文档改用数据库实际保存的网关会话 ID／接收序号及 outbox 回复记录描述，并明确记录未删除不等于自动重新发送。仅调整措辞，未修改运行代码。 |
