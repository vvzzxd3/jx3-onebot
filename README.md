# jx3-onebot

当前版本：**0.1.0**。版本变化见 [CHANGELOG](<CHANGELOG.md>)。

面向 QQ 的 Python IM Bot 服务端，业务方向为剑网 3 查询，仅服务 QQ。[官方服务](<src/jx3_bot/__main__.py>)直接接入 QQ 官方机器人 API v2，是仓库唯一受支持的运行入口；旧 NoneBot2／OneBot V11 入口与插件已清理。

> **实现与部署边界**：官方 WebSocket＋HTTP 回复＋天气首功能与四表聊天记录链路已完成离线开发；**尚未进行真实 QQ／天气联调，也未完成生产部署切换**。仓库代码已统一为官方服务，旧个人号进程不会自动停止。仓库名称暂沿用 `jx3-onebot`。

## 当前实现与验证边界

尚无经过验证的完整剑网3查询命令清单，不能把天气首版或接入代码等同于游戏功能／萌萌替代已完成。

| 模块 | 当前仓库状态 |
| --- | --- |
| 新 QQ 官方接入 | [独立包](<src/jx3_bot/>) 实现动态WSS、Token、心跳／Resume、可靠inbox／outbox与HTTP被动回复；离线模拟通过，真实平台未联调 |
| 新天气首功能 | [天气业务模块](<src/jx3_bot/modules/weather/>) 实现城市本地检索／Geo兜底、每日v1预报、缓存、默认输入与自身自然月限额 |
| 旧QQ接入 | NoneBot／OneBot 入口、插件、依赖与旧环境配置已从工作树清理；不再支持个人号客户端接入；现有部署需人工停旧进程并切换 |
| 群复读／AI 对话 | 旧插件已移除，**尚未迁移**到官方服务；当前不提供这些功能 |
| 旧天气 | 旧插件已移除，官方天气模块独立实现；历史接口资料移入[天气参考目录](<docs/weather/reference/>) |
| 剑网3开服查询 | [API业务模块](<src/jx3_bot/modules/api/>) 实现 `开服 服务器名称`，查询 JX3BOX 开服状态；离线实现，真实接口与QQ尚未联调 |
| 旧爬虫 | 旧探索插件已移除，未迁移 |
| 聊天数据库 | 已按批准方案实现用户、群、单聊消息、群聊消息4张业务表；入站提交后日志／匹配、出站QQ成功确认后原子归档与发送日志；默认保存支持的消息且不自动到期，尚未做真实QQ联调 |

## 代码组织：按业务模块聚合

官方服务采用 **业务模块纵向聚合＋独立 QQ 接入＋精简公共核心**，不按全局 service／models／client 目录分散各业务代码。

- **业务模块**：在 `src/jx3_bot/modules/<业务>/` 保存自己的业务处理、数据模型、外部客户端、存储访问、命令注册和模板。仅本业务使用的代码不放公共目录；文件按实际复杂度拆分，不创建空模块。
- **接入层**：QQ 事件、消息规范化、Token、WebSocket 与 HTTP 回复独立于业务；业务返回通用结果，不自行管理 QQ 连接或投递状态。
- **公共核心**：只保存跨业务共享的命令／回复结构、匹配分发、数据库事务、日志与基础异常；禁止将业务专属模型堆入全局模型文件或 common。
- **模型归属**：天气 City／预报／请求与响应模型属于天气；QQ 消息模型属于 QQ 接入；真正通用的 Command／Reply 才属于核心。小模块使用自己的 `models.py`，规模扩大后可拆 `models/` 子包，不建立全项目统一业务模型仓库。
- **存储与装配**：模块拥有自己的持久化访问代码，但共享受控数据库连接与事务；不因拆目录另建数据库、重置月计数或改变 inbox/outbox 原子性。应用入口负责依赖装配。

