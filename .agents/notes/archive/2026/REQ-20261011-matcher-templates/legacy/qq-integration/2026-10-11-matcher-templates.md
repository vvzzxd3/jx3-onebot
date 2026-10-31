> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/qq-integration/2026-10-11-matcher-templates.md`。

# 通用匹配器、结果模板与单聊天气

- 建档／最近更新：2026-10-11（Asia/Shanghai）
- 状态：本轮代码与124项完整离线测试完成；真实QQ／天气联调、现有进程重启与正式切换未执行。
- 需求来源：维护者要求每类功能结果模板化、美化输出并核实官方格式；参考NoneBot2完成群聊／单聊输入匹配及功能触发。
- 跨模块记录：[总记录](<../project/2026-10-11-templates-matcher.md>)；天气变化见[天气实施记录](<../../../REQ-20261010-official-weather/legacy/weather/2026-10-10-implementation.md>)；部署见[指南](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>)。

> 2026-10-12 结构更新：天气模型／客户端／业务／仓储／命令／模板已聚合到业务目录，共享与QQ模型分开；原平铺路径为兼容入口，见[模块聚合记录](<../../../REQ-20261012-weather-modules/legacy/weather/2026-10-12-module-aggregation.md>)。下文124项为本阶段历史结果；本次结构调整完整134项通过，真实联调仍未执行。

## 1. 官方消息格式：本轮核实

核实日期2026-10-11。优先使用具体接口的新schema，而非滞后的SDK注释或早期消息类型页脚：

| 形式 | 群聊 | QQ单聊 | 本轮实现 |
| --- | --- | --- | --- |
| 纯文本 `msg_type=0` | `content` | `content` | 默认；标题、字段、分隔线、来源统一排版 |
| Markdown `msg_type=2` | `markdown.content` | `markdown.content` | 可配置；标题／正文／引用页脚，动态内容转义 |
| 富媒体 `msg_type=7` | `media.file_info` | 同左，必须来自单聊上传 | 本轮只调研，未实现图片／视频／语音／文件上传 |
| 输入中 `msg_type=6` | 未列为群支持 | `input_notify`，最多60秒 | 未实现 |
| 按钮 `keyboard` | 消息附加字段 | 消息附加字段 | 本轮未添加按钮或后台互动处理 |
| ARK／Embed／旧图文枚举 | 新具体接口未列为可发送 | 同左 | 不据旧SDK枚举承诺支持 |

依据：[群发送](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)、[单聊发送](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)、[富媒体](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/rich-media.html)。`msg_type=2`不混发顶层content／ark；代码只生成明确的0／2两种负载。

[Markdown专页](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html)标明2026-04-23已向所有机器人开放群／单聊自定义Markdown；[2026-10-09更新的官方FAQ](https://bot.q.qq.com/wiki/bot_FAQ/)同时确认Markdown和消息按钮开放，无需单独申请模板。**这是公开产品能力，不是本应用或目标群已联调成功的证明。**频道仍有不同约束，本项目不实现频道。

新群／单聊schema把`template_id`／`custom_template_id`标为废弃；Markdown专页还保留旧`custom_template_id+params`示例及旧模板权限错误。不能宣称所有旧模板已停用；本轮直接采用自定义正文，不依赖其兼容性。“本地结果模板”与“旧官方模板ID”不是同一概念。

按钮可作后续优化，但回调按钮需额外`INTERACTION_CREATE`／`1<<26`、限定时间ACK及客户端行为验证。本轮不增加interaction订阅或任意外链图片，避免把输出美化扩大成新互动协议。

## 2. 模板与格式分层

- [本地天气模板](<../../../../../../../src/jx3_bot/modules/weather/templates/weather.toml>)集中19类结果：预报、额度、城市保存／删除／查看／未设置、缺查询、输入无效、本地／Geo消歧、城市无结果、预算用尽、暂无预报、鉴权异常、限流、不可用、过期、路由变更、输出超长。
- [渲染层](<../../../../../../../src/jx3_bot/replies.py>)提供`Reply(title, body, footer)`、`render_result`和`message_payload`。每个新业务将TOML放在自己模块的 templates 子目录，通过`module`选择；现有示例见[天气模板目录](<../../../../../../../src/jx3_bot/modules/weather/templates/>)。模板只支持普通命名占位符，不支持表达式、属性访问、格式化代码或执行逻辑。
- [天气业务](<../../../../../../../src/jx3_bot/weather.py>)返回结构化结果；保留`handle()`／`format_forecast()`文本接口供旧调用者，CLI注册的是`handle_result()`。
- `QQ_REPLY_FORMAT=text`为默认，已有专用配置缺少该项也采用美化文本。想使用富格式，在本机已有专用配置中单独增加`QQ_REPLY_FORMAT=markdown`，然后重启同一官方进程。**不要覆盖实际配置或发送凭据。**本轮只更新[无密钥示例](<../../../../../../../.env.production.example>)，未读取或改实际配置。
- 模板修改在重启后生效；模板按模块有界缓存。CLI的`check-config`与`run`在业务联网前检查天气模板，新增模块须在启动组装时显式调用`validate_templates(module)`。
- Markdown标题和正文统一转义，用户输入不变成链接／图片／HTML／提及。预报保留来源URL、更新时间、本地缓存标记；Geo仍仅显示用户输入和“查询地点当地时间”，不存供应商返回命名时区。

发送前的保守长度上限1800字符：Markdown转义后过长但文本仍合格时，在**进入POST前**固定选择文本；多个handler合并后的文本也超长时，返回单条友好提示，不截断、不拆多条、不使整个服务退出。QQ拒绝／不确定POST后**不自动换格式重发**。

## 3. 通用匹配器与NoneBot借鉴边界

实现：[匹配注册器](<../../../../../../../src/jx3_bot/matching.py>)、[通用消息模型](<../../../../../../../src/jx3_bot/models.py>)、[启动组装](<../../../../../../../src/jx3_bot/__main__.py>)。

支持：

- `on_command`：严格关键字＋空白参数、别名；当前天气没有`/`前缀，不误匹配“今天天气很好”／“天气北京”。
- `on_fullmatch`：精确文本或多个备选文本。
- `on_regex`：正则search，想限制开头／全文请使用锚点；捕获到的完整匹配文本成为argument。
- `on_message`：显式、可限定会话的catch-all；**默认未注册**，不得为了迁移框架就启用全聊天存储。
- `scenes={"group", "c2c"}`、纯同步`rule(context)`、`to_me`、argument validator、稳定matcher名称。
- `priority`小值优先，同级按注册顺序执行；`block=True`阻断后续更低优先级，不阻断同级，最多8个匹配。多结果合并成一次被动回复，固定`msg_seq=1`；handler返回None不生成outbox。

参考：[NoneBot事件响应器进阶](https://nonebot.dev/docs/advanced/matcher)。不重新依赖NoneBot／OneBot，也不承诺其依赖注入、临时会话、got／reject、多适配器或所有默认语义兼容。规则／正则是可信开发配置，必须快速、纯计算；网络查询放async handler。不要运行用户提供的任意正则。

### 接入一个新功能（仅示例，未新增剑网3业务）

在CLI构造好共享registry、启动Gateway之前注册：

```python
from jx3_bot.matching import to_me
from jx3_bot.replies import Reply

