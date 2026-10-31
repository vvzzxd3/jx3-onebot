# 独立 QQ 官方天气服务：配置、部署与切换

**适用状态**：官方接入、天气与四表聊天记录链路已有离线实现；真实 QQ／天气 API 联调、部署机网络验证和正式生产切换仍待完成。本指南是现行部署说明，不代表部署验收已通过。项目功能与架构见 [项目 README](<../../README.md>)，后续范围见 [路线图](<../project/2026-10-10-roadmap.md>)。

## 1. 运行边界

唯一受支持入口为 [官方服务](<../../src/jx3_bot/__main__.py>)：Python 3.12、asyncio、httpx、websockets、SQLite；旧 NoneBot／OneBot 入口与插件已清理，不监听公网 HTTP 端口。当前实现 QQ 群／单聊天气命令、业务事件路由／本地输出模板，以及离线实现的 `开服 服务器名称` 查询，详见[输出模板与功能匹配](<../../README.md#输出模板与功能匹配>)；其他剑网3查询、AI 与复读尚未迁移。

代码层支持 Linux／macOS（单实例锁使用 `fcntl`）及 Windows x64（使用 `msvcrt`），并显式安装 `tzdata` 以覆盖 Windows 通常缺少 IANA 时区库的问题。当前 Windows 结论仅来自非 Windows 主机上的兼容回归与依赖交叉解析，尚未完成 Windows 实机部署验收；Windows ARM64 因锁定的 `cryptography` 没有对应 wheel，不属于开箱支持范围。部署账号需具备 QQ 群事件与被动回复权限，群主开启全量消息后才能验证无需 @ 的命令。公开 IPv6 地址不能证明 API／动态网关可达，必须在实际部署机验证 HTTPS／WSS 出站，必要时解决 IPv4 出站或 NAT64，程序不承诺纯 IPv6 可用。

QQ HTTP与WebSocket均为应用层直连，不自动读取系统／环境代理；WebSocket在15显式设置proxy=None，14保留原直连接口，无需安装python-socks、关闭系统代理或清除终端代理变量。系统VPN／TUN和网络路由仍可能影响出站；实际QQ WSS连通性须由维护者在部署机验证。

## 2. 独立环境与安全配置

使用 [uv](https://docs.astral.sh/uv/getting-started/installation/) 管理Python、默认 `.venv` 与锁定依赖。在仓库根目录运行；无需 `PYTHONPATH`、激活环境或设置 `UV_PROJECT_ENVIRONMENT`。生产配置使用 `.env.production`，不得覆盖已有私有文件；只有新部署且文件不存在时才按[生产模板](<../../.env.production.example>)创建。测试环境仅使用[测试模板](<../../.env.test.example>)生成独立 `.env.test` 和 `data/test/bot.sqlite3`，不得复制生产凭据／历史库。

```bash
uv sync --locked
# 只在首次配置且没有私有文件时手动执行：
# cp .env.production.example .env.production && chmod 600 .env.production
# 离线检验本机配置和JWT，绝不启动真实连接
uv run jx3_bot check-config
```

[Python版本文件](<../../.python-version>)固定3.12.11；[项目配置](<../../pyproject.toml>)与[锁文件](<../../uv.lock>)是唯一依赖来源。项目只使用uv管理和安装依赖，不维护pip兼容requirements导出。依赖改变后执行 `uv lock`、`uv sync --locked` 与离线验证：

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q src/jx3_bot tests
uv pip check --python .venv/bin/python
```

Windows x64 PowerShell 使用虚拟环境中的 Windows 路径执行依赖检查，其余 `uv run` 命令不变：

```powershell
$env:UV_CACHE_DIR="$PWD\.cache\uv"
uv sync --locked --no-build
uv run --locked python -c "from zoneinfo import ZoneInfo; import cryptography,httpx,jwt,websockets; print(ZoneInfo('Asia/Shanghai'))"
uv pip check --python .venv\Scripts\python.exe
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q src\jx3_bot tests
uv run --locked jx3_bot --help
```

Windows 上 `os.umask` 不提供 POSIX“仅所有者可读”的等价权限保证；生产数据库、锁文件、环境文件和私钥必须位于 NTFS 本地磁盘，并由运维使用 ACL 限制服务账号访问。SQLite WAL 与实例锁未承诺支持 UNC／网络共享目录。

uv 默认不安装 pip，无需补装 ensurepip。安装公共依赖、离线验证与真实业务联调不同，不能相互替代。

在本机填写 `QQ_APP_ID`、`QQ_CLIENT_SECRET`、帐号专属 `QWEATHER_API_HOST`，以及唯一的Ed25519 JWT鉴权；不要发送到聊天或提交Git。

- `QWEATHER_DEVELOPER_ID`、`QWEATHER_PROJECT_ID`、`QWEATHER_CREDENTIAL_ID`、`QWEATHER_PRIVATE_KEY_FILE`四项均必填。生成新的Ed25519密钥，私钥放到源码之外／被忽略的秘密目录，按官方控制台流程上传公钥。
- JWT只在本机签名：iss开发者ID、sub项目ID、kid凭据ID，iat预留30秒偏差，15分钟有效期；不为配置检查主动展开JWT。所有模式运行日志不脱敏，若既有日志字段含凭证会原样暴露，须独立治理日志访问与留存。
- 服务默认只显式加载 `.env.production`，不发现历史 `.env`／`.env.prod`；进程环境变量优先。`--env-file .env.test` 指定隔离测试配置，不得指向生产数据库。不要为排障主动在工具输出或文档中展开私有配置；运行日志按本指南不脱敏规则输出，存在凭证泄露风险；若已有配置仍将 `BOT_DATABASE` 设为旧默认值，CLI 只对这个**完全匹配**的旧值自动改为 `data/bot.sqlite3`；其他自定义路径须维护者自行安全核对，显式 `--database` 永远优先。
- 不复用仓库中有泄露风险的历史私钥；应先撤销／轮换。新增忽略规则不解决已提交私钥或旧Git历史风险。

离线校验（不会请求 QQ Token／天气接口）：

```bash
uv run jx3_bot check-config
```

## 3. 首次城市导入与更新

必须先导入官方城市列表，再启动服务；无城市来源记录时启动在联网前失败，以免所有查询意外转成GeoAPI。

```bash
# 官方公开GitHub下载；不占天气数据API计数
uv run jx3_bot import-cities --download

# 或使用自行下载的官方CSV
uv run jx3_bot import-cities --csv /safe/path/China-City-List.csv
```

导入校验版本／表头／唯一ID／坐标范围／时区，验证下载blob哈希，事务替换城市库并记录SHA256、版本、来源及时间；失败保留旧库。**更新前停止同一数据库上的新服务**，导入与服务共用实例锁。更新不清空月用量或用户自有输入偏好。

原始第三方CSV不提交源码、不默认打进镜像；未声明独立MIT／CC BY或无限再分发权。官方列表存在覆盖和时效限制，本地优先是经维护者批准的产品选择，不冒称供应商推荐顺序；列表外地点由GeoAPI即时兜底，不建立GeoAPI结果索引。

## 4. 启动、命令与上限

```bash
# 唯一默认启动命令：将连接QQ，仅在本机配置及授权后执行
uv run jx3_bot

# 离线查看本模块当前月尝试数，不要求QQ／天气凭证
uv run jx3_bot usage

# 所有全局选项放在子命令之前
uv run jx3_bot --env-file .env.test --database data/test/bot.sqlite3 usage
```

| 群／单聊命令 | 行为 |
| --- | --- |
| `天气 北京` | 本地搜索／Geo兜底并返回每日预报 |
| `天气 北京 朝阳` | 中文行政区消歧；本地重名也可按回复提示用城市ID |
| `天气城市 北京` | 只保存本人在当前群／单聊的主动输入，不隐式调用天气 |
| `天气城市` | 查看本人在当前群／单聊的默认输入 |
| `天气城市 删除` | 删除默认输入 |
| `天气` | 使用默认输入；未设置则提示 |
| `天气额度` | 查看天气模块本月外发尝试数／配置上限 |

默认命令与参数间需要空格，不使用 `/` 前缀；若设置 `BOT_COMMAND_PREFIX=/`，须统一输入 `/天气` 等带前缀命令；普通聊天不触发天气查询；支持的群聊／单聊消息仍默认归档并永久保存。首版预报默认3天（可配置1–10天），本地来源缓存默认1小时且不跨地点本地午夜；显示天气归因。Geo路径不缓存，回复不保留Geo返回的名称／ID／坐标／命名时区，默认城市仅存用户输入。

| 配置 | 默认／范围 |
| --- | --- |
| `BOT_DATABASE` | `data/bot.sqlite3`；测试配置示例独立用 `data/test/bot.sqlite3` |
| `QWEATHER_MONTHLY_REQUEST_LIMIT` | 50000，非负整数；0禁用天气外发 |
| `QWEATHER_USAGE_TIMEZONE` | Asia/Shanghai；数据库绑定后不允许直接改时区 |
| `QWEATHER_FORECAST_DAYS` | 3，1–10 |
| `QWEATHER_CACHE_SECONDS` | 3600，0–21600；0禁用缓存 |
| `QWEATHER_MAX_RETRIES` | 1，0–2；每次外发均计数 |
| `BOT_WORKERS` | 2，1–8；同应用／scene／会话／用户命令FIFO |
| `BOT_COMMAND_PREFIX` | 空；可配置最多16个非空白可打印字符，例如 `/`，非空时指令必须加此前缀 |
| `QQ_HEARTBEAT_ACK_INTERVALS` | 2，1–5 |
| `QQ_REPLY_FORMAT` | text；markdown启用群／单聊自定义Markdown，不使用官方模板ID |

月额度只统计该天气模块Geo／每日预报／重试的外发尝试；请求前SQLite原子占位，失败／超时／崩溃不返还，缓存和本地搜索不计。只保证持久化状态正常时自身不超过配置限额，**不统计帐号其他项目、不读取帐号统计、不保证帐号整体免费**。降低上限低于已用量立即熔断，剩余有效缓存仍可使用。

结果模板与Markdown支持见[输出模板与功能匹配](<../../README.md#输出模板与功能匹配>)；修改[本地天气模板](<../../src/jx3_bot/modules/weather/templates/weather.toml>)后需重启进程，check-config会离线检查必需模板和占位符。已有配置缺少QQ_REPLY_FORMAT时使用美化文本；要启用Markdown只在本机增加该字段，不覆盖真实凭据。

## 5. 存储与故障处置

升级前停止原官方进程并做一致性备份，继续使用同数据库。CLI首次打开时对旧群preferences／inbox／outbox原子迁移，加入scene，原群数据与预算、水位、seq、attempts和uncertain不重置；新代码迁移后不要直接运行不认识新schema的旧官方版本。回复格式与payload在outbox中固定，配置变化不改变已计划的回复。单聊保守沿用原始时间＋300秒，Token预检之后、POST之前再次检查期限，过期清正文且不外发。真实单聊窗口文档存在60分钟／5分钟冲突，未擅自扩大。

- 同一数据库仅一个运行实例／导入器；符号链接规范到同一路径加锁，硬链接数据库不支持。不要绕过CLI锁直接启动多个实例或将SQLite放在不可靠的共享文件系统。
- 数据库绑定QQ AppID与计数时区；凭据轮换不重置计数，不向另一应用发送旧outbox。多实例／多AppID仅为待确认待办；当前不处理AppID更换，不修改现有数据库绑定或数据。未来方案须使用隔离的实例与数据库，并保守保持共享天气预算，另行批准后实施。
- **不得删除、重建、随意换路径或回滚月计数数据库以“恢复额度”**。默认数据库位于 `data/bot.sqlite3`；路径迁移前应停服并做一致性备份，迁移后核实完整性并保留原有预算与传输状态，私有备份应限制访问。恢复旧快照可能低估本月次数，必须保守对账后再联网。
- 当前四表对支持的群聊／单聊消息默认保存入站正文和本应用已确认发送的出站正文，不自动到期；不保存附件鉴权URL或原始全量事件。短期inbox/outbox退役不等于删除聊天历史；主动遗忘仅逻辑擦除，WAL／备份另需治理。长期数据还包括主动城市偏好、官方CSV派生城市库及逐月自身计数。详见[双向聊天数据库](<../../README.md#双向聊天数据库离线实现默认永久保存>)。
- 重启恢复会终结旧 `pending`／`working`／`unmatched` inbox 业务，不补执行因重启尚未触发或未完成的处理函数；已写入的聊天记录、网关会话 ID 和接收序号不因此删除或重置。已写入 outbox 的回复记录仍在数据库中，是否发送仍受其状态、有效期限及以下重试限制约束，不会因为记录仍在就直接重新发送。
- outbox先持久化、每个逻辑回复固定msg_seq=1。只有“令牌预检失败且确认未进入消息POST”可在5分钟内最多三次同序号尝试；真正发送结果不确定或重启发现sending统一置uncertain，不自动重发或改序号。用户可发新命令，程序不冒称未知回复已送达。
- op9／需要新会话的关闭码先按序排空已收到的有界队列再清Resume状态；落盘失败／15秒排空失败停止而非跳水位。普通断连优先Resume；未知关闭码停止供人工核实，十次连续短连接失败停止，避免无限Identify。
- 默认启用单行聊天与QQ回包摘要：聊天会话／发送人OpenID、已有昵称和正文按要求展示；默认其他回包只输出选字段摘要。所有模式日志层均不脱敏、不截断，`safe` 方法仅单行转义，可能暴露凭证、URL、身份及私聊正文；显式`BOT_QQ_RECEIVE_LOG=payload`还会完整输出原始WS文本，包括其中的`message_scene.ext`鉴权数组或附件URL。CLI关闭httpx/httpcore/websockets普通请求／协议DEBUG。应用不建日志文件，但运维采集stdout／stderr时需限制访问和保留；生产资源监控和退出告警仍需配置，不要无限快速重启耗尽启动额度。

### 5.1 单行日志与无响应排查

同一配置与数据库重新启动官方进程后，缺少新日志配置时也默认生效；不用覆盖真实专用配置。聊天示例为合成数据：

```text
2026-10-11 13:00:01 INFO [receive] [群聊] 未知群（group_openid） - 小明（member_openid）：天气 北京
2026-10-11 13:00:02 INFO [receive] [单聊] - 小红（user_openid）：你好
```

群消息未提供群名称，固定显示未知群；用户名取`author.username`，缺失／空值显示未知用户，ID不是实际QQ号。单聊已通过QQ业务路由处理有效天气命令；普通受支持单聊默认入四表聊天历史，匹配结果可为command_not_matched；unsupported_event表示未支持的其他事件。

| 日志配置 | 默认／范围 |
| --- | --- |
| `BOT_LOG_LEVEL` | INFO；DEBUG／INFO／WARNING／ERROR／CRITICAL，提高到WARNING将隐藏聊天和普通回包 |
| `BOT_QQ_RECEIVE_LOG` | summary；payload额外记录完整原始WS文本；所有模式日志层均不脱敏（可能暴露凭证、URL及私人信息）；off关闭逐帧／逐HTTP回包摘要但保留聊天及处理／失败日志 |
| `BOT_QQ_CHAT_LOG` | true；false关闭聊天行，不关闭协议摘要 |
| `BOT_QQ_LOG_MESSAGE_TEXT` | true；false正文显示隐藏，ID／昵称仍展示 |

`BOT_LOG_MAX_PAYLOAD_CHARS`已废止并忽略，包括非整数／越界等旧非法值，不能因此导致启动失败；生产模板已移除此项。日志时间固定Asia/Shanghai，stdout逐行flush。所有模式日志层不按字符数、引用条目数／深度或附件数截断，也不脱敏；`safe` 方法仅转义换行及控制字符以保持单行，隐藏正文开关仍生效。`payload`模式的`qq.ws.payload raw=`是完整原始WS文本的JSON字符串编码（将`raw`作为字符串解码即可恢复原文，而不是解析后的聊天投影），保留`op/d/s/t`和未知字段，Hello、READY及畸形报文也完整记录。默认仍为summary，级别与日志开关默认不变；summary与HTTP响应仍为既有选字段摘要，不是完整报文，不扩增新的网络请求／响应／Header日志。日志可能暴露凭证、鉴权字段、URL和私人信息，运维自行治理采集、访问与留存，不将真实敏感日志复制到文档／需求记录。

日志规则不改变数据库／业务独立保存策略：原有 `redact` 函数继续供这些调用方使用，数据库上游脱敏、截断与有界投影不变。聊天日志读取已提交的数据，不能恢复此前已脱敏、截断、删除或未保存的内容；数据源自带的截断标记不代表日志二次截断。关注以下顺序：

1. `qq.gateway.ready`／`qq.gateway.resumed`：只有进程启动还不够，先确认握手／恢复阶段。
2. `[receive]`与`qq.ws.receive`：没有目标事件时检查平台订阅、群开关、测试群范围，不推断群内没有聊天。
3. `qq.dispatch.result`：reason区分command_not_matched、unsupported_event、missing_identity、expired等；state区分accepted、duplicate_message、stale_sequence和失败。
4. `command.start`／`command.done`／`reply.planned`：判断worker或业务／存储阶段；weather.result显示额度／供应商等安全分类。
5. `qq.http.response`／`reply.result`：HTTP200仍检查code，明确failed／uncertain／预检重试，不因排障自动重发不确定POST。

控制台慢输出使用1024条有界队列，缺口通过log.loss累计披露；停机刷新最多2秒，超时尝试独立stderr告警log.flush_timeout。慢输出仍可能丢弃整条日志，输出通道不可用或强制终止无法保证日志完整；不截断单条内容不代表所有日志送达，不是无丢失审计。

## 6. 联调与正式切换检查表

1. 在部署机验证API Host、QQ HTTPS及接口返回的WSS域名DNS／TLS／出站；不要仅检查入站IPv6。
2. 使用新应用凭证和新天气凭证，群主开启全量消息；在授权测试群分别验证 `天气 北京` 与@命令，确认d.id、group_openid、member_openid、原始timestamp与实际内容形态。
3. QQ单聊同样测试天气、设置／查看／删除默认城市，确认author.user_openid与C2C回复目标；验证不影响群偏好。选用Markdown时在实际客户端检查标题／正文／来源展示，失败不自动换格式重发。检查真实每日v1的数量／条件／归因，必要时只保留完全脱敏的结构样本；测试同名、无结果、缓存和配置0时的熔断。
4. 人工模拟断连／重投／凭证失效，确认业务去重、seq=0恢复和稳定msg_seq。关闭码与op9策略目前仅离线模拟通过，实际版本行为尚未确认。
5. 代码层已移除旧入口／旧依赖／旧插件，**不等于旧进程已停止**。试运行通过后由维护者在授权环境停止旧个人号客户端与旧进程、确认没有重叠回复，再切换唯一官方入口；若旧AI／复读／爬虫曾被使用，应确认功能中断已获接受并另行规划替代，不可直接声称等价迁移。切换前备份一致性SQLite并检查月预算、inbox/outbox及日志／备份隐私边界，切换后持续观察真实平台权限、双投与出站结果。

上述清单为待执行的验收要求，不是已完成结果。离线测试与公开CSV下载不能证明真实QQ／天气受保护API、群内行为或生产切换已经通过。Agent需求及历史过程记录统一从 [Agent需求索引](<../../.agents/notes/INDEX.md>)进入，不作为当前部署状态的替代依据。
