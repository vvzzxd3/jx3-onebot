> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/chat-history/2026-10-13-database-review.md`。

# 单聊／群聊双向记录数据库设计（历史评审稿）

> **2026-10-16修订说明**：下文的授权范围及有限保留方案已被维护者否定；当前支持的聊天消息默认归档并永久保存，见[永久保存修正](<../../../REQ-20261016-permanent-history/legacy/chat-history/2026-10-16-permanent-history.md>)。四表模型与收发事务边界保留。

- 建档／最近更新：2026-10-13（Asia/Shanghai）
- 状态：维护者已批准此设计；实施与最新离线验证见[实施记录](<2026-10-13-implementation.md>)；下方前轮审查／校验叙述保留历史时间语境。
- 本次修订依据：维护者明确单聊外键叫user_id；机器人发送成功后才保存聊天记录，再输出日志；最终取消单聊／群聊独立@标识字段
- 前序：[八表提案摘要](<2026-10-13-data-model-proposal.md>)、[模型／日志澄清](<2026-10-13-model-log-discussion.md>)；总范围：[路线图](<../../../../../../../docs/project/2026-10-10-roadmap.md>)

## 1. 已确认模型取舍

只建4张聊天业务表：

| 表 | 职责 |
| --- | --- |
| `chat_users` | 当前应用的用户基本资料 |
| `chat_groups` | 当前应用的群基本资料 |
| `chat_user_messages` | 按user_id集合的单聊入站／成功出站记录 |
| `chat_group_messages` | 按group_id集合的群聊入站／成功出站记录 |

会话就是记录集合，不建 `chat_user_identities`、`chat_user_sessions`、`chat_group_sessions`，本期不建群成员关系表或任何分类／报表表。群内显示名称按消息快照保存，不推断完整成员名单。

单聊和群聊均不增加独立@标识字段。**机器人聊天记录只在发送成功后插入**；pending、sending、failed、expired、uncertain属于短期传输任务outbox，不作为聊天消息行或历史消息状态。

完整范围是本应用可收到的消息和本应用确认发送的文本／Markdown；不保证平台未投递消息、其他应用私聊、上线前历史、附件文件或未清洗的原始事件完整性。维护者确认无聊天历史需搬迁，不重建共享库或清空其他业务数据。

## 2. 官方文档核查与最终处理

2026-10-13直接读取以下公开官方文档（不是调用QQ业务接口）：

1. [群@机器人消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html)：用户@机器人时触发GROUP_AT_MESSAGE_CREATE；content已去掉@机器人的前缀；mentions列表**不含@机器人自身**。
2. [群消息（全量模式）](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)：开启接收所有消息后，每条群消息都会推送，**不限于@机器人**；各字段含义与专用AT事件一致。
3. [发送群聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)：成功响应包含消息id、timestamp及ext_info；msg_id与固定msg_seq关联一次被动回复。群被动回复5分钟内、最多5次。

官方没有独立at_me返回字段，mentions和已去前缀的正文也不能保证反推出实际@本应用行为。维护者最终决定**取消独立@标识字段**：两张聊天消息表及新归档DTO均不增加at_me、is_at_me或at_me_source，不保留三态／布尔替代列。

event_type仍作为事件来源保存；现有匹配器需要时按官方事件构造运行上下文to_me，不把该上下文另存为聊天字段。全量／专用AT重复投递依然按QQ消息ID去重，具体投递机制留授权联调，不为填一个已取消的字段增加资料API或猜测判断。

## 3. 用户与群

### 3.1 `chat_users`

| 字段 | 类型／约束 | 含义 |
| --- | --- | --- |
| `id` | INTEGER PK | 本应用用户表主键 |
| `app_id` | TEXT NOT NULL | 应用隔离 |
| `user_openid` | TEXT NOT NULL | 接入层归一后的发送人／单聊用户OpenID |
| `display_name` | TEXT NULL | 已有可靠显示名；无值不写“未知用户” |
| `is_bot` | INTEGER NULL，0/1 | 平台机器人标记；缺失时不猜身份 |
| `name_updated_at` | REAL NULL | 防止旧事件覆盖较新名称 |
| `first_seen_at`、`last_seen_at` | REAL NOT NULL | 观察范围，last_seen单调推进 |
| `created_at`、`updated_at` | REAL NOT NULL | 本地维护时间 |

唯一 `(app_id, user_openid)`；另设 `(id, app_id)`唯一键供外键。单聊author.user_openid和群author.member_openid归一定位；当前应用下值相同使用同一用户，不按昵称合并不同值、不跨AppID合并身份。OpenID原样保存以支持业务查询，数据库／备份限制访问，不用HMAC替代业务查找字段。

### 3.2 `chat_groups`

| 字段 | 类型／约束 | 含义 |
| --- | --- | --- |
| `id` | INTEGER PK | 内部群ID |
| `app_id` | TEXT NOT NULL | 应用隔离 |
| `group_openid` | TEXT NOT NULL | 平台群OpenID |
| `name` | TEXT NULL | 有可靠来源才填，当前无名称事件不造群名 |
| `first_seen_at`、`last_seen_at` | REAL NOT NULL | 观察范围 |
| `created_at`、`updated_at` | REAL NOT NULL | 本地维护时间 |

唯一 `(app_id, group_openid)`；另设 `(id, app_id)`唯一键。不为填资料调用额外API，不补造QQ群号或成员名单。

## 4. 两类消息字段

### 4.1 共有字段（不是另建父消息表）

| 字段 | 类型／约束 | 含义 |
| --- | --- | --- |
| `id` | INTEGER PK | 本地消息ID |
| `app_id` | TEXT NOT NULL | 应用隔离 |
| `direction` | TEXT，in/out | 收到外部消息／成功发送本应用消息 |
| `qq_message_id` | TEXT NOT NULL | 入站事件id或成功发送返回id，两方向均已有平台ID |
| `reply_to_id` | INTEGER NULL | 同应用、同用户／群的被回复入站消息 |
| `msg_seq` | INTEGER NULL | 成功出站的固定回复序号；入站为空 |
| `content` | TEXT NOT NULL | 清洗后的实际消息正文，不拼日志前缀 |
| `content_format` | TEXT | text／markdown／non_text必要类型摘要 |
| `sender_name` | TEXT NULL | 事件显示名或本应用名称快照，不用于身份判断 |
| `source` | TEXT | qq_event／bot_http |
| `event_type` | TEXT NULL | 入站事件名称；HTTP出站为空 |
| `message_at` | REAL NULL | 平台消息时间；缺失不编造 |
| `received_at` | REAL NULL | 入站本地收到时间；出站为空 |
| `confirmed_at` | REAL NULL | 出站平台成功确认的本地时间；入站为空 |
| `created_at` | REAL NOT NULL | 本地插入时间，出站在成功后填写 |
| `truncated` | INTEGER，0/1 | 是否截断／省略 |
| `expires_at` | REAL NOT NULL | 当前保留策略的正文到期时间 |
| `deleted_at` | REAL NULL | 正文退休／逻辑删除标记 |

取消聊天行的local_send_key和发送状态字段：只有成功出站才建行，稳定发送键／尝试次数和任务状态留在outbox。正文先安全处理再截断，不保存凭证、完整事件、鉴权ext、附件URL或供应商原始Geo响应。保存实际渲染内容，Markdown格式保留；其他媒体只作类型摘要，不下载附件。

### 4.2 `chat_user_messages`

在共有字段上增加：

| 字段 | 类型／约束 | 含义 |
| --- | --- | --- |
| `user_id` | INTEGER NOT NULL，FK→chat_users.id | 当前单聊记录集合所属用户；in/out均是该用户 |

字段直接叫user_id，不加peer前缀。direction=in时发送者为这个用户，out时发送者为app_id对应的本应用；不额外伪造机器人用户OpenID。没有session_id、identity_id或is_at_me。

### 4.3 `chat_group_messages`

在共有字段上增加：

| 字段 | 类型／约束 | 含义 |
| --- | --- | --- |
| `group_id` | INTEGER NOT NULL，FK→chat_groups.id | 所属群，两方向相同 |
| `sender_user_id` | INTEGER NULL，FK→chat_users.id | 入站必须有外部发送人；本应用出站为空 |

## 5. 约束与索引

- 用户／群关联用 `(对象id, app_id)`复合外键，禁止跨应用关联；默认删除限制，不从用户／群无条件级联毁掉历史。
- QQ消息去重：单聊唯一 `(app_id, user_id, qq_message_id)`；群聊唯一 `(app_id, group_id, qq_message_id)`，不加入direction，以便识别同一成功出站的回投。
- 同一逻辑成功回复可另以入站reply_to_id和固定msg_seq约束，稳定发送任务键仍由outbox持有；不因重试规划新发送序号。
- reply_to指向同集合入站消息，用复合外键及仓储类型校验；删除前清空失效引用，不用复合ON DELETE SET NULL误清app_id／对象id。
- 入站不设msg_seq／confirmed_at；出站有合法固定msg_seq、成功QQ ID和confirmed_at；群入站必须有sender_user_id。
- 用户／群时间线索引分别以 `(app_id, user_id, message_at, id)`、`(app_id, group_id, message_at, id)`为基础，缺平台时间时用明确标识的created_at；分页以稳定id破除同时间并列。期限清理使用expires_at索引。
- 同一QQ消息的全量／AT重复投递只保存一行，依据QQ消息ID和inbox处理状态去重；已接受业务不重复执行，不为去重另建@标记字段。事件类型与运行时匹配上下文按接入层已有规则处理。

## 6. 入站顺序不变

```text
统一解析／校验（不调用匹配器）
  → 同事务：维护用户／群 + 写消息 + 写待处理inbox + 持久水位
  → COMMIT
  → 输出接收聊天日志
  → 匹配器／业务