天气已作为首个模块聚合到 [天气模块目录](<src/jx3_bot/modules/weather/>)，其中 [模型](<src/jx3_bot/modules/weather/models.py>)、[客户端](<src/jx3_bot/modules/weather/client.py>)、[服务](<src/jx3_bot/modules/weather/service.py>)、[仓储](<src/jx3_bot/modules/weather/repository.py>)、[城市导入](<src/jx3_bot/modules/weather/cities.py>)、[命令注册](<src/jx3_bot/modules/weather/handlers.py>)、[格式化](<src/jx3_bot/modules/weather/rendering.py>)与模板共同维护。[共享模型](<src/jx3_bot/core/models.py>)与[QQ消息／事件目录](<src/jx3_bot/integrations/qq/>)分别落地；QQ 传输、完整匹配器、日志及公共存储仍保留原位置，后续按职责逐步归入 `integrations/qq/` 与 `core/`。兼容入口只做转发，不继续新增业务实现。先完成组织调整，再单独完善天气请求／响应 DTO，不把目录迁移冒称完整模型体系已实现。

目标业务模块示例（文件按需创建，不要求所有模块拥有相同文件）：

```text
modules/<业务>/
├── models.py       # 该业务的数据结构与校验
├── service.py      # 业务编排
├── client.py       # 外部服务调用
├── repository.py   # 持久化访问
├── handlers.py     # 命令注册与处理入口
├── rendering.py    # 业务结果到通用回复的转换
└── templates/      # 该业务的本地输出模板
```

## 新官方服务快速开始

详细配置、权限、存储与切换检查见 [部署指南](<docs/qq-integration/2026-10-10-deployment.md>)。

使用 uv 完整管理官方服务；代码层支持 Linux、macOS 与 Windows x64，Windows 单实例锁使用标准库 `msvcrt`，并通过 `tzdata` 提供 IANA 时区数据。[Python版本文件](<.python-version>) 固定3.12.11，[项目配置](<pyproject.toml>) 声明依赖，[锁文件](<uv.lock>) 固定解析结果。当前仅完成非 Windows 主机上的兼容回归与 Windows x64 依赖交叉解析，尚未在 Windows 实机或真实平台联调；Windows ARM64 因锁定的 `cryptography` 无对应 wheel，不属于开箱支持范围。离线验证不能替代部署机和真实平台验收。

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，在仓库根目录运行；uv 自动创建默认 `.venv`，无需激活或配置 `PYTHONPATH`。生产配置使用 `.env.production`，不得覆盖已有私有配置；新环境可按无密钥模板手动创建。测试环境使用独立的 `.env.test` 和 `data/test/bot.sqlite3`，不可复制生产凭据或数据库。迁移或恢复数据库前须停服并做一致性备份，保留月计数和未完成收发状态。

```bash
uv sync --locked
# 只有没有实际配置时才创建；勿覆盖现有私有文件
# cp .env.production.example .env.production && chmod 600 .env.production

# 在本机填写专用配置，不复用或传播旧密钥；以下两条不请求天气数据API
uv run jx3_bot check-config
uv run jx3_bot import-cities --download  # 仅下载公开城市CSV

# 唯一默认启动命令：将连接QQ，仅在本机配置及授权后执行
uv run jx3_bot
```

[生产配置模板](<.env.production.example>)与[测试配置模板](<.env.test.example>)提供QQ应用凭证、帐号专属QWeather Host与唯一的Ed25519 JWT鉴权配置。`QWEATHER_DEVELOPER_ID`、`QWEATHER_PROJECT_ID`、`QWEATHER_CREDENTIAL_ID`、`QWEATHER_PRIVATE_KEY_FILE`四项均必填。唯一入口默认只显式加载 `.env.production`，不自动读取历史 `.env`／`.env.prod` 或旧JWT脚本；使用新Ed25519密钥，不复用旧密钥。

**QQ HTTP 与 WebSocket 均使用应用层直连，不自动读取系统或环境代理。** WebSocket在websockets15显式禁用自动代理，并兼容14的直连接口，无需安装SOCKS依赖或关闭系统代理。系统VPN／TUN或网络路由仍可能影响实际出站，QQ WSS可达性须在部署机验证。

