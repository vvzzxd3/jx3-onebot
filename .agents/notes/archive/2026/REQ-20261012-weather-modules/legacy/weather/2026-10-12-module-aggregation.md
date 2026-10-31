> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/weather/2026-10-12-module-aggregation.md`。

# 天气按业务模块聚合重构

- 建档／最近更新：2026-10-12（Asia/Shanghai）
- 状态：代码、文档与完整离线验证完成；真实联调未执行。
- 需求来源：维护者批准“业务模块纵向聚合＋独立 QQ 接入＋精简公共核心”，要求先写入 [README](<../../../../../../../README.md>)／[AGENTS](<../../../../../../../AGENTS.md>)，再修改天气代码。
- 跨职责汇总：[官方实施总记录](<../../../REQ-20261010-official-weather/legacy/project/2026-10-10-official-implementation.md>)。

## 1. 开始前事实与边界

重构前 [原天气文件](<../../../../../../../src/jx3_bot/weather.py>) 混合 JWT、网络请求、格式化及业务编排；[原模型入口](<../../../../../../../src/jx3_bot/models.py>) 混合城市、命令和 QQ 消息；[原存储层](<../../../../../../../src/jx3_bot/store.py>) 同时承担天气 SQL 与 inbox/outbox。前述链接现在指向兼容层／共享设施，不代表仍有原来的混合实现。

维护者此前要求撤销的“全局 models 包”方案已经撤销，本轮不恢复。实际采用业务与数据模型同模块组织；共享／QQ模型只承担各自职责。

### 非目标

- 不变更命令、天气接口、JWT-only、月预算、Geo 隐私、缓存、群／单聊隔离或投递可靠性。
- 不补齐完整天气请求／响应 DTO：本轮先完成职责迁移，供应商预报仍是原字典及手工校验处理。
- 不修改数据库 schema、重建数据库或拆成多数据库；不新增依赖，不改真实配置，不运行联网服务。
- 不顺带迁移全部 QQ HTTP／WSS、日志、完整匹配器和公共数据库设施。本轮先落地天气与必要的共享／接入模型，其余保持原位置。

## 2. 规则固化与实际代码归属

先在 README／AGENTS 写入模块聚合、模型归属、共享事务、兼容层和分阶段迁移约定，随后实施代码调整。

| 实际位置 | 归属及职责 |
| --- | --- |
| [天气模型](<../../../../../../../src/jx3_bot/modules/weather/models.py>) | City、坐标规范化、名称别名及现有校验；不包含 QQ 消息 |
| [天气客户端](<../../../../../../../src/jx3_bot/modules/weather/client.py>) | Ed25519 JWT、Geo／每日预报请求、逐次请求预算占位及重试 |
| [天气服务](<../../../../../../../src/jx3_bot/modules/weather/service.py>) | 默认城市、城市解析、消歧、缓存单飞及业务错误分类 |
| [天气仓储](<../../../../../../../src/jx3_bot/modules/weather/repository.py>) | 预算、城市／别名、偏好及缓存的实际 SQL，复用共享存储连接／锁／事务 |
| [城市导入](<../../../../../../../src/jx3_bot/modules/weather/cities.py>) | 官方公开 CSV 下载／校验／原子导入 |
| [天气命令](<../../../../../../../src/jx3_bot/modules/weather/handlers.py>) | 天气命令注册、参数验证和兼容解析，使用同一匹配器注册表 |
| [天气格式化](<../../../../../../../src/jx3_bot/modules/weather/rendering.py>) | 预报字段／归因校验、模板参数约束和通用回复转换 |
| [天气模板](<../../../../../../../src/jx3_bot/modules/weather/templates/weather.toml>) | 原19类结果文本逐字保留；放在业务自己的模板目录 |
| [公共模型](<../../../../../../../src/jx3_bot/core/models.py>) | Command、MatchContext、Reply，保持原字段／默认值／格式化 |
| [QQ模型](<../../../../../../../src/jx3_bot/integrations/qq/models.py>)、[QQ事件](<../../../../../../../src/jx3_bot/integrations/qq/events.py>) | 入站消息／判定、官方事件规范化与旧群模型别名 |

根 [天气兼容入口](<../../../../../../../src/jx3_bot/weather.py>)、[城市兼容入口](<../../../../../../../src/jx3_bot/cities.py>)和[模型兼容入口](<../../../../../../../src/jx3_bot/models.py>)只导出原API，不复制实现。旧 [匹配器](<../../../../../../../src/jx3_bot/matching.py>) 的天气注册／解析函数改为委托模块；共享回复仍由 [通用渲染层](<../../../../../../../src/jx3_bot/replies.py>) 输出安全QQ负载，它从模块目录加载模板，并使用天气拥有的参数约束。

[CLI](<../../../../../../../src/jx3_bot/__main__.py>)直接装配新天气模块与仓储；[网关](<../../../../../../../src/jx3_bot/gateway.py>)直接引用QQ事件解析。原平铺文件不是后续业务开发入口。

## 3. 持久化及兼容关键决策

1. Store 成功打开原连接、完成既有迁移后创建唯一的 WeatherRepository；新仓储只持有共享存储能力，不新开连接、不新建锁。天气代码接受仓储；历史调用传入 Store 时解析为其同一个 weather 仓储。
2. 天气 SQL 迁入仓储，Store 的同名预算／城市／偏好／缓存方法保留兼容委托，不复制 SQL。原 schema 初始化、历史 scene 迁移与综合 housekeeping 保留在 Store；缓存与 inbox/outbox 清理仍在原同一事务执行。
3. 原 `BEGIN IMMEDIATE`／RLock／WAL、预算时区绑定、逐次先占位、失败不退与倒退月份拒发不变；不存在第二份预算桶或数据库重置。
4. 旧消息别名、字段与JSON恢复保持不变；群／C2C去重与FIFO、被动窗口、固定seq／payload、uncertain不重发未变更。
5. 模板原内容与迁入副本逐字比对一致后，逐路径核实并删除旧全局模板文件，避免双份维护；通用加载器读取模块自己的模板。文档模板路径和旧模型行号链接同步，历史方案保留并指向新记录。
6. 模型仅依赖基础异常和共享模型；事件与命令注册的匹配器导入延迟到函数内，避免循环导入。天气自身不导入QQ HTTP／WSS或发送消息。

## 4. 实际验证

使用现有 uv 管理的独立环境（Python3.12.11），没有依赖变更，无须同步或更新锁文件：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-official-uv/bin/python -m unittest discover -s tests -q
PYTHONPATH=src .venv-official-uv/bin/python -m compileall -q src/jx3_bot tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv-official-uv/bin/python -m jx3_bot --help
git diff --check -- README.md AGENTS.md docs src/jx3_bot tests
```

