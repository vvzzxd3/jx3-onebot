> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/qq-integration/2026-10-10-implementation.md`。

# QQ 官方独立接入实施与离线验证

- 任务建档：2026-10-10；最近更新：2026-10-13（Asia/Shanghai）
- 状态：首版代码与离线故障验证完成；真实平台联调、部署与正式切换未执行。
- 需求／批准：维护者回复“按此方案开始实现”；[协议方案](<2026-10-10-websocket-plan.md>)、[总记录](<../project/2026-10-10-official-implementation.md>)。

> 后续更新：2026-10-11新增[通用匹配器／本地结果模板／C2C天气](<../../../REQ-20261011-matcher-templates/legacy/qq-integration/2026-10-11-matcher-templates.md>)，完整124项离线测试通过。下文群纯文本首版说明保留为历史；当前支持群与QQ单聊、文本／自定义Markdown，保守300秒窗口与固定seq／uncertain策略不变。

## 1. 实际实现

[QQ HTTP与Token](<../../../../../../../src/jx3_bot/qq_http.py>)、[WebSocket](<../../../../../../../src/jx3_bot/gateway.py>)、[应用生命周期／worker](<../../../../../../../src/jx3_bot/application.py>)、[可靠存储](<../../../../../../../src/jx3_bot/store.py>)、[命令规范化](<../../../../../../../src/jx3_bot/models.py>) 与 [独立入口](<../../../../../../../src/jx3_bot/__main__.py>) 组成单进程asyncio服务；依赖由[项目配置](<../../../../../../../pyproject.toml>)和[uv锁文件](<../../../../../../../uv.lock>)管理，不含NoneBot／OneBot。当时生成的兼容清单已按后续决定移除，见[uv专用依赖管理记录](<../../../REQ-20261016-uv-only/legacy/project/2026-10-16-uv-only-dependency-management.md>)。没有通用适配器、多平台、Webhook并行入口或旧插件加载。

运行指引见 [部署与切换](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>)。保留 旧入口（历史路径 `bot.py`，已清理）、旧依赖（历史路径 `requirements.txt`，已清理）、[旧元数据](<../../../../../../../pyproject.toml>)及维护者已有暂存／未暂存内容，不自动切换现有服务。

## 2. 协议与可靠性决策

- QQ Token独立管理、单飞刷新、安全缓存到期；鉴权`QQBot token`。HTTP200也检查错误字段，不输出Token。POST401只使后续消息刷新，不自动重发当前消息。
- HTTPS动态`/gateway/bot`，仅单分片shard=[0,1]；新Identify检查启动余量，有效Resume（包括seq=0）不被Identify余量为0阻断。
- Hello op10的毫秒间隔；op2 Identify使用群Intent `1<<25`；心跳op1携带最新接收s，op11仅清ACK状态，响应平台op1，不向ACK再回ACK。
- 接收／控制帧、独立心跳、顺序可靠落盘三个协程分离，慢天气或慢存储不堵塞控制帧读取；接收队列最大64项，仅保留规范化必要命令／会话元数据，不排队原始聊天／附件auth URL。
- READY保存session与水位；Resume op6只用**有序可靠接收前缀**，不同于心跳最新接收seq。命令inbox与水位同事务，忽略事件明确推进水位但不存原文；落盘失败不跳过缺口，不用max(s)冒充可靠顺序。
- 全量与@群消息按AppID／group_openid／d.id跨类型去重；普通聊天、机器人标记、缺身份、无效／非有限时间、超龄事件不触发天气或入库。被动窗口按原始timestamp，不按重投到达时间重置。
- 每个逻辑回复固定msg_seq=1，先持久化outbox；HTTP成功且返回非空消息ID才标sent；200缺失ID也标uncertain，不误报送达。只有Token预检失败且消息请求尚未开始才最多三次同序号尝试；其他不确定发送保留uncertain，重启sending恢复uncertain，不自动换序号重发。明确4xx失败不冒称成功。
- 同一App／群／用户的命令按入站順序认领，避免默认双worker乱序设置／删除／读取偏好；不同用户可并行。
- CLI canonicalize数据库锁路径；符号链接不可绕锁，硬链接数据库拒绝。单实例内recover恢复working待处理，数据库绑定AppID与计数时区；没有分布式多实例承诺。

## 3. 有界重连与清会话

采用 [方案中的策略](<2026-10-10-websocket-plan.md>)，**这里只证明程序的离线分支，未声称当前QQ版本真实关闭码行为已联调**：

| 情况 | 首版策略 |
| --- | --- |
| 普通网络断开／op7／ACK逾期 | 退避，有有效会话优先Resume |
| op9 | 不套用Discord布尔语义；先排空可靠接收，再清会话、退避后新Identify |
| 4006／4007、4900–4913 | 先排空收到的队列，再清会话，以新Identify恢复 |
| 4008／4009 | 退避、保留可用Resume |
| 4001／4002、4010–4014、4914／4915 | 停止无意义重试 |
| 未识别关闭码 | 停止，人工核实版本／文档 |

清会话前按顺序落盘已收到的有界队列，最多等待15秒；失败停止服务，不直接清水位跳过命令。旧连接正在执行的落盘在取消时等待其结束，避免旧READY与新连接竞态。连续10次短连接失败停止；连接持续60秒后重置失败计数。启动额度耗尽时不无限Identify。

## 4. 已执行验证与审查修复

完整套件**60项离线通过**（Python3.12.7）；[WebSocket测试](<../../../../../../../tests/test_gateway.py>)、[HTTP／应用测试](<../../../../../../../tests/test_qq_application.py>)、[回归测试](<../../../../../../../tests/test_regressions.py>)。

覆盖Identify、Resume seq=0、毫秒心跳、服务端心跳请求／ACK、慢存储下ACK独立、ACK逾期、op7／op9／关闭策略、双事件去重、未知聊天／附件信息不落盘、落盘失败水位不跳过、op9／新会话关闭前排空、发送超时不重发、原始窗口到期、单实例符号链接／硬链接、防跨App数据库及完整合成天气回复链。

只读实现审查在45项初始测试通过后另用合成条件复现6项遗漏：畸形天气归因崩溃、锁路径别名、清会话丢排队事件、Token预检误标不确定、双worker城市偏好乱序、POST401不刷新后续Token。主代理均已修复并追加回归；当前60项通过。测试Socket为Fake队列，HTTP为MockTransport，不创建真实服务器或连接官方WSS。

执行命令：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m jx3_bot --help
PYTHONPATH=src .venv/bin/python -m compileall -q src/jx3_bot tests
```

语法与CLI帮助均通过；临时生成Ed25519密钥签名／验签通过，cryptography48.0.1已装入测试虚拟环境；未读取旧凭证。

## 5. 风险／下一步

- 当前没有获取真实QQ Token、连接真实网关或发送群消息；平台权限／全量开关／d.content实际形态／seq与关闭码语义／被动回复成功率待授权群验证。
- 只有公网IPv6不证明纯IPv6出站可用；实际DNS／TLS／WSS与帐号白名单需部署环境确认，不擅自更改账号设置。
- 当前为单分片／单实例、5分钟被动回复首版，不实现主动推送、历史消息或全部旧插件迁移。
- 进程告警、日志轮转、CI和生产资源监控待运维补齐；不将无限自动重启视为可靠性保障。
- 新链路试运行后由维护者切换；移除旧框架依赖与个人号运行路径另行验收，避免新旧同时回复。