群聊与QQ单聊均支持 `天气 北京`、`天气 北京 朝阳`、`天气城市 北京`、`天气城市`、`天气城市 删除`、`天气`、`天气额度`；完整指令可无参数（传空 `args`），或用空格分隔参数，默认不需`/`。可选 `BOT_COMMAND_PREFIX=/` 时，所有业务关键字都必须加此前缀，例如 `/天气`、`/天气 北京`，且无前缀形式不再匹配。群与单聊默认城市分别保存，不跨会话共享；普通聊天不触发天气查询；支持的群聊和单聊消息默认归档并永久保存，不需要聊天开关或白名单；短期inbox／outbox不是聊天历史表。官方群@事件已去机器人@前缀；群主开启全量消息后，无需@模式仍须真实应用验证。

群聊（普通消息／@消息）与QQ单聊还支持 `开服 青梅煮酒`：从 JX3BOX 的 `https://spider2.jx3box.com/api/spider/server/server_state` 获取服务器列表，按完整 `server_name` 精确匹配。`connect_state=true` 回复已开服并显示“开服时间”，`false` 回复维护中并显示“维护时间”；两者均将接口的秒级 `maintain_time` 按北京时间（Asia/Shanghai）格式化为 `YYYY-MM-DD HH:mm:ss`，时间无效时显示“不可用”。其中“开服时间”是对上游 `maintain_time` 的展示标签，其实际语义尚未验证。缺少参数时默认查询绝代天骄，未找到服务器或接口故障分别提示，不将故障当作维护中。此命令遵循相同的 `BOT_COMMAND_PREFIX`，例如 `/开服 青梅煮酒`；无缓存、无自动重试、不占天气额度，也不新增配置或依赖。真实上游接口及QQ消息投递尚未联调。

城市使用官方CSV本地优先、GeoAPI即时兜底；本地重名提示ID／行政区，默认城市只保存用户主动输入。Geo返回ID／名称／坐标／命名时区不建立持久索引，也不借缓存／outbox保存。第三方原始城市数据不默认提交源码或打包发布。

```dotenv
QWEATHER_MONTHLY_REQUEST_LIMIT=50000
QWEATHER_USAGE_TIMEZONE=Asia/Shanghai
```

月上限只计**本天气模块自身**Geo／预报／每次重试的外发尝试；SQLite请求前原子占位，失败不退、重启不清、存储失败拒发，0禁用外发。缓存命中／本地查询／QQ／公开CSV下载不计；不读取帐号使用统计，不处理其他项目消费，不承诺帐号整体免费。不得删除／回滚数据库绕过计数。

### 输出模板与功能匹配

每类结果集中在[本地天气模板](<src/jx3_bot/modules/weather/templates/weather.toml>)，包含预报、默认城市、额度、消歧与错误提示；标题／正文／页脚统一排版。默认是美化文本，可在本机已有专用配置中单独设置后重启官方进程：

```dotenv
QQ_REPLY_FORMAT=markdown
```

[QQ官方Markdown文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html)已向所有机器人开放群／单聊自定义Markdown；本实现直接发送正文，不使用新版已废弃的官方模板ID。支持公开能力不等于实际客户端已联调，图片／按钮尚未实现。未配置时采用`text`；不要复制示例覆盖真实凭据。

[QQ业务路由](<src/jx3_bot/routing.py>)借鉴Matcher/Handler的装饰器用法，但没有NoneBot／OneBot依赖。启动时仅按确定顺序扫描可信 `jx3_bot.modules.<业务>.handlers`，自动编译 `@on_group(match="天气")`／`@on_private(match="天气")` 等规则，业务无需填写 `scene` 或路由名；群 `at_me=False` 默认只接普通群事件，显式 `True` 只接群@事件，私聊无 `at_me`。处理函数可选声明安全 `event`、带参或无参指令的 `args`，并仅通过显式 `Inject` 注入应用装配的业务服务；不直接发送QQ消息。同一函数叠加装饰器时每条消息至多调用一次，回复仍走既有outbox。[天气处理函数](<src/jx3_bot/modules/weather/handlers.py>)及[天气模板](<src/jx3_bot/modules/weather/templates/>)为现有示例；[旧匹配器](<src/jx3_bot/matching.py>)暂留内部／兼容入口。默认不注册全聊天业务处理器；聊天采集与匹配独立，支持的消息默认归档，不自动过期。

