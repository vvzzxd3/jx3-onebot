> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/qq-integration/2026-10-10-websocket-plan.md`。

# QQ 官方 WebSocket 独立接入层方案

- 建档：2026-10-10；最近更新：2026-10-13（Asia/Shanghai）
- 状态：协议设计记录；用户已批准实施，首版代码与离线验证完成，详见 [实现记录](<2026-10-10-implementation.md>)／[部署指南](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>)。下文实施前事实和建议为历史方案，不表示现在没有代码；真实联调／正式切换仍未执行。
- 需求来源：维护者只有公网 IPv6，优先 WebSocket，接入方式作为独立层级便于维护替换；群主可开启无需 @ 的全量群消息。
- 总记录：[WebSocket 与天气首功能](<../project/2026-10-10-websocket-weather.md>)；首业务：[和风天气方案](<../weather/2026-10-10-qweather-plan.md>)。
- 前序：[架构迁移方案](<2026-10-10-architecture-migration.md>)（保留历史建议，Webhook 优先建议由本记录替代）。

## 1. 当前事实与目标

旧入口（历史路径 `bot.py`，已清理） 仍是 NoneBot2／OneBot，没有官方 WebSocket 实现。当前仅确认技术方向和平台支持；本项目的 AppID、权限、群主开关与网络实际行为未验证。

目标：通过官方 HTTPS／WSS 主动出站连接实现群天气命令与回复，接入状态机独立于天气业务。无需 Webhook 服务、公开 HTTP 监听或回调验签。Python 异步服务运行框架的最终细节将在实现时确定。

非目标：通用多平台或 OneBot 协议桥接；同时开发 Webhook；默认保存全量群消息；迁移所有实验插件；未经授权联调。

## 2. 接入分层与职责

```text
应用装配与生命周期（asyncio）
  ├─ QQ access_token 管理 + 官方 HTTP 客户端／回复
  ├─ QQ 事件接入边界
  │    └─ WebSocket 实现：网关、登录、心跳、重连、Resume
  └─ 最小事件入队／去重 → 命令路由 → 天气服务 → QQ 回复
```

- 事件接入边界只定义本项目所需的事件投递与停止／运行生命周期；WebSocket 连接细节、opcodes、seq、session 都限制在 QQ 接入模块。
- 业务接收规范化群消息上下文，不持有 socket，不引用 NoneBot／OneBot。回复经 QQ 官方 HTTP 发送，与入站传输方式分离。
- 后续若改用 Webhook，只新增相同事件投递边界的具体入口；不把全部旧插件规范重新实现，也不预建通用平台适配层。
- 不引入 FastAPI／Uvicorn 作为仅 WebSocket 出站服务的必需依赖；天气模块和存储不应依赖接入实现。

## 3. 当日官方资料核实与实现约束

| 环节 | 官方协议／约束 | 实现要求 |
| --- | --- | --- |
| Token | `POST https://api.bot.qq.com/app/getAppAccessToken`，`appId`、`clientSecret`；Header 为 `QQBot {access_token}` | 按 body.code／HTTP 判断，expires_in 兼容数值或数值字符串；单一刷新管理器、定时刷新、避免日志泄露 |
| Token 刷新 | 有效期内获取同值，过期前 60 秒内可取新值 | 用实际到期时间调度，不无限提前重复刷新；下次 Identify／Resume 用新 Token，当前 socket 不盲目重复 Identify |
| 网关 | `GET /gateway` 或 `/gateway/bot`，返回 WSS url；后者还有分片和会话启动预算 | 不硬编码示例 WSS url；单连接初期 shard=[0,1]，检查启动额度与 max_concurrency |
| Hello／Identify | op10 带 heartbeat_interval（毫秒）；op2 为 `QQBot token`、intents、shard、properties | 首期 intents=1<<25，区分群与频道事件，不使用旧 Bot appid.app_token 鉴权 |
| 心跳 | op1 携带最新接收 s 或 null；op11 是 ACK | 心跳不受天气慢调用阻塞；处理平台 op1 请求与 ACK 超时，不向 ACK 再回 ACK |
| Resume | op6 携带 Token、session_id、seq；恢复完成 RESUMED | 持久化可靠接收水位与心跳接收水位区分；业务队列可靠接收后才推进 Resume 水位 |
| 群事件 | GROUP_MESSAGE_CREATE 和 GROUP_AT_MESSAGE_CREATE 均为 GROUP_AND_C2C_EVENT (1<<25) | 支持两者，用消息身份跨事件去重；处理普通无 @ 天气命令，普通聊天不入库 |
| 回复 | `POST /v2/groups/{group_openid}/messages`，msg_type=0、content、msg_id=d.id、msg_seq | 同一逻辑回复序号固定；5 分钟／5 次预算，过期不静默转主动推送 |
| 群开关 | 群主可开启接收所有消息；允许主动发言是另一个权限 | 不能将全量接收等同主动推送授权；本功能使用收到命令的被动回复 |
| 网络与后台 | WebSocket 不依赖公网服务；后台切换立即生效 | 正式切换先停旧发送范围；只有公网 IPv6 不是兼容性证明，需验证实际出站 |

