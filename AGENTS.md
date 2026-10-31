# AGENTS.md

## 适用范围与项目定位

本文件约束整个仓库中的 AI 开发工作；更深层目录的约定仅在其范围内补充或覆盖本文件。默认使用中文撰写开发文档与工作总结。

- 本项目是 Python IM Bot **服务端**，业务方向是剑网3查询，仅服务QQ。
- **当前实现**：[QQ官方服务唯一入口](<src/jx3_bot/__main__.py>)，WebSocket＋HTTP回复＋天气首功能及已批准的四表双向聊天数据库代码存在；旧NoneBot2／OneBot入口、插件、依赖和环境配置已从工作树清理。**未进行真实QQ联调或生产切换**，不把代码清理冒称部署完成。
- **目标边界**：按 [QQ官方机器人API v2](https://bot.q.qq.com/wiki/develop/api-v2/) 直接接入，部署不依赖NoneBot2、OneBot适配层和个人号客户端。暂无多平台或OneBot兼容规划；旧AI／复读／爬虫未迁移。
- **业务目标**：当前四表单聊／群聊双向记录数据库已完成离线开发；长期 **2027年1月完全替代萌萌**目标保留，其他业务后续另行规划，范围见 [路线图](<docs/project/2026-10-10-roadmap.md>)。
- [README](<README.md>) 面向使用者；本文件面向开发代理；现行正式文档进入 [文档目录](<docs/README.md>)，需求过程记录进入 [需求记录索引](<.agents/notes/INDEX.md>)。

## 必须遵守的文档流程

过程记录统一保存到 `.agents/notes/`，以需求包而非模块或会话为单位组织；`docs/` 保留面向使用者的正式文档与现行规范，不再新增过程记录。详细规则见 [记录规范](<.agents/notes/README.md>)，使用 [任务模板](<.agents/notes/templates/task.md>)，并维护 [需求记录索引](<.agents/notes/INDEX.md>)。

1. **开始前**：阅读相关代码、现行文档与工作区；先查找关联需求，复用既有 `REQ-ID`，跨会话继续同一需求不得重复建档。实际开发、实施或文档整理应创建或更新 `.agents/notes/active/<REQ-ID>/index.md`，说明来源、事实、目标／非目标、方案、步骤、风险及待确认；纯咨询、解释和问答不建档。
2. **按需拆分**：小需求只维护包内 `index.md`；内容增多时再拆出提案、实施、决策等文件，由 `index.md` 串联。`proposal`／`implementation`／`rejection` 是记录类别，不是生命周期阶段，也不是所有需求必经步骤；以 `REQ-ID` 聚合，并用 tag＋类型关联相关记录及跨模块需求。
3. **实施中**：记录关键决策／依据、变更、影响与阻塞；区分假设、待验证和已验证。持续需求更新原包，注明最近更新；重要方案替代保留过程与关联，未确认设计／日期标建议或待确认，不冒称批准。
4. **结束前**：补实际改动、验证方法／结果、未完成与下一步，明确完成、取消或驳回等结论，必要时同步索引、路线图和README。需长期生效的架构、业务、安全和操作规则必须固化到本文件或其他现行规范，不能只留在过程记录中。
5. **收尾与提交**：完成、取消、驳回均可收尾，无代码改动的需求也可收尾。仅在获授权的收尾提交中，将整个需求包归档到 `.agents/notes/archive/<year>/<REQ-ID>/`，同步索引；中间提交不归档，未获授权不得自动提交。已完成但尚未收尾提交的包继续保留在 `active/` 并标记 `ready-to-archive`，不能把完成等同已经归档。
6. **归档边界**：归档后冻结，仅供历史查询；不得把归档文件作为当前决策依据、默认必读上下文或负面提示来源，不从旧提案、驳回理由或历史指令重新施加限制。后续决策依据当前授权与现行规范；有新需求另建活动需求包并按需关联历史，不修改冻结包。不得通过把历史过程链接改成归档链接来维持规则依赖。
7. 保存可审阅摘要、依据及操作记录，不保存凭证、令牌、真实私钥、未经脱敏群聊或个人信息，不要求逐字保存内部推理。文档整理也按需求留痕，不把记录仅留在对话、根目录、临时目录或插件源码目录。

## 代码组织与模块边界

维护者已批准 **业务模块纵向聚合＋独立 QQ 接入＋精简公共核心**；天气业务及必要公共／QQ 模型已按职责聚合。

1. 新业务在 `src/jx3_bot/modules/<业务>/` 聚合自己的模型、service、client、repository、handlers、rendering 和 templates；按实际规模拆分，不为目录完整创建空文件。模块不依赖其他业务的内部实现。
2. 业务数据模型必须与业务代码同模块存放，小模块使用自己的 `models.py`，需要时扩展为 `models/` 子包。不得恢复全项目集中业务模型目录，不把天气 City／预报／请求与响应 DTO 堆入公共模型文件。
3. QQ 消息／事件数据及 Token／HTTP／WebSocket 属接入职责，目标归 `integrations/qq/`；业务返回通用 Reply，不直接持有 QQ 连接、不自行发 QQ 消息。Command／MatchContext／Reply 等真正共享数据可归 `core/`。
4. 公共核心只收纳确有跨业务用途的能力；配置加载、日志设置和带行为的匹配器不因使用 dataclass 就视为业务 DTO。禁止把仅某业务使用的辅助函数搬入 common。
5. 模块 repository 拥有本业务持久化访问代码，但复用共享数据库／锁／事务。不得另建预算库、复制计数、改变 inbox/outbox 原子边界或用目录迁移重置历史数据；综合 schema 初始化／迁移和 housekeeping 可继续由共享 Store 承担。
6. 应用入口负责装配模块；业务命令注册与模板放业务目录，通用匹配／安全 payload 构建独立于业务。旧平铺路径只保留薄兼容导出，不再往其中新增实现，内部代码优先引用实际归属模块。
7. 分阶段迁移：其余 QQ 传输、日志、完整匹配器和公共数据库设施仍保留旧位置，不为统一目录顺带大迁移。先保持行为完成职责归类，再单独补请求／响应模型及校验，文档明确哪些仍是字典解析。
8. 每次结构调整检查导入循环、序列化／旧 inbox 兼容、模板路径、测试与文档链接；验证命令、接口、预算、隐私及发送可靠性不变，未联调不冒称上线。

### QQ 业务事件处理约定（已离线实现）

- 业务处理函数定义在 `src/jx3_bot/modules/<业务>/handlers.py`。应用启动时调用[自动发现与注册](<src/jx3_bot/routing.py>)，仅扫描可信 `jx3_bot.modules` 的直接业务子包并按名称排序导入其 `handlers`，每个应用单独构建注册表；无运行时热加载，不扫描任意路径或依赖业务在入口手工登记。旧[匹配器](<src/jx3_bot/matching.py>)保留给内部／旧调用方，不让新业务依赖跨重启稳定的业务路由名。
- 使用 `@on_group(match="天气")`／`@on_private(match="天气")`；`match` 可省略以显式接收该场景的受支持文本，默认并未注册此类兜底处理器。群 `at_me=False` 默认仅匹配 `GROUP_MESSAGE_CREATE`，显式 `True` 仅匹配 `GROUP_AT_MESSAGE_CREATE`；私聊仅匹配 `C2C_MESSAGE_CREATE`，不存在私聊 `at_me`。一个函数可叠加装饰器，首次匹配只调用一次；`priority`、`block` 延续注册表的同优先级组合回复与后续优先级阻断语义。不得扫描正文猜测提及关系或虚构 QQ 事件类型。
- `match="天气"` 匹配 `天气`（`args=""`）与 `天气 北京`（`args="北京"`），不匹配 `天气预报`；配置 `BOT_COMMAND_PREFIX=/` 后须输入 `/天气` 或 `/天气 北京`。前缀可空，不能包含控制字符／空白，长度不超过16。业务可选声明 `event: GroupMessageEvent | PrivateMessageEvent`（单场景使用对应单一类型）、匹配关键字时才可声明 `args: str`；无参函数也允许。事件仅包含已提交、脱敏、有界的身份、正文和事件类型投影，不含QQ原始 `d`、URL／凭证等；业务只能返回 `Reply | str | None`，不持有QQ连接或直接发消息。可参考[天气处理函数](<src/jx3_bot/modules/weather/handlers.py>)。
- 应用仅把显式声明 `weather: WeatherService = Inject("weather")` 之类的业务服务按名称和类型注入；不得把 Store、QQ HTTP、配置或原始事件借别名注入。业务依赖由应用装配后显式提供，处理器签名／注解在启动期验证，业务返回值在分发期验证；新业务需增补合成事件、启动发现、注入与边界测试。
- 重启时[存储恢复](<src/jx3_bot/store.py>)在持有单实例锁下终结旧 `pending`／`working`／`unmatched` inbox 业务，不跨进程重新触发；已写入数据库的聊天记录及网关会话 ID、已保存的接收序号不因此删除或重置。已写入 outbox 的回复记录仍在数据库中，后续是否发送取决于其原有状态、有效期限及重试限制；重启前处于 `sending` 的记录转为 `uncertain`，不得自动补发。此规则不改变正常运行中的“入站同事务提交→聊天日志→业务匹配”顺序；不能为跨重启重放储存原始 QQ `d`。

## 当前代码导航

| 位置 | 说明 |
| --- | --- |
| [官方入口](<src/jx3_bot/__main__.py>) | 显式环境加载、单实例锁、run／check-config／usage／import-cities／chat-history／chat-cleanup／chat-forget |
| [QQ HTTP](<src/jx3_bot/qq_http.py>)、[WebSocket](<src/jx3_bot/gateway.py>) | 官方Token、动态WSS、独立心跳与可靠Resume／HTTP被动回复 |
| [应用](<src/jx3_bot/application.py>)、[存储](<src/jx3_bot/store.py>) | worker／FIFO、SQLite月计数／城市／偏好／短期inbox／固定seq outbox |
| [QQ业务路由](<src/jx3_bot/routing.py>)、[旧匹配器](<src/jx3_bot/matching.py>)、[渲染层](<src/jx3_bot/replies.py>)、[天气模板](<src/jx3_bot/modules/weather/templates/weather.toml>) | 业务装饰器与限定目录自动发现、旧command／fullmatch／regex兼容路径、文本／自定义Markdown与模块独立模板 |
| [日志](<src/jx3_bot/observability.py>)、[日志测试](<tests/test_observability.py>) | 单行群聊／单聊展示、默认选字段摘要、完整原始WS payload；所有模式日志层不截断不脱敏，有界队列可能丢整条日志 |
| [天气模块](<src/jx3_bot/modules/weather/>)、[模型](<src/jx3_bot/modules/weather/models.py>)、[客户端](<src/jx3_bot/modules/weather/client.py>)、[服务](<src/jx3_bot/modules/weather/service.py>) | 每日v1、JWT／Geo、预报／偏好／缓存编排；业务实现在同一模块 |
| [天气仓储](<src/jx3_bot/modules/weather/repository.py>)、[城市导入](<src/jx3_bot/modules/weather/cities.py>)、[命令](<src/jx3_bot/modules/weather/handlers.py>)、[格式化](<src/jx3_bot/modules/weather/rendering.py>) | 模块自己的SQL／CSV／注册／输出；复用共享Store的连接与事务 |
| [公共模型](<src/jx3_bot/core/models.py>)、[QQ模型](<src/jx3_bot/integrations/qq/models.py>)、[QQ事件解析](<src/jx3_bot/integrations/qq/events.py>) | Command／MatchContext／Reply 与QQ入站数据分属不同职责 |
| [旧天气路径](<src/jx3_bot/weather.py>)、[旧城市路径](<src/jx3_bot/cities.py>)、[旧混合模型入口](<src/jx3_bot/models.py>) | 仅兼容导出，不在此新增业务实现 |
| [项目依赖](<pyproject.toml>)、[uv锁文件](<uv.lock>)、[生产模板](<.env.production.example>)、[测试模板](<.env.test.example>) | 官方服务仅通过uv管理依赖；无旧NoneBot元数据和依赖 |
| [离线测试](<tests/>) | 合成事件、FakeSocket、MockTransport、临时SQLite／新生成测试密钥 |
| [文档索引](<docs/README.md>)、[需求记录索引](<.agents/notes/INDEX.md>) | 正式文档与需求过程记录分别导航 |

### 开发环境与命令

唯一QQ服务由uv完整管理，Python范围为3.12系列，[版本文件](<.python-version>)固定3.12.11。uv安装 `src/jx3_bot` 包和 `jx3_bot` console script，使用默认 `.venv`，无需设置 `PYTHONPATH` 或 `UV_PROJECT_ENVIRONMENT`。[生产配置](<.env.production>) 保留私有配置，测试模板不带凭据；SQLite使用 [现有数据库](<data/bot.sqlite3>)，迁移时保留原始数据及私有备份。单实例锁在Linux／macOS使用`fcntl`、Windows使用`msvcrt`，Windows缺少系统IANA时区数据时由`tzdata`提供；当前仅在非Windows主机完成兼容回归与Windows x64依赖交叉解析，尚无Windows实机验收，Windows ARM64因`cryptography`无对应wheel不属于开箱支持范围。旧框架已移除，真实联调未执行。

```bash
uv sync --locked
# 离线测试，不加载真实配置
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m compileall -q src/jx3_bot tests
uv pip check --python .venv/bin/python
uv run jx3_bot --help

# 默认启动会真实连接QQ；仅在本机安全配置／授权后执行
uv run jx3_bot
```

- 依赖新增／升级维护 [项目配置](<pyproject.toml>) 后执行 `uv lock`、`uv sync --locked`、离线验证。依赖仅由uv管理，不维护pip兼容导出清单或使用pip安装依赖；`uv pip check`仅用于检查环境。保持 [uv锁文件](<uv.lock>)与[Python版本文件](<.python-version>)可纳入Git。
- uv默认不安装pip，无需ensurepip；工具使用 `UV_CACHE_DIR="$PWD/.cache/uv"` 在工作区保存缓存，不改全局工具配置。
- CLI默认显式加载 [生产配置](<.env.production>)，测试通过 `--env-file .env.test` 隔离，不发现历史 `.env`／`.env.prod`；不为排障主动在工具输出／文档展开私有配置值；运行日志遵循下述已授权的不脱敏规则，其泄露风险另行治理。JWT测试只读新生成的临时密钥，不读根目录旧PEM或旧JWT脚本。
- 官方天气命令以精确关键字及空格参数识别，默认无`/`前缀；非空 `BOT_COMMAND_PREFIX` 必须出现在所有业务关键字之前。普通聊天不触发天气业务。四表双向消息及接收顺序已离线实现；真实@事件内容形态与双投行为尚待授权联调。
- 维护者已批准单行控制台日志：默认INFO展示群聊／单聊的官方会话／发送人OpenID、已有username及正文，群名未提供显示未知群；群／单聊天气统一匹配与回复。维护者进一步授权所有模式日志层不脱敏、不按字符数、引用条目数／深度或附件数截断；`safe` 方法仅单行转义。默认仍为 `BOT_QQ_RECEIVE_LOG=summary`，日志级别与聊天／正文开关默认不变；summary 与 HTTP 仍按既有字段输出摘要，不是完整报文，不扩增新的网络日志。`payload` 额外输出完整原始WS文本（完整JSON字符串单行编码，可解码还原，即使畸形文本也完整输出）。`BOT_LOG_MAX_PAYLOAD_CHARS`废止并忽略，旧非整数／越界值也不影响启动，生产模板移除该项。日志可能暴露凭证、鉴权数组、URL、OpenID及私人正文；运维自行治理采集、访问与留存，真实敏感日志不复制到需求记录。原有 `redact` 函数保留供数据库／业务独立保存策略使用，数据库上游脱敏、截断、有界投影及聊天事务顺序不变；日志不能恢复上游已脱敏或丢失的数据。1024条有界队列及 `log.loss` 报告保留，慢输出可能丢整条日志，刷新超时／通道故障／强制退出也可能丢失，不是无丢失审计。
- 离线测试覆盖四表、原子事务、收发顺序、清理／忘记和官方业务；尚无CI／lint／类型检查。变更须执行合适测试，在对应需求包记录实际命令、结果及适用范围；历史测试数量不代表当前结果，未执行的不写通过。
- 不为文档或离线验收启动会连接QQ、发送消息或调用天气数据接口的服务。公共CSV／公开文档访问不是受保护API联调，须分别记录。

### 已知限制与风险

- 旧AI／复读／爬虫已移除且未迁移；当前官方服务不提供这些功能。
- 旧框架JWT脚本与配置已从工作树移除，[生产配置](<.env.production>) 保留私有凭据，历史密钥原本已删除工作树副本；Git索引／历史可能含真实凭据。不得传播；真实泄露须撤销／轮换，不能把删除工作树文件当作消除泄露。
- 新服务仍未真实验证应用权限、DNS／TLS／动态WSS、纯IPv6出站或供应商响应；单分片／单实例和未知关闭码停止策略不是线上验证结论。

## 后续实现与维护边界

### QQ官方接入与天气

- 维护者已批准 **独立QQ WebSocket＋官方CSV本地优先＋GeoAPI即时兜底不保存返回地理字段＋天气模块自身自然月计数**，不得再以帐号初始用量、供应商账单时区或城市源选择阻止实现。
- QQ HTTP／WebSocket保持应用层直连，不自动读取系统或环境代理；默认WebSocket连接器兼容14／15，15显式禁用proxy。不得通过恢复自动代理或擅自新增SOCKS依赖绕过，策略变更另行确认。
- 新运行链路不新增多平台／OneBot兼容／Webhook第二入口；QQ接收与HTTP回复独立于业务。扩展前核实官方文档和平台权限，不把群主开关等同本应用已实测。
- 天气鉴权仅支持Ed25519 JWT；开发者ID、项目ID、凭据ID、新私钥路径四项必填，不恢复已取消的替代鉴权字段／Header。
- 月限默认50000／Asia/Shanghai，只计该天气模块Geo／每日／每次重试外发；SQLite先占位，失败不退、重启不清、存储不可用拒发。0禁用外发，缓存／本地搜索／QQ／公开CSV不计。不处理帐号其他用量，不保证帐号整体免费。
- 计数时区与QQ AppID绑定数据库，不能删／重建／随意换路径／回滚旧快照重置预算；迁移或恢复 [现有数据库](<data/bot.sqlite3>) 必须保留原始数据并保守保持月计数。单实例锁canonicalize路径，拒绝硬链接，不绕过CLI并发recover。
- 官方CSV与GeoAPI回包不同来源；CSV记录来源／版本／哈希，原始数据不默认提交或打包，不声明无限再分发许可。GeoID／名称／坐标／命名时区不通过缓存、偏好或outbox间接保存；偏好仅用户主动输入，命令FIFO。
- 接收可靠前缀与心跳最新seq分开；落盘失败不跳洞，清无效会话前排空队列。outbox固定seq，不确定发送／重启sending不自动重发；确定POST前预检失败有界重试，不变成主动推送。
- 部署操作见 [部署指南](<docs/qq-integration/2026-10-10-deployment.md>)；指南存在不代表已完成真实平台联调或生产验收。
- 多实例／多 AppID 仅登记后续工作，不代表获准实施；当前暂不处理 AppID 更换，不修改数据库绑定或现有数据。未来方案须隔离各应用的传输与数据，并保守保持共享天气预算，另行确认后实施。

### 聊天数据库

- 维护者已批准四表双向聊天数据库，代码已完成离线开发；现有数据库已迁移并保留私有备份，未启动真实服务，仍待授权平台联调／部署验收。
- 维护者已明确“会话”只是聊天记录集合；不建外部身份表或单聊／群聊会话容器，保留用户、群、两类消息业务表，记录本应用send结果。
- 入站顺序为统一解析、消息／待处理任务／水位同事务落库、输出聊天日志、最后匹配业务。单聊外键叫user_id；两类聊天消息均取消独立@标识字段，to_me只属运行时匹配上下文。
- 机器人发送成功后才插入聊天历史，出站行与outbox sent同事务提交后输出成功发送日志，不预建pending聊天行。failed／expired／uncertain仅属outbox与诊断；成功后本地落库失败不重发QQ消息、不伪造成功归档日志，也不伪造本应用OpenID。
- 支持的群聊／单聊默认全部保存，不设白名单或聊天保留时间；旧授权／保留配置均忽略。四表旧到期列仅为原位兼容占位，不按旧日期删除或隐藏；`chat-cleanup`仅清理传输／缓存等短期状态，不清聊天历史；手动`chat-forget`仍可逻辑删除，无法恢复此前已经抹除的正文，也无法确保清除WAL／备份；不重建数据库或清空天气预算／传输队列。
- 用户按同一应用内可信的 `(app_id, user_openid)` 关联，不按昵称合并、不跨应用猜测真人身份；全量／@双投依据QQ消息ID等稳定身份去重，不按相同正文猜测重复。QQ HTTP与本地SQLite无法组成跨系统原子事务，发送成功后本地故障可能留下历史缺口，不能通过自动重发掩盖。

## 变更纪律

- 开始前检查工作区，保留维护者暂存／未暂存／未跟踪改动，不进行无关格式化、回滚、清理或自动提交。
- 文档任务不顺带改代码／依赖／敏感配置；当前实现获明确批准，范围外治理另行确认。
- 改架构／运行／配置／状态同步现行文档，区分当前事实、目标设计与待验证，不依赖历史过程文件维持现行规则。
- 完成说明修改范围、实际验证、限制与下一步；有需求包时附过程记录链接，纯咨询不为满足链接要求额外建档。
