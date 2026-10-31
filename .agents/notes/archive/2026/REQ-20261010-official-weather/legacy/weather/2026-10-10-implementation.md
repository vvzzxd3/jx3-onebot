> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/weather/2026-10-10-implementation.md`。

# 天气首功能实施与验证记录

- 任务建档：2026-10-10；最近更新：2026-10-12（Asia/Shanghai）
- 状态：首版代码及离线验证完成；真实天气接口／QQ群联调尚未执行。
- 批准依据：维护者回复“按此方案开始实现”，采用 [已批准方案](<2026-10-10-confirmation-plan.md>)；总记录见 [跨模块实施](<../project/2026-10-10-official-implementation.md>)。

> 后续更新：2026-10-11按维护者要求新增[本地结果模板](<../../../../../../../src/jx3_bot/modules/weather/templates/weather.toml>)及[群／单聊通用匹配与回复](<../../../REQ-20261011-matcher-templates/legacy/qq-integration/2026-10-11-matcher-templates.md>)，当前124项完整离线测试通过。下文首版60项与群作用域说明保留为实施历史，偏好现按AppID／scene／会话／用户隔离；JWT、预算和Geo隐私边界不变。

> 2026-10-12 后续重构：天气实现在[业务模块](<../../../../../../../src/jx3_bot/modules/weather/>)共同维护，原天气／城市／模型文件仅为旧路径兼容导出；模板链接同步到模块目录。完整134项离线测试通过，职责划分与验证见[模块聚合记录](<../../../REQ-20261012-weather-modules/legacy/weather/2026-10-12-module-aggregation.md>)。下文原文件位置和各阶段数量保留为历史，不表示供应商请求／响应模型已补齐或已上线。

## 1. 实际实现与影响范围

新增 [配置](<../../../../../../../src/jx3_bot/config.py>)、[城市／命令模型](<../../../../../../../src/jx3_bot/models.py>)、[SQLite存储](<../../../../../../../src/jx3_bot/store.py>)、[官方城市导入器](<../../../../../../../src/jx3_bot/cities.py>)、[天气客户端与业务](<../../../../../../../src/jx3_bot/weather.py>)。独立 [CLI](<../../../../../../../src/jx3_bot/__main__.py>) 提供导入／更新、用量与运行命令；[配置模板](<../../../../../../../.env.production.example>)、[项目依赖配置](<../../../../../../../pyproject.toml>)和[uv锁文件](<../../../../../../../uv.lock>)及[部署指南](<../../../../../../../docs/qq-integration/2026-10-10-deployment.md>) 已提供。当时的兼容依赖导出已按后续决定移除，见[uv专用依赖管理记录](<../../../REQ-20261016-uv-only/legacy/project/2026-10-16-uv-only-dependency-management.md>)。

不加载旧天气插件，不依赖NoneBot／OneBot／Redis；未修改 旧入口（历史路径 `bot.py`，已清理）、旧天气入口（历史路径 `src/plugins/weather/__init__.py`，已清理） 或读取旧密钥／旧环境配置。暂无POI、小时预报、帐号统计或其他插件迁移。

## 2. 城市与数据边界

- 官方公开CSV本地优先，解析UTF-8 BOM／版本首行，校验必要字段、唯一ID、有限坐标及ZoneInfo时区；城市ID保留字符串。
- GitHub下载走公共contents API，限制响应大小并验证Git blob SHA1，入库记录来源／版本／blob revision／SHA256／时间／记录数。这里revision是**blob哈希，不是commit ID**。
- SQLite事务替换城市／别名，失败保留旧库，成功清天气缓存但保留用户输入／用量。导入与服务共用单实例锁，更新须先停服务。
- 本地别名包括名称／英文／ID以及部分市区县后缀，结合中文行政区消歧；不选第一条。本地重名返回最多8条ID提示，可补行政区查询。列表外才调用GeoAPI，中文多段输入传入location／adm并在内存复核。
- 偏好按AppID／群／用户存**用户主动输入**，设置／查看／删除不隐式查询API。同一用户命令在inbox认领时FIFO，保证设置、删除及随后默认查询的副作用顺序。
- Geo候选ID／名称／坐标／命名时区不存城市表、缓存或outbox；Geo预报标题只用用户输入，时间在内存按地点换算，回复标为“查询地点当地时间”。不借用户偏好或消歧回复积累Geo索引。
- 原始CSV不提交Git、不默认随镜像再分发；未声明MIT／CC BY或无限再分发许可。官方列表并非全世界／最新地理库，遵循 [来源评估](<2026-10-10-city-source-evaluation.md>) 的边界。

## 3. 模块自然月计数

`QWEATHER_MONTHLY_REQUEST_LIMIT=50000`、`QWEATHER_USAGE_TIMEZONE=Asia/Shanghai`；非负上限，0禁用外发。每次Geo／每日预报／重试前SQLite `BEGIN IMMEDIATE`检查并占位；HTTP失败、超时和崩溃不返还，存储失效拒发。所有天气网络调用只通过一个预算入口，关闭隐式重试／重定向。

本地搜索／有效缓存／城市偏好／用量命令／QQ消息／公开CSV下载不计天气数据API调用。自然月按绑定时区YYYY-MM分桶，重启／凭据轮换不清零；降低上限低于当前计数拒发，改已绑定时区或月份倒退拒发。不得删除／重建／回滚数据库绕过计数，恢复旧备份须保守对账。

这是**本模块外发尝试计数**，可能大于供应商收到的请求数，不是帐号账单。没有帐号初始用量、帐号统计API或其他项目消费逻辑；不承诺帐号整体免费。

## 4. 每日预报与鉴权

- 帐号专属HTTPS Host；Geo城市lookup与 `/weather/v1/daily/{latitude}/{longitude}`，Decimal半入两位小数，处理负零。
- 默认3天，可配1–10天；localTime=true、lang=zh。解析forecastStartTime、temperatureMin／Max数量、昼夜condition.text及metadata.attributions。
- 本地列表城市预报单飞／SQLite缓存，默认3600秒且不跨地点当地午夜；键含API版本、城市ID／坐标、天数、语言与时区。Geo路径不缓存。
- 归因必须保留；无结果／缺字段／畸形归因／异常大数安全处理，不将供应商格式错误伪装成成功或使服务退出。
- 鉴权仅支持Ed25519 JWT／Bearer，不在URL查询参数中放凭据；2026-10-11按维护者要求移除其他鉴权配置和分支，详见 [JWT-only变更记录](<../../../REQ-20261011-weather-jwt/legacy/weather/2026-10-11-jwt-only.md>)。JWT需开发者IDiss、项目IDsub、凭据IDkid及新私钥路径，四项必填，签发偏差30秒、有效期15分钟。
- 2026-10-10复核 [每日文档](https://dev.qweather.com/docs/api/weather/weather-daily-forecast/) 公共JSON示例的嵌套温度与`°C`单位／归因URL；2026-10-11复核 [身份认证](https://dev.qweather.com/docs/configuration/authentication/) 的iss／sub含义。仅请求公开文档，不请求真实天气数据。

## 5. 已执行验证

完整命令：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m compileall -q src/jx3_bot tests
```