升级前停止同数据库进程并备份；首个CLI打开数据库时兼容迁移旧群schema，保留偏好、预算、网关会话 ID、已保存的接收序号和 outbox 中的回复记录。**重启前未触发的旧业务不补执行**：启动时终结遗留 inbox 任务；已写入的聊天记录、网关会话 ID、接收序号及 outbox 回复记录不因此删除。回复是否发送仍取决于原有状态、有效期限及重试限制；重启前发送中的记录转为 `uncertain`，不会自动重发。处理函数／模板改动均需重启；发送前固定格式和 payload，不因重启或发送失败换格式重发。

### 双向聊天数据库（离线实现，默认永久保存）

维护者已批准四表设计；[聊天仓储](<src/jx3_bot/modules/chat_history/repository.py>)实现 `chat_users`、`chat_groups`、`chat_user_messages`、`chat_group_messages`，不创建会话／身份映射业务表。单聊外键 `user_id` 指向本应用用户表；没有独立@列，运行时匹配上下文按事件类型推导。

实际入站顺序是**统一解析→消息／待处理任务／可靠水位同事务提交→聊天日志→匹配器／业务**；普通支持的消息同样归档，无须匹配天气。出站只在QQ返回非空成功消息ID后**插入聊天历史并标记outbox sent同事务→成功发送日志**；发送前不建pending历史行。不确定、失败或过期仅在传输状态记录；ACK后本地落库失败不重发，QQ与SQLite间的崩溃窗口可能留下历史缺口。

支持的QQ群聊、群@和单聊消息默认归档，无聊天开关、授权白名单或自动清理时限；旧`BOT_CHAT_ENABLED`／`BOT_CHAT_GROUPS`／`BOT_CHAT_USERS`／`BOT_CHAT_TEXT_DAYS`／`BOT_CHAT_DEDUP_DAYS`即使仍在旧配置中也不再生效。可选`BOT_CHAT_MAX_TEXT_CHARS`只限制单条脱敏后的文本长度；不保存原始事件或附件URL。旧库保留`expires_at`与`dedup_expires_at`列作为兼容占位，但不会按旧日期隐藏或删记录；以前已经删除的正文无法恢复。`chat-forget`仅用于主动指定会话逻辑删除，**不能擦除SQLite WAL、旧备份或运行管理器日志**；离线历史只读，清理与遗忘使用现有单实例锁、绑定AppID。写操作前须停服备份，勿复制模板覆盖真实配置；实际QQ群全量／@双投／回投行为尚待授权验证。

```bash
# 安全离线操作示例：请用已有、已绑定AppID的专用库路径；不单独传AppID，勿对运行中实例绕过锁操作
uv run jx3_bot --database /path/to/existing.sqlite chat-history --scene group --conversation GROUP_OPENID --limit 100
uv run jx3_bot --database /path/to/existing.sqlite chat-cleanup
uv run jx3_bot --database /path/to/existing.sqlite chat-forget --scene group --conversation GROUP_OPENID --confirm
```

具体参数以`--help`和[入口](<src/jx3_bot/__main__.py>)为准；清理／遗忘属于写操作，前者只维护非聊天的到期传输／缓存状态而不清聊天历史，后者仅遗忘指定会话。

### 控制台日志

官方进程重启后默认开启单行聊天、每个QQ回包摘要和处理／回复结果日志，已有专用配置缺少新字段时使用默认值，不要复制模板覆盖实际凭据。示例是合成内容：

