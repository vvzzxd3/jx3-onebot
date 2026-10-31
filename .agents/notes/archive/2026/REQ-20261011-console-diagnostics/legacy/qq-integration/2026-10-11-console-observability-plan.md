> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/qq-integration/2026-10-11-console-observability-plan.md`。

# QQ 官方回包与无响应问题：控制台日志方案

> 后续更新：维护者已授权群／单聊通用匹配；单聊从“仅观察”扩展为天气业务，见[模板与匹配器实现](<../../../REQ-20261011-matcher-templates/legacy/qq-integration/2026-10-11-matcher-templates.md>)。本记录的96项及单聊非目标保留为日志任务历史，后续模板阶段完整124项通过；2026-10-12天气模块聚合后的最新结构与134项完整回归见[模块聚合记录](<../../../REQ-20261012-weather-modules/legacy/weather/2026-10-12-module-aggregation.md>)。

- 所属模块：QQ 官方接入
- 建档日期：2026-10-11；最近更新：2026-10-13（Asia/Shanghai）
- 状态：维护者已回复“按照方案执行”；单行聊天与QQ回包控制台日志已实现，96项离线测试通过（新增35项日志测试）。本轮未接入真实运行进程，未复现或宣称修复无响应根因。
- 来源：维护者报告“启动成功，但是和机器人对话没有反应”，要求思考如何在控制台打印官方返回的所有消息；随后明确要求展示聊天类型（群聊／单聊）、会话／组群ID与昵称、发送人ID与昵称、会话内容。本次明确要求替代初版的正文隐藏和聊天身份去标识默认建议，仅限指定聊天展示字段，不解除凭证保护。
- 关联：[当前接入实现](<../../../REQ-20261010-official-weather/legacy/qq-integration/2026-10-10-implementation.md>)、[部署指南](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>)。

## 1. 当前事实与尚未验证的信息

维护者已报告启动成功；本轮没有接入其运行进程、查看真实事件或复现无响应，不能由启动成功推断 READY、群事件权限或回复接口成功。

以下为**实施前**静态检查发现，保留用于说明变更依据；所列日志缺口已在第8节记录修复，链接指向当前源码：

- [入口](<../../../../../../../src/jx3_bot/__main__.py#L97-L104>) 配置 INFO 日志，但只有级别和文本，没有时间、模块、关联标记；httpx/httpcore 已抑制普通请求日志。
- [网关](<../../../../../../../src/jx3_bot/gateway.py#L45-L141>) 的首个 Hello 和后续事件在接收、过滤前均未记录；断连只有关闭码或通用提示。
- [事件解析](<../../../../../../../src/jx3_bot/integrations/qq/events.py>) 仅接收 GROUP_MESSAGE_CREATE／GROUP_AT_MESSAGE_CREATE。私聊事件、普通聊天、机器人事件、缺身份和无效时间统一返回 None，没有解释原因。
- [命令解析](<../../../../../../../src/jx3_bot/modules/weather/handlers.py>) 精确匹配天气关键字和空格参数，不处理提及前缀。初版提出“@正文可能含机器人提及前缀”的假设；2026-10-11补查[官方群@事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html)明确说明content已去除@机器人前缀，因此不能把常规@事件必然有前缀作为故障解释。其他用户提及、特殊消息形态和实际回包仍待现场验证。
- [QQ HTTP](<../../../../../../../src/jx3_bot/qq_http.py>) 没有统一响应日志，平台业务码被转换成异常；[应用](<../../../../../../../src/jx3_bot/application.py>) 回复告警只显示状态，不记录业务码或请求关联。
- [Store.accept](<../../../../../../../src/jx3_bot/store.py#L176-L196>) 的 False 既可能是重复水位，也可能是已存在命令或无命令，不足以直接把 False 标为 duplicate。

## 2. 目标、非目标与语义

目标：一次授权群测试后能判断事件是否到达、为什么忽略、是否入队、是否完成业务、是否提交回复、官方是否确认接收。

“所有消息”定义为**本进程实际收到的每个 QQ WebSocket 应用帧及每个 QQ HTTP 响应都有日志入口**，包括普通聊天、未知事件、Hello、ACK、错误回包和非 JSON 回包；不是只记录匹配命令，也不是所有字段无差别原样输出。平台没有推送的消息不可观测，不可把没有日志当作群内没有消息。

非目标：本轮不自动启动服务或调用真实 API、不新增私聊支持、不放宽命令、不修改权限／intent、不改变可靠水位、去重、月计数及回复重试策略；不建立原始群聊归档。

## 3. 三层日志设计

### 3.1 接收层：先记收到，再检查／过滤

1. **WebSocket**：首个 `ws.recv()` 与后续迭代统一经过观察函数；紧接收到后解析并记录，然后才做协议判断、命令过滤与队列提交。不要把日志放到只包含规范化命令的 dispatch 队列之后。
2. 正常 JSON 记录 `event=qq.ws.receive`、连接号、接收序号、`op`、`t`、`s`、字节数、正文存在／长度及数据结构。Hello 记录心跳间隔，READY／RESUMED明确记录成功状态；session_id 不明文输出。
3. 畸形 JSON、非对象、超限／非法字段也记录安全的解析结果、类型、字节数，不输出未经检查的原始字符串。关闭帧由异常／连接终止路径记录关闭码；未知关闭原因不直接打印，避免反射敏感内容。
4. **QQ HTTP**：Token、`/gateway/bot`、群回复三处覆盖每次响应，包括401后的下一次 GET响应。在业务码判断或抛异常之前记录 HTTP状态、业务码、耗时、响应长度和可用的安全请求关联号。使用逻辑路由名 `token`／`gateway`／`group.reply`，不输出含OpenID的实际路径。
5. 非 JSON／HTML错误页只输出类型、长度和解析失败。网络异常另记 `qq.http.error`、阶段和异常类：它不是收到HTTP回包，不能虚构status。Token回包只保留有效期／成功与否等白名单，access_token始终删除。
6. 初期范围是 QQ；天气侧补充必要业务状态即可。若后续增加天气HTTP观察，必须单独设计，不把Geo返回名称、ID、坐标、命名时区间接写入日志。

### 3.2 决策层：每个事件有可解释的去向

建议将过滤结果表达为结构化结果，保留原命令匹配语义，避免为了日志再次解析形成两个规则版本。只用固定枚举，不拼接原始正文：

- `ignored / unsupported_event`：私聊或首版未处理的其他事件。
- `ignored / bot_author`、`malformed_payload`、`missing_identity`。
- `ignored / command_not_matched`：普通聊天或提及正文未符合精确规则。
- `ignored / invalid_timestamp`、`expired`、`future_timestamp`。
- 参数非法但已经匹配的命令保持既有 invalid 命令处理，不能误记为完全忽略。
- `accepted`：命令与水位事务已经完成，才记录可靠接受。
- `duplicate_message`／`stale_sequence`：由同一次存储事务返回明确结果；现有bool不能准确区分，需要最小结果类型扩展，而不是先查再写。
- `accept_failed`／`queue_full`：不冒称可靠接受，不跳过原有水位故障策略。

生命周期事件READY／RESUMED不假装命令；输出网关会话状态与订阅intents数值，便于辨认“进程运行”与“连接可处理事件”。

### 3.3 处理／回复层：闭合整条链路

- `command.start`、`command.done`／`command.error`，记录命令类型、耗时和安全结果分类，不默认输出参数或天气／群聊全文。
- `reply.planned` 只在outbox提交成功后记录。
- `reply.submit`／`reply.result` 记录固定msg_seq、attempt、HTTP状态、平台业务码及outbox最终状态。
- `sent`含义为HTTP成功、返回有效消息ID并已更新outbox，不表示用户确实阅读或客户端展示。`failed`、`uncertain`、`expired`、`preflight_retry`必须区分；不因日志新增POST自动重试。
- 恢复、清理使pending过期／sending变uncertain时也给出安全计数摘要，避免只有日志开头没有结尾。

### 3.4 聊天展示层：固定中文字段，不用原始JSON代替

按维护者新增要求，所有实际收到的群聊／单聊消息在业务过滤前生成独立聊天记录。缺字段也保留字段位置和明确状态，不因业务不支持、普通聊天或重复推送而不展示。心跳／Token等非聊天回包仍用技术摘要，不伪装成聊天。

| 展示字段 | 群聊来源 | 单聊来源／语义 |
| --- | --- | --- |
| 聊天类型 | GROUP_MESSAGE_CREATE／GROUP_AT_MESSAGE_CREATE → 群聊 | C2C_MESSAGE_CREATE → 单聊；未知事件不强行归类 |
| 会话ID | d.group_openid，标注“群OpenID” | d.author.user_openid作为当前应用下单聊对端标识，标注“对端OpenID”；不是平台另给的独立会话ID |
| 会话名称／昵称 | 已核实消息体不包含群名称字段，单行显示“未知群”；不从发送人昵称或卡片标题推断群名 | 对端author.username；缺失或空串时“未知用户”，单行与发送人字段合并，说明这是对端昵称不是独立会话名称 |
| 发送人ID | d.author.member_openid | d.author.user_openid |
| 发送人昵称 | d.author.username，有效非空字符串才展示 | 同左，允许为空，不能承诺每条都有昵称 |
| 会话内容 | d.content，保留前后空格和提及结构用于排障，安全处理后展示 | 同左 |

场景身份缺失时可展示有效author.id作为“发送人ID（来源：author.id）”，但不能假装它就是member_openid／user_openid，也不能由日志兜底改变业务所需身份字段。会话ID缺失显示“缺失”，不会编造群／单聊对端。

只有附件时显示“[无文本] [图片×1]／[语音×1]／[文件×1]”等结构摘要，不自动下载附件、识别语音或打印鉴权链接。引用／转发消息若仅有msg_elements，按有界深度展示其中content并明确“引用／转发内容”，避免误归为当前发送人的原创正文；卡片仅有界摘要，不展开未知fields、地址或联系人信息。超长／超深内容明确标注截断以及原始长度，不静默省略。

字段依据已于2026-10-11直接核实官方公开文档：[群全量事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)、[群@事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html)、[单聊事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/c2c_message_create.html)。User包含username；单聊官方示例username为空，群消息体只有group_openid而无群名称。获取群名／补齐昵称不增加资料查询API，不擅自增加权限或长期身份缓存；若维护者后续要求始终可读的群名称，可另行设计显式群OpenID→显示名称配置并标注“本地备注”。本轮只查公开文档，未调用真实API。web_search因工具缺少搜索凭证未可用，随后直接读取官方页面完成核实。

## 4. 关联、格式与安全

建议先用标准库logging，不引入NoneBot、loguru或第三方SDK日志。仅 `run` 配置控制台处理器，命令 `usage`／`import-cities` 的JSON输出保持不被新日志污染。

技术日志采用单行 `时间 级别 模块 event=... key=value`。维护者认为多行聊天记录过于复杂，现改为一条消息一行：群聊采用 `时间 INFO [receive] [群聊] 群名称（群OpenID） - 用户昵称（成员OpenID）：内容`；单聊采用 `时间 INFO [receive] [单聊] - 用户昵称（用户OpenID）：内容`，单聊发送人就是对端，不重复打印相同昵称／ID。保留聊天类型标记以满足前述群聊／单聊区分要求，正文前加冒号防止与ID粘连。群名为空显示“未知群”，用户昵称为空显示“未知用户”，ID缺失显示“缺失”；不把本地占位当官方名称。正文不strip，真实换行转为可见`\n`，转义ESC、CR、退格和其他控制字符，名称中的换行也转义；独立消费线程一次输出完整记录，禁止用户文本伪造新日志行。技术日志保留connection／trace等关联信息，默认聊天展示行不堆叠这些排障字段；必要时DEBUG另附关联信息。统一技术字段：`connection`、`trace`、`op`、`event_type`、`seq`、`route`、`status`、`code`、`duration_ms`、`reason`、`state`。

同一消息跨群全量／@事件、inbox和outbox使用一致trace：由进程随机密钥对App／群／消息ID进行HMAC生成短标签。技术日志继续使用HMAC标签；**聊天展示区按本次明确要求显示官方会话／发送人ID、已有昵称和正文，不以HMAC代替指定字段**。密钥不输出不落盘，默认只保证同一进程内关联；重启使用新运行号，不能承诺跨重启标签相同。无有效消息身份的事件用连接号与接收序号关联。

采用事件类型白名单投影与递归安全处理，而非只按几个敏感键替换后打印整个字典：

- 永不输出QQ secret、access_token、Authorization、JWT、私钥、Cookie和配置全量，正文里包含可识别鉴权数据时同样隐藏。
- 仅聊天展示区的会话／发送人ID及昵称允许按需求直出；其他用户资料、跨应用账号、消息／会话恢复ID仍隐藏或去标识。官方OpenID不等于QQ号／群号，不宣称可反查实际QQ号码。
- 签名链接、附件URL、网关查询参数隐藏。特别是message_scene.ext可包含`auth_token=...`，不能原样展开该数组；控制台聊天记录也不输出它。
- 平台错误message、未知字段值、非JSON正文默认隐藏，业务码仅接受有界的安全码值；供应商可反射敏感文本，不能认为“官方字段”天然安全。
- 本任务聊天展示按新增要求开启ID／昵称／正文，其他日志不默认复制聊天正文。文本保留可读性但转义控制字符、屏蔽可识别鉴权信息并设置明确截断标记；自由文本无法保证完全匿名。业务inbox仍只保存必要命令，不为日志保存普通聊天或昵称。
- “只在控制台”不等于不落盘：systemd、Docker、终端或重定向可保留输出。按要求打印的ID／昵称／正文可能含个人信息，运行环境应限制日志访问与保留；应用不新增文件handler／SQLite聊天归档，不把真实回包复制到文档／仓库。

## 5. 已实现配置与日志负载

以下配置已经生效，解析见[日志模块](<../../../../../../../src/jx3_bot/observability.py>)，默认值同步到[配置模板](<../../../../../../../.env.production.example>)。只在`run`安装stdout日志，时间固定Asia/Shanghai；已有配置缺少新增字段时使用默认值，无需覆盖实际专用配置。`check-config`也校验日志选项，但不输出聊天日志或联网：

| 配置 | 建议值 | 含义 |
| --- | --- | --- |
| `BOT_LOG_LEVEL` | `INFO` | 应用日志级别；排障应保留INFO，否则接收摘要不可见 |
| `BOT_QQ_RECEIVE_LOG` | `summary` | 技术接收摘要模式；`payload`附安全结构；`off`关闭逐条技术接收但保留生命周期和失败 |
| `BOT_QQ_CHAT_LOG` | `true` | 按维护者要求启用独立聊天记录，显示官方会话／发送人ID及已有昵称，不受技术摘要／payload模式控制 |
| `BOT_QQ_LOG_MESSAGE_TEXT` | `true` | 本任务按明确要求开启有界聊天正文；关闭时明确显示“正文隐藏”，不声称满足完整内容展示 |
| `BOT_LOG_MAX_PAYLOAD_CHARS` | `4096` | 单条安全结构及聊天正文各自展示上限，明确标注truncated与原始长度，不静默丢失 |

排障建议 `INFO + 聊天记录`，所有收到的聊天先展示指定字段，再给出过滤原因；按需开启payload检查其余安全结构。开启全部技术接收时Hello、ACK和未知op也覆盖，不采样；以后若增加心跳聚合模式必须明确不再是逐帧日志。

不启用websockets/httpx/httpcore全局DEBUG：它们可能输出Identify／Resume里的token、请求Header及身份路径，不能用它们代替安全的应用观察点。

慢控制台不能阻塞接收／心跳：建议有界QueueHandler＋独立消费线程，脱敏后入队，不把原始帧长期放进日志队列。日志输出错误不改变业务事务结果，不把logger异常当接收成功／失败。队列满或输出设备不可用需维护丢失计数、恢复后告警／停机汇总；禁止静默丢失并宣称日志完整。无限慢输出下“逐条必达”和“不阻塞业务”无法同时保证，首版选择业务可靠性优先，明确披露日志缺口；该日志不是可靠消息存储的替代。正常关闭有界刷新，格式异常／故障不得打印未脱敏备用原文。

## 6. 示意输出与排障方法

下面是已实现格式的合成示例，不是真实群聊。此格式替代前序多行字段区块；当前群名固定为未知群，未实现本地群备注或自动资料补查。

```text
2026-10-11 13:00:00 INFO [receive] [群聊] 未知群（group_openid_demo） - 小明（member_openid_demo）：天气 北京
2026-10-11 13:00:01 INFO [receive] [单聊] - 小红（user_openid_demo）：你好
2026-10-11 13:00:02 INFO [receive] [群聊] 未知群（group_openid_demo） - 未知用户（member_openid_demo）：第一行\n第二行
```

单聊的用户昵称／ID同时代表会话对端，因此不重复。聊天内容展示不等于业务支持单聊；若不处理仍由技术日志给出unsupported_event原因。

技术摘要格式示意（标签和时间仍为合成数据，省略部分字段）：

```text
2026-10-11 12:50:00 INFO event=qq.ws.receive connection="c1" received=1 op="10" bytes=46
2026-10-11 12:50:00 INFO event=qq.gateway.hello connection="c1" heartbeat_ms=30000
2026-10-11 12:50:01 INFO event=qq.gateway.ready connection="c1" seq=1 intents=33554432
2026-10-11 12:50:05 INFO event=qq.ws.receive trace="m_demo" event_type="GROUP_AT_MESSAGE_CREATE" seq="18" content_chars=16
2026-10-11 12:50:05 INFO event=qq.dispatch.result trace="m_demo" state="ignored" reason="command_not_matched"
2026-10-11 12:50:10 INFO event=qq.dispatch.result trace="m_other" state="accepted" command="weather"
2026-10-11 12:50:10 INFO event=command.done trace="m_other" duration_ms=260
2026-10-11 12:50:10 INFO event=qq.http.response trace="m_other" route="group.reply" status=200 code="0" duration_ms=180
2026-10-11 12:50:10 INFO event=reply.result trace="m_other" state="sent" msg_seq=1
```

未知事件类型输出`unknown`，不回显任意未经白名单允许的t值。payload是白名单结构投影（字段存在性、类型、Hello数值等），不是脱敏整个原始字典；不回显未知字段名／值、HTTP错误message或真实WSS URL。

- 没有READY：先查Token／网关／Identify／Resume／关闭日志，不认为启动命令返回或进程存活代表连接完成。
- READY后只有心跳，没有目标消息：查平台权限、订阅、测试群范围和全量开关；日志只能证明客户端未观察到，不确定平台侧原因。
- 有私聊事件但unsupported_event：首版未实现私聊，不是天气处理失败。
- 有群事件但command_not_matched：依据展示正文检查命令空格／前后缀／其他用户提及及特殊结构；官方文档说明已去除机器人@前缀，不能先认定该前缀就是根因，不在没有样本时改解析。
- accepted后无command.done／reply.planned：查worker、业务／存储故障和期限。
- 有reply.submit但失败或uncertain：查HTTP业务码、权限、被动回复窗口及网络；不能为测试可见性绕过保守重试。

## 7. 实施计划与验收

1. 新增独立观测模块，实现安全投影、关联标记、技术单行与聊天记录双格式、有界输出与配置验证；聊天记录固定展示群聊／单聊类型、会话ID／名称、发送人ID／昵称和内容。
2. 覆盖Hello、每个后续WS帧、关闭事件及三个QQ HTTP响应入口；处理异常／非JSON时不泄露回包。
3. 增加解析／存储结果类型和worker阶段日志，保持命令语义、水位和重试不变。
4. 同步配置模板与部署指南；本机真实配置不由本轮读取或重写。
5. 使用FakeSocket、MockTransport、合成秘密与临时SQLite做离线回归：过滤前日志、私聊／普通消息／未知事件、全部ACK、双事件同trace、接受／重复／旧seq、畸形JSON／非对象／非JSON响应、HTTP200业务错、401刷新、Token失败、uncertain、输出队列满／故障与停机刷新。
6. 验证嵌套未知字段、混合大小写Header、token、JWT、URL、message_scene.ext中的auth_token、控制字符与超长文本的防护；聊天记录只允许指定会话／发送人OpenID原值，不在其他技术日志泄漏身份。覆盖username缺失／空串、无群名称、单聊对端映射、author.id来源标记、附件／引用摘要、并发输出行不交错及正文开关；原有离线套件全部回归。
7. 由维护者重启测试进程并执行授权群测试；真实@内容、权限和平台业务码仍需现场验证。

## 8. 本轮实际改动与验证

### 实际改动

| 位置 | 改动 |
| --- | --- |
| [日志模块](<../../../../../../../src/jx3_bot/observability.py>) | 新增LogSettings、Observer、安全投影／单行展示、HMAC关联、控制字符／已知凭据／JWT／Header／URL保护，标准库Handler＋线程有界输出 |
| [配置](<../../../../../../../src/jx3_bot/config.py>)、[入口](<../../../../../../../src/jx3_bot/__main__.py>) | 验证日志选项；仅run安装stdout日志，模块共享Observer；第三方普通请求／协议DEBUG关闭，异常不打印外部正文或traceback，退出有界刷新 |
| [网关](<../../../../../../../src/jx3_bot/gateway.py>)、[QQ HTTP](<../../../../../../../src/jx3_bot/qq_http.py>) | Hello与后续每帧在过滤前观察，Token／网关／回复每次HTTP响应在业务检查和401重试前记录；生命周期、关闭码／退避／网络阶段可见 |
| [消息解析](<../../../../../../../src/jx3_bot/models.py>)、[存储](<../../../../../../../src/jx3_bot/store.py>) | 单一解析返回MessageDecision；accept可选detailed结果在同一事务区分accepted／duplicate_message／stale_sequence／ignored，保留默认bool接口；恢复、过期和清理给提交后计数 |
| [应用](<../../../../../../../src/jx3_bot/application.py>)、[天气](<../../../../../../../src/jx3_bot/weather.py>) | command／reply阶段同trace，明确sent／failed／uncertain／过期／预检；天气已捕获错误补安全分类，不打印Geo字段／请求路径 |
| [模板](<../../../../../../../.env.production.example>)、[部署指南](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>)、[README](<../../../../../../../README.md>)、[开发约定](<../../../../../../../AGENTS.md>)、[文档索引](<../../../../../../../docs/README.md>) | 同步默认选项、日志隐私边界及使用说明；没有读取或重写实际专用配置 |
| [日志测试](<../../../../../../../tests/test_observability.py>) | 新增35项合成输入测试，覆盖固定格式、缺昵称、全部回包、忽略原因、同trace、故障／过滤前观察、凭证保护及输出队列 |

没有新增依赖、数据库表或聊天归档；单聊只展示已收到事件，未新增单聊业务回复。命令／intents／可靠水位／去重键／FIFO／被动窗口／POST重试不变。没有启动真实服务、读取实际配置或私钥、请求QQ／天气受保护API，未动旧插件或维护者已有改动，不自动提交。

### 输出边界与审查修复

- 队列固定最多1024条，入队前完成安全投影／格式化，不缓存原始帧；独立线程输出stdout并flush。队列满或设备错误累计`log.loss`，可写时及停机汇总；输出永久不可用时不能保证该通道上可见告警。
- 正常退出最多等待2秒刷新，未完成输出计入unflushed，另用独立stderr线程尝试`log.flush_timeout`告警（等待最多0.1秒）；stdout与stderr都不可用或强制终止时无法保证完整日志，不把诊断队列视为可靠inbox。
- 已注册凭据保护在进程寿命内保留，轮换后旧QQ Token不因有界历史淘汰而重新明文出现；注册表仅内存、不持久化，内存成本随进程期间凭据轮换数量增长。自由文本保护是识别已知值／常见鉴权结构，不声称可自动匿名化任意个人信息或识别所有编码／混淆内容。
- 只读审查早期回传的边界已由主代理独立补回归并修复：非eyJ合法JWT与点号前缀的重叠候选、技术event名称不能被JWT误伤、完整Basic／自定义Authorization／Cookie、旧opaque Token、引用数量／深度截断标记及已有正文与引用共存、停机刷新超时的独立告警。trace也使用ASCII JSON编码，异常Unicode不使观察阶段编码失败。
- 审查子任务在完成前失败，没有完整最终审查结论；不将它写成完整安全审计通过。已回传发现均由主代理独立复验，不将未确认猜测写成业务故障。

### 已执行验证

此前方案阶段2份文档／29处本地链接检查通过。实施后在现有独立uv环境执行：

```bash
PYTHONPATH=src .venv-official-uv/bin/python -m unittest discover -s tests -p test_observability.py -v
PYTHONPATH=src .venv-official-uv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src .venv-official-uv/bin/python -m compileall -q src/jx3_bot tests
PYTHONPATH=src .venv-official-uv/bin/python -m jx3_bot --help
git diff --check -- .env.official.example src/jx3_bot tests docs README.md AGENTS.md
```

结果：新增35项日志专项通过；完整**96项离线通过**（最终复验0.755秒，Python3.12.11）；语法、CLI帮助及已有Git差异空白检查通过。最终套件使用`unittest.defaultTestLoader.discover('tests')`＋`TextTestRunner`复验，将根日志级别设为WARNING降低合成测试输出噪声；新增用真实阻塞的合成控制台验证网关心跳／ACK继续运行。FakeSocket／MockTransport／临时SQLite与合成凭据，不创建真实服务。usage仍单条JSON，回包观察覆盖GET401后两次响应，POST401／不确定发送不新增重试；普通聊天与昵称不落库。未跟踪新增文件的格式与最终文档链接另用离线脚本检查，不只依赖git diff。

最终限定范围检查通过：6份本轮文档、171处本地链接、11份源码／测试／模板文件的末尾换行与行尾空白；Python AST无NoneBot／OneBot新增导入，Markdown围栏匹配，`git diff --check`通过。Git状态中的原有暂存／未暂存／未跟踪工作保留，没有提交或清理；本轮无后台任务残留。

### 未完成／下一步

维护者需停止旧的本次官方测试进程，再用相同数据库与配置重新启动，避免并发实例；默认日志即可生效，不要复制模板覆盖实际凭据。授权群发`天气 北京`，依据receive／dispatch／command／reply各层定位根因；若在单聊测试，unsupported_event符合当前业务边界。群名补查／本地备注、单聊业务、真实权限／网关／响应联调均未在本轮实现或验证。本方案不要求维护者发送原始真实群聊／凭证样本。

## 9. 变更历史

| 日期 | 变化 | 依据 |
| --- | --- | --- |
| 2026-10-11 | 新建控制台日志方案，明确接收前观察、过滤原因、HTTP回包、安全边界和验收 | 维护者报告启动成功但对话无反应，要求先思考日志构建 |
| 2026-10-11 | 补固定中文字段的多行聊天记录，按明确要求展示会话／发送人ID、昵称与正文；核实username、缺失群名称及单聊映射，修正机器人@前缀假设 | 维护者新增展示要求，直接核实官方三种事件文档；实现仍未开始 |
| 2026-10-11 | 将多行展示替代为`[receive]`紧凑单行格式，保留群聊／单聊区分；单聊省去重复对端信息，换行可见转义 | 维护者提出多行格式过于复杂并提供单行示例；当时只改方案 |
| 2026-10-11 | 实施单行聊天、QQ回包、过滤及处理／回复日志、安全投影与有界输出；新增35项日志测试，完整96项通过，6份文档／171处链接检查通过 | 维护者回复“按照方案执行”；只做离线验证，真实无响应根因仍待重启后按日志定位 |