```

合法授权范围内的普通消息也记录，不以天气匹配决定存储。未授权事件不保存正文，拒绝诊断不含正文。业务TTL到期可以记录历史，但不生成过期回复或主动推送。

当前inbox是已匹配命令队列，批准后需扩展为收到消息的持久处理队列：否则先推进水位、后在内存匹配会漏任务。消息／待处理任务／持久水位同事务，存储失败不跳洞、不执行匹配；心跳最新seq仍与可靠Resume水位分开。日志失败不回滚消息或阻止业务，不承诺崩溃前后日志exactly-once。保持有界背压、场景／对象FIFO和天气预算。

## 7. 机器人发送成功后才存历史

```text
业务返回并渲染回复
  → 仅规划现有outbox任务（不插入聊天消息行）
  → 调用QQ HTTP
  → 收到成功确认与QQ消息ID
  → 同事务：插入成功出站聊天行 + 标记outbox sent
  → COMMIT
  → 输出成功发送聊天日志
```

成功回复归入相应user_id／group_id集合；QQ ID、平台时间及实际发送正文一起保存。sent只表示平台接受，不等于用户已读或最终送达。

- pending／sending／failed／expired／uncertain仅存在outbox／诊断，不出现在两张聊天历史表；无成功确认就不插入历史，不输出成功发送聊天日志。
- 明确POST前未发送，仍可按已有有限预检策略继续同一任务和固定seq；不改变“不确定不自动重发”的约束。
- HTTP成功后本地事务失败：不伪造已归档日志，不再次调用QQ发送；记录存储失败／不确定结果。在当前进程仍持有成功返回值时，可以只重试归档事务，用QQ ID去重。
- 成功返回后、归档提交前崩溃可能留下历史缺口；HTTP与SQLite不能原子提交。重启后sending按既有策略恢复uncertain，不以重新发送补聊天历史；缺少已落盘确认时无法保证自动补档。
- outbox保留短期发送任务的必要payload与状态，不把它当长期聊天表，也不让清理任务级联删除成功历史。
- 本应用发送回投能以QQ ID关联时合并已有出站，不触发业务；不能可靠对应时不按正文猜关联。未归档成功时与回投之间的先后竞态需离线测试及授权联调验证。

## 8. 模块、保留及迁移边界

接入层负责QQ消息解析／成功响应验证，聊天模块拟归 `src/jx3_bot/modules/chat_history/`，模型及repository纵向聚合，复用共享Store事务。日志仅消费规范化／已提交记录；匹配器在入站落库和日志后使用消息，已识别机器人和本应用出站不执行业务。

正文默认30天、必要去重默认90天已作为实施默认值，仍需在授权部署前确认实际策略。到期／删除清空正文及昵称快照，保留有界去重并防止延长保留期后旧数据复活；活跃传输任务最低状态不因聊天清理重置。SQLite WAL／备份需独立治理，逻辑删除不是安全擦除。

不搬迁旧聊天内容、不删库或更换路径。批准后替换旧聊天schema／接入依赖，保留天气usage、cities、preferences、cache和gateway元数据；现存inbox/outbox任务、固定seq和预算保守迁移，不将“聊天无历史”视为清空传输队列授权。

## 9. 已核对现存代码与批准后验收

前轮核对[QQ解析](<../../../../../../../src/jx3_bot/integrations/qq/events.py>)、[日志](<../../../../../../../src/jx3_bot/observability.py>)、[Gateway](<../../../../../../../src/jx3_bot/gateway.py>)、[Store](<../../../../../../../src/jx3_bot/store.py>)、[Application](<../../../../../../../src/jx3_bot/application.py>)和[QQ HTTP](<../../../../../../../src/jx3_bot/qq_http.py>)：当前接收日志／匹配在接受事务前；发送逻辑丢弃HTTP回复结果；QQHTTP.reply已有含id的返回字典。新设计不是当前已实现链路。

批准后验收：4表外键／索引与user_id；两类消息均无独立@字段及全量／AT去重；解析→提交→日志→匹配顺序；待处理任务恢复；成功HTTP后插入历史与outbox sent同事务；失败／过期／不确定不建历史；成功后本地提交失败不重发；Markdown正文和成功QQ ID可查；预算／清理／迁移不改其他模块数据。发送回投及事件实际覆盖留授权联调，不编造平台保证。

## 10. 文档过程与实际验证

前轮按维护者要求删除旧专项需求，修改／新增21份文本文件；旧文档仅留撤销说明／必要聊天事实，天气预算及独立长期替代目标保留。前轮实际检查32份Markdown和493个本地链接、格式与撤销要求残留，不能把前轮检查算成本次修订或业务测试。

本次核对官方公开文档并修订单聊user_id、取消两类消息独立@字段和成功后落库流程，共修改7份Markdown，同步根说明／开发约定、索引、路线图及两份旧澄清摘要。搜索工具缺少凭据，采用直接读取官方页面核实；未请求真实QQ Token、连接WSS、发送QQ消息或调用天气数据接口。

**批准前的当轮记录**：实际校验7份修订Markdown及187处本地链接，格式／围栏、user_id命名、无独立@字段、成功→归档提交→发送日志顺序检查通过；`git diff --check -- README.md AGENTS.md docs`通过。当时未执行业务测试、数据库DDL、运行配置修改或代码开发；当时完整设计仍待批准。**此后维护者已批准并进入实施**，新结果单独记于[实施记录](<2026-10-13-implementation.md>)；平台事件覆盖依旧待授权联调。

## 变更历史

| 日期 | 变化 | 依据 |
| --- | --- | --- |
| 2026-10-13 | 撤销八表与独立会话／身份容器；提交4表、双向记录及存储后日志／匹配设计 | 维护者明确会话是记录集合，要求先评审 |
| 2026-10-13 | 将单聊外键命名为user_id；最终取消两类消息独立@字段；撤回发送前预建聊天行，改为成功后归档再日志 | 维护者最新决定及本次官方文档核查 |
| 2026-10-14 | 标明后续批准及实施记录；原有批准前检查不冒称为本轮业务验证 | 维护者明确同意开发 |
