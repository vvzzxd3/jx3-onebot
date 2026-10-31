> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/qq-integration/2026-10-12-websocket-proxy-importerror.md`。

# WebSocket 自动代理触发 ImportError 的启动排查

- 建档／最近更新：2026-10-12（Asia/Shanghai）
- 状态：直连修复及138项完整离线验证完成；真实 WebSocket 联调未执行。
- 来源：维护者提供16:00:12～16:00:13启动日志，先要求分析，随后明确“禁用代理，直连QQ”。排查阶段未改代码；本次仅修复默认连接器并补离线回归，不改AppID绑定／数据库／真实配置或系统代理，不启动真实服务。

## 1. 日志与当前事实

维护者日志显示 application.start、QQ Token获取／刷新、gateway发现均成功，随后 application.error 的 error_type 为 ImportError，应用停止。没有出现 qq.gateway.connected 或 Hello／Identify 日志。

这与先前AppID绑定冲突是不同故障：本次已越过 [启动绑定检查](<../../../../../../../src/jx3_bot/__main__.py#L51-L64>)。由于日志隐藏外部异常正文，日志本身不能给出完整ImportError来源；结论需由现有依赖和实际初始化路径复现支持。

## 2. 修复前的代码与依赖依据

- [默认连接器](<../../../../../../../src/jx3_bot/gateway.py#L23-L32>)调用 websockets.asyncio.client.connect，没有显式代理参数。
- [网关主循环](<../../../../../../../src/jx3_bot/gateway.py>)在获取WSS地址后进入该连接器；ImportError不在可重连异常名单内，所以冒泡导致停止，而不是无限重试配置错误。
- [依赖配置](<../../../../../../../pyproject.toml#L7-L13>)允许websockets14～15；[锁文件](<../../../../../../../uv.lock#L197-L200>)及当前独立环境实际版本均为15.0.1，没有安装python-socks。
- websockets15默认自动读取系统／环境代理；WSS优先选择专用wss代理，其次SOCKS，再HTTPS。macOS可能将系统SOCKS代理交给urllib为http方案，websockets会将其规范化为socks5h，不应据此误判只配置了HTTP代理。
- 当前本机代理检测存在http／https／socks类型；虚构WSS地址实际选中socks5h。没有输出代理地址、端口或认证信息。
- HTTP链路的 [httpx客户端](<../../../../../../../src/jx3_bot/__main__.py#L62-L64>)显式trust_env=False；WebSocket链路却自动发现代理，导致两类连接代理策略不一致。

## 3. 已执行的离线复现

使用现有独立环境、真实Gateway默认连接器，但将目标设为虚构保留域名wss://offline-gateway.invalid/，不加载专用环境配置，不访问数据库，不使用AppID／Token。Python审计钩子阻止socket.connect／getaddrinfo／sendto。

实际结果：

```text
websockets_version: 15.0.1
python_socks_installed: False
selected_proxy_scheme: socks5h
reproduced_exception: ImportError
safe_library_message: python-socks is required to use a SOCKS proxy
network_operations_attempted: 0
offline reproduction exit code: 0
```

依赖在真正连接代理前就抛出错误，解释了Token和gateway HTTP成功、随后尚无WebSocket连接事件就退出的日志顺序。此结论来自当前环境复现，不冒称取得维护者历史进程的完整异常栈；不同终端若有显式代理／绕过变量差异，最终选路可能不同。

## 4. 排查阶段的修复建议（历史）

1. 若期望直连：对当前websockets15连接显式设置proxy=None，使WSS不自动读取系统代理，与现有HTTP策略一致；正式修复需同时处理依赖允许14的问题（14没有相同proxy接口），并补无外发代理回归。直连配置正确不等于QQ WSS网络连通已经验证。
2. 若明确需要SOCKS：通过uv项目依赖纳入python-socks的asyncio支持，更新uv.lock并验证；仅通过uv管理／安装依赖，不使用pip安装。同时明确HTTP／WSS的代理策略，不以自动发现作为隐含约定。
3. 对已知代理依赖缺失增加固定安全错误提示，避免只有ImportError；不全面打印外部异常正文／代理URI／Token。

上述排查阶段未修改源代码、依赖或实际代理设置，未连接真实QQ／天气接口，未改变AppID绑定和多实例待办。当时未重跑完整134项测试，既有结果不作为联网验证；只执行记录及索引链接／格式检查。

## 5. 已授权的直连修复

维护者选择直连。保留依赖范围与现有锁文件，不新增python-socks；默认连接器识别connect是否公开proxy参数：websockets15显式传proxy=None，14没有该参数且原本没有自动代理功能，因此不传未知参数。原生心跳禁用、1MiB消息上限、15秒握手超时及注入连接器接口保持不变。

[连接器实现](<../../../../../../../src/jx3_bot/gateway.py>)增加公开参数检查；新增[4项连接器回归](<../../../../../../../tests/test_gateway.py>)覆盖15参数、模拟14签名、注入连接器不变，以及实际安装版本绕过系统／环境SOCKS代理并进入被拦截的直连transport。后者将代理发现和SOCKS函数设为失败哨兵，将事件循环create_connection替换成AsyncMock，确认目标仍为虚构WSS域名／443、TLS与server_hostname保留；没有真实DNS／TCP／TLS／QQ鉴权。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-official-uv/bin/python -m unittest discover -s tests -p test_gateway.py -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-official-uv/bin/python -m unittest discover -s tests -q
```

专项12项全部通过（0.168秒），完整**138项全部通过（0.983秒）**。实际安装版本为15.0.1；14仅验证模拟公开签名的参数兼容，没有另装14版本环境，不冒称实际14完整套件已执行。compileall、离线CLI帮助、4份相关Markdown／152处本地链接、源码／测试AST与行尾格式、限定git diff --check均通过。未更新依赖、锁文件或导出清单，无需另装SOCKS支持。

README／部署说明同步应用层HTTP与WSS均不自动使用代理；AppID绑定、inbox/outbox、预算和真实配置不变。系统VPN／TUN或网络路由不属于本次应用层代理配置，修复不证明实际QQ WSS网络可达。