```text
2026-10-11 13:00:01 INFO [receive] [群聊] 未知群（group_openid） - 小明（member_openid）：天气 北京
2026-10-11 13:00:02 INFO [receive] [单聊] - 小红（user_openid）：你好
```

官方群消息不含群名，所以显示未知群；昵称来自`author.username`，为空则显示未知用户。ID是官方OpenID，不是QQ号／群号。单聊收到后也展示；有效天气命令通过QQ业务路由处理并走官方C2C回复，普通单聊不触发业务；支持的聊天消息默认归档且不自动过期；当前新流程在消息提交后输出聊天展示，原始WebSocket帧不作为聊天正文日志来源；默认诊断仍为选字段的摘要，不等同于完整原始报文。所有模式的日志层均不按字符数、引用条目数、附件数或引用层数截断，也不脱敏；`safe` 方法仅进行单行转义。摘要字段选择与隐藏正文开关仍保留，不扩增新的网络日志。日志采集及终端留存须运维独立治理。

`BOT_LOG_LEVEL=INFO`、`BOT_QQ_RECEIVE_LOG=summary`、`BOT_QQ_CHAT_LOG=true`、`BOT_QQ_LOG_MESSAGE_TEXT=true`为默认值。`summary`只输出选字段摘要，**不会还原官方WS的`op/d/s/t`报文**。需要原始WS文本时可显式设`BOT_QQ_RECEIVE_LOG=payload`：`qq.ws.payload raw=`记录收到的完整原始WS文本（含顶层与`d`，完整JSON字符串转义后单行输出；对`raw`值做JSON解码即可恢复完整原文），不是聊天归档投影；即使报文不是合法JSON也会完整记录。`BOT_LOG_MAX_PAYLOAD_CHARS`已废止并忽略，旧配置即使含非整数或越界值也不会因此导致启动失败；生产模板不再提供此项。所有模式日志均不脱敏，可能暴露凭证、URL、OpenID、昵称及私聊正文；`payload`还会完整记录`message_scene.ext`鉴权值、附件URL等原始字段，运维须自行治理stdout采集、访问与留存，勿将真实敏感日志提交或复制到需求记录。`off`关闭逐帧／逐HTTP回包摘要；聊天展示与失败／处理日志独立保留。聊天日志仅转义其数据源中已有内容，不再追加脱敏；HTTP回包仍是既有选字段摘要，而非完整HTTP报文，不新增请求／响应／Header网络日志。

“不截断、不脱敏”仅约束日志层，不改变数据库和业务独立保存策略：原有 `redact` 函数继续供这些调用方使用，数据库上游的脱敏、截断及有界投影（例如`BOT_CHAT_MAX_TEXT_CHARS`）不变；聊天日志读取已提交的数据，上游已脱敏、截断、删除或未保存的内容无法由日志恢复。已有的正文截断标记反映数据源状态，不代表日志又截断一次。控制台输出仍使用1024条有界队列，慢输出可能丢弃整条日志并通过`log.loss`披露；停机刷新有超时，输出通道故障或强制终止仍可能丢日志，不能将完整单条内容等同于保证所有日志送达。

观察`qq.gateway.ready`／`resumed`确认连接阶段，`qq.dispatch.result`区分未匹配／不支持／重复／已接受，`reply.result`显示sent／failed／uncertain等结果；sent仅表示官方接口确认且outbox已提交，不表示用户读到。程序不新增日志文件，运行管理器可能保存stdout／stderr，需限制访问和保留。

### 依赖维护与离线验证

以[项目配置](<pyproject.toml>)和[锁文件](<uv.lock>)为依赖事实来源；项目仅使用uv管理和安装依赖，不维护pip兼容requirements清单。修改依赖后执行 `uv lock` 并重新同步、测试；常规安装使用 `uv sync --locked` 防止静默改变锁文件。

```bash
# 修改项目依赖后才执行：uv lock && uv sync --locked
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q src/jx3_bot tests
uv pip check --python .venv/bin/python
```