资料来源（核实日期 2026-10-10）：

- [WebSocket 协议](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/websocket.html)
- [事件 Payload](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/payload.html)
- [Access Token](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/access-token.html)
- [获取网关](https://bot.q.qq.com/wiki/develop/api-v2/openapi/wss/url_get.html)、[分片网关及启动预算](https://bot.q.qq.com/wiki/develop/api-v2/openapi/wss/shard_url_get.html)
- [全量群事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)、[群 @ 事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html)
- [发送群消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)
- [官方 FAQ](https://bot.q.qq.com/wiki/bot_FAQ/)、[介绍指南](https://bot.q.qq.com/wiki/bot_new_product-intro/)

### 关闭与重连策略建议

- 网络断开／op7：有有效会话时优先 Resume，失败再按协议回退。
- 4006／4007：清无效 session／seq，受启动频控重新 Identify。
- 4008／4009：有界退避，优先可用 Resume。
- 4900–4913：按详细表重新 Identify，不错误地永久循环 Resume。
- 协议／分片／Intent 权限错误 4001、4002、4010–4014 和状态错误 4914／4915：停止无意义自动重试并告警，不依据同页简化建议无限 Identify。
- ACK 超时阈值、op9.d 语义、Resume 保留窗口、刷新后长连接行为未在本次资料中完整定义，须测试并避免照搬其他平台常量。
- 初版单发送／接收实例；多实例之前先设计共享去重与会话启动调度。

## 4. 执行与验收步骤

1. 天气预算只计本模块自身自然月外发，默认50,000且可配置，不处理帐号其他项目或初始帐号用量。代理已拟定官方城市列表本地优先／GeoAPI即时兜底，待维护者批准 [最新确认稿](<../weather/2026-10-10-confirmation-plan.md>)；之后明确配置与最小事件接口即可离线实施。
2. 实现 Token／HTTP 客户端、WebSocket 状态机和持久化去重队列，使用合成 payload 验证 Identify／Resume、心跳、关闭码及重投。
3. 实现天气命令，覆盖无 @ 和 @ 事件双推、消息为空、机器人自循环、天气慢调用与断连恢复。
4. 经维护者授权后在部署环境检查 API／返回 WSS 域名的 DNS／TLS／网络可达，再用测试群验证全量开关和官方回复。
5. 试运行通过后切换；上线前保留旧代码但旧发送范围不得重叠。最终依赖清理与所有功能替代另行验收。

## 5. 风险与待确认

- 单有公网 IPv6 不代表无法 IPv4 出站；若纯 IPv6 出站，官方并未在本次文档中保证 AAAA／WSS 可用。真实网络可能需要 IPv4 出站或 NAT64，不能先承诺连接成功。
- 当前后台文档不应再默认旧沙箱；帐号是新版或历史应用、下架状态与测试范围需后台确认。
- 官方介绍的 IP 白名单只支持单个 IPv4；不将 IPv6 直接填入白名单，也不擅自替用户更改安全设置。
- 用户已确认平台群主开关，不代表本项目已收到真实全量事件；本任务没有采集数据或上线。
- 官方消息中可能携带 auth_token、身份与附件鉴权 URL；只保存命令所需短期数据，不记录整个 payload。

## 6. 实施与验证记录

本次只新增方案并链接需求总记录，未修改运行代码。已检查现有环境及公开官方资料，未连接网关、请求 Token、发送消息或执行实现测试。模块联调与部署测试待后续明确授权。

## 7. 变更历史

| 日期 | 变化 | 依据 |
| --- | --- | --- |
| 2026-10-10 | 将首选由 Webhook 改为独立 WebSocket 接入层，确认全量群命令方向 | 维护者网络条件与明确要求、当前官方资料 |
| 2026-10-10 | 同步天气月度预算已确认与城市库评估已完成；接入实现仍待后续阶段 | 维护者更正额度周期，并明确选择先评估本地城市库 |