首版完整套件**60项通过**；2026-10-11 JWT-only变更后完整套件**61项通过**，详见 [本次变更记录](<../../../REQ-20261011-weather-jwt/legacy/weather/2026-10-11-jwt-only.md>)；天气／存储直接覆盖见 [天气测试](<../../../../../../../tests/test_weather.py>)、[城市／月限测试](<../../../../../../../tests/test_store_cities.py>)、[回归测试](<../../../../../../../tests/test_regressions.py>)。HTTP使用MockTransport，日期／事件合成，SQLite临时；预算覆盖零上限、失败／逐次重试、最后一份32线程／双连接争用、跨月、重启、时区变更和存储失败；覆盖缓存单飞／跨日、偏好隔离／删除与Geo字段不落outbox。

另执行一次**公开城市下载CLI冒烟**，显式使用不存在的测试环境文件、临时SQLite，未配置或使用真实QQ／天气凭证；结束后临时库清理：

| 项 | 实际结果 |
| --- | --- |
| 版本 | China-City-List v202506200 |
| 记录数 | 3577 |
| blob revision | 3ee735fa2176d306009c354386ef8e903bcdeee7 |
| SHA256 | 95bdaeef4f273feb03d706a066526c61b9e2aee599b1b79c1b54aafc2279b8be |
| 北京检索 | 1个匹配 |
| 朝阳检索 | 4个别名匹配；含后缀别名，区别于前序原始名称精确匹配3条 |
| 天气数据API尝试 | 0 |

公共下载不属于受保护天气API联调，也不代表已初始化生产数据库。Ed25519真实签名／验签使用测试临时生成的私钥，不读取旧PEM、不打印令牌。

## 6. 未完成与下一步

生产城市库／实际配置由维护者按部署指南建立；真实天气鉴权、返回结构／时间、授权群指令与IPv6出站仍待联调。其他业务未实现；旧框架正式移除、凭证泄露治理、生产监控／CI另行推进。不重新要求帐号用量或城市源选择。