uv 默认不向环境安装 pip，无需另行补装 ensurepip。

离线测试覆盖聊天仓储、配置与CLI、合成收发及故障注入、天气与传输回归。验证应使用合成事件、临时数据库和新生成测试密钥，不加载真实凭据；不为文档或离线验收启动QQ服务、获取真实Token或调用天气受保护数据接口。公开CSV下载不是业务联调。离线结果不等于群内上线验收；生产监控、CI／lint／类型检查尚未配置。

## 唯一运行入口与旧部署退出

仓库不再提供 `bot.py`、旧插件、旧 `requirements.txt` 或 NoneBot 配置；不要再用旧虚拟环境、旧个人号客户端启动此项目。当前配置与运行说明见[正式文档索引](<docs/README.md>)。官方服务**代码清理**不等于生产**切流**：部署人员需先备份官方 SQLite／确认预算和待发送状态，再在授权群验证官方权限、事件与回复，停掉可能重复回复的旧进程，最后按[官方部署指南](<docs/qq-integration/2026-10-10-deployment.md>)切换。旧 AI／复读／爬虫未迁移，不能把功能损失描述为等价替换。

## 开发路线与文档

1. **QQ官方接入**：首版官方链路代码／离线验证已完成，旧框架文件已清理；下一步本机配置、授权群联调与生产切换。
2. **聊天数据库**：已获批准并完成四表与收发顺序的离线实现；下一步授权本机部署、真实QQ群／单聊与回投行为联调，其他业务后续重新规划。
3. **2027年1月完全替代萌萌**：以确认功能清单、群内对照、试运行和切换验收为准，不以天气或接入代码完成代替业务验收。

- [项目路线图](<docs/project/2026-10-10-roadmap.md>)、[文档索引](<docs/README.md>)、[AI开发约定](<AGENTS.md>)。
- [部署指南](<docs/qq-integration/2026-10-10-deployment.md>)说明配置、运行与切换要求。
- [Agent需求索引](<.agents/notes/INDEX.md>)统一管理需求、方案与历史过程记录；历史记录不替代正式运行说明，也不自动授权后续开发。

### 待办事项

- [x] **单聊／群聊双向记录数据库（离线实现）**：维护者批准的4张聊天业务表、成功发送归档及“提交→日志→匹配”顺序已经离线测试；上线前尚需授权平台联调、备份／日志治理和部署验收。
- [ ] **多实例／多 AppID 部署支持（待实现）**：共享业务代码，每个 QQ 应用独立配置与实例，隔离 Token、WebSocket／Resume、数据库、待处理消息／回复及用户偏好；同一天气项目的调用上限需统一预算管理或保守额度分配，保留历史计数，避免多实例重复获得完整额度。补齐共存、故障／重启、无跨应用误投递与预算并发验证。具体方案及排期待确认，见 [Agent需求索引](<.agents/notes/INDEX.md>)。
- [ ] **日志组建重构优化**：分trace/info等多个等级，最好是使用现有的log框架。对心跳，重新握手等系统级事件在info中隐藏，仅在info中展示和业务相关的事件，并统一处理后打印在控制台。支持文件持久化。

本条仅登记后续工作，不代表已实现或授权立即开发；**当前暂不处理 AppID 更换，不修改数据库绑定或现有数据。**

## 安全提醒

旧框架生产配置与旧 JWT 脚本已从工作树移除；现用生产配置 `.env.production` 原样保留私有凭据，根目录历史密钥原本已不在工作树；**Git 索引／历史可能仍含敏感数据**。若凭据曾提交或泄露，维护者须安全核对索引并撤销轮换；源码清理与新专用配置忽略规则不能消除历史泄露。

## 参考资料

- [QQ官方机器人API v2](https://bot.q.qq.com/wiki/develop/api-v2/)
- [和风天气每日预报](https://dev.qweather.com/docs/api/weather/weather-daily-forecast/)
- [天气历史参考目录](<docs/weather/reference/>)（历史资料，未经重新核实，不是现行接口契约）