- 首次迁移后原124项完整回归通过（0.849秒）。更新 [存储失败测试](<../../../../../../../tests/test_weather.py#L132-L135>) 的注入点到实际客户端仓储，仍验证存储失败在天气外发前阻止请求，没有删减断言。
- 新增 [10项模块回归](<../../../../../../../tests/test_weather_module.py>)：新／旧导出对象身份、定义归属／薄兼容层、独立首导入顺序、城市／消息序列化与旧payload、仓储／旧Store双向共享预算／偏好／缓存及重启、存储关闭后失败边界、模板实际位置／文本、SQL不重复、离线CLI本地CSV／额度命令，以及新模块仓储→客户端→服务→群／单聊命令链路。
- 最终完整 **134项全部通过，1.020秒**。包含既有预算并发／跨月／重启、旧库迁移／故障、Geo隐私、FIFO、固定outbox、QQ连接故障及文本／Markdown回归。HTTP为MockTransport、消息及身份为合成、数据库／测试私钥为临时或新生成。
- compileall、离线CLI帮助、限定`git diff --check`通过；24份Markdown／456处本地链接存在性、文档围栏／换行／行尾空白／私钥标记及29个官方Python模块AST／格式检查通过，覆盖未跟踪的新文件；未发现NoneBot／OneBot运行导入。
- 独立只读审查未发现可复现缺陷；额外18模块首导入、纯内存预算／rollback／FIFO／固定seq／Geo字段不落库核查通过。它不替代本轮完整持久化／重启回归，也不作为真实接口验证。

本轮没有失败测试遗留，没有请求真实QQ Token／WSS／天气数据，也未下载公开CSV；本地CSV回归使用合成数据。没有读取实际专用配置、旧PEM或旧JWT脚本，没有修改生产数据库、旧插件、依赖或自动提交。已有工作区改动保留。

## 5. 未完成与下一步

- 完整天气请求／响应DTO与内部预报模型仍未实现，当前HTTP参数及预报数据继续字典处理，校验仍是原手工校验。
- 其余公共设施与QQ传输尚未全量搬入目标目录；后续独立重构，不将本轮天气完成写成全项目迁移完成。
- 真实QQ／天气联调、正式切换及其他业务仍未完成。部署继续使用同数据库；如升级运行进程，由维护者安全停机／备份后重启，不借结构迁移删除或回滚预算。