matcher = registry.on_command(
    "jx3.raid", "副本", aliases=("raid",),
    priority=10, block=True, scenes={"group", "c2c"},
    rule=to_me, validator=lambda arg: len(arg) <= 80,
)

@matcher.handle
async def raid_handler(message):
    # message.command.argument是已匹配参数；查询业务在此执行。
    return Reply("副本查询", "示例结果，不代表已实现游戏查询", "数据来源／更新时间")
```

实际业务推荐独立模板，通过`render_result("result", module="jx3", ...)`渲染；新增模板模块名只允许小写字母、数字、下划线，不能传文件路径。handler名称升级需保持稳定；旧inbox无matcher字段的天气命令有兼容路由，已删除路由返回友好提示。handler副作用应幂等；inbox recover不提供外部业务exactly-once事务保证。

## 4. 群与单聊规范化及持久化

[群@事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_at_message_create.html)、[全量群事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)、[单聊事件](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/c2c_message_create.html)均使用`GROUP_AND_C2C_EVENT=1<<25`，本轮无需改变既有intent。群全量仍需群主设置允许；特殊事件订阅是否具备权限仍须应用验证。

- 群用`group_openid`和`author.member_openid`；C2C用`author.user_openid`作为会话和回复目标。群／单聊OpenID不默认合并为同一人。
- 官方群@事件明确`content`已去机器人@前缀，因此不泛化删除任意提及，trim后统一匹配。C2C和群@的`to_me=True`，普通全量群消息为False；内置天气不要求to_me，能否无@收到依赖真实群权限。
- 通用`IncomingMessage`保留旧`GroupMessage`导入兼容；scene明确为group／c2c。队列只保存必要身份、原始时间、稳定matcher名称与已校验参数，不存正文、附件、鉴权数组或完整原始事件。
- [SQLite](<../../../../../../../src/jx3_bot/store.py>)将preferences／inbox／outbox主键加入scene，同一组表在一个事务中复制、换表；旧群数据标group，兼容更早无attempts的表，按原rowid保留FIFO。基础缺表初始化独立于三表换表事务，不承诺任意损坏schema修复。
- 迁移不删除月用量、城市、缓存、AppID／时区绑定或gateway水位；旧pending、seq、attempts、uncertain完整保留。旧文本outbox维持原格式，新Markdown负载在plan_reply时固定，重启／配置改变／再次plan_reply不覆盖。
- 去重按AppID／scene／会话／d.id，群@和全量仍去重；FIFO再加发送人。群与单聊偏好分离，用户不能通过单聊删除本群默认城市。
- outbox正文与Markdown payload过期同时清除。只有确定POST未开始的token预检失败可有限重试；预检结束再复核expires，过期不POST并立即清正文。真正发送不确定不重发。

单聊文档页头写60分钟／4次，msg_id字段仍写5分钟；本轮保持**原始消息时间＋300秒、每请求一条回复**的保守策略，不声称更长窗口已验证。

## 5. 实际验证、风险与下一步

[专项测试](<../../../../../../../tests/test_matching_replies.py>)新增28项，覆盖匹配类型／别名／规则／优先级／阻断、19类结果两种格式、模块模板与路径安全、群C2C隔离、全链路、偏好、旧库迁移／中途失败／重启、固定payload、Geo隐私、拒绝Markdown不重发、预检过期与多结果超长。

最终完整124项通过（0.856秒）；compileall、CLI帮助与13项环境依赖一致性检查通过。原96项中的单聊普通聊天日志断言已按新范围更新：现在command_not_matched，不是unsupported_event。测试只用FakeSocket／MockTransport／临时SQLite／生成测试密钥。

只读审查复现并修复三项：预检跨期限仍POST、旧attempts ALTER在迁移外残留、多handler超长会停止服务；均有回归验证。未启动真实服务、未请求实际QQ Token／WSS／天气数据、未改旧业务／实际配置／私钥／生产数据库，未自动提交。

下一步由维护者停官方旧进程、备份同数据库，再升级重启；在授权群与QQ单聊分别测试命令、群@／无需@、默认城市作用域、Markdown实际客户端展示、真实预报来源和拒绝发送分类。未通过真实联调前不清旧入口或把代码完成写成上线。
