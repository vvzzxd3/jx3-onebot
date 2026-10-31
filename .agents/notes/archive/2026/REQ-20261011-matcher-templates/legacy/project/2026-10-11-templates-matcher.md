> **历史材料／非现行规范**：2026-10-19迁入需求包。下文保留当时语境，不是当前决策、实施授权或默认上下文；旧待办与驳回不自动生效。收尾状态见需求主记录。原路径：`docs/project/2026-10-11-templates-matcher.md`。

# 统一输出模板与群聊／单聊匹配器

- 建档／最近更新：2026-10-11（Asia/Shanghai）
- 状态：代码、配置示例及124项离线验证完成；当前用户授权本轮两项优化，真实接口联调不在本轮范围。
- 来源：维护者要求每类功能结果模板化、核实官方格式并美化输出；参考旧NoneBot2处理方式，实现群聊与单聊输入匹配及对应功能触发。
- 关联：[项目说明](<../../../../../../../README.md>)、[原单聊排查](<../../../REQ-20261011-console-diagnostics/legacy/qq-integration/2026-10-11-private-message-no-response.md>)、旧天气（历史路径 `src/plugins/weather/__init__.py`，已清理）、旧AI（历史路径 `src/plugins/ai/__init__.py`，已清理）、旧复读（历史路径 `src/plugins/repeat_message/__init__.py`，已清理）。

> 2026-10-12 后续组织调整：天气已按业务模块聚合，模板链接更新到模块目录，共享／QQ模型分开；详细改动与134项完整回归见[天气模块聚合记录](<../../../REQ-20261012-weather-modules/legacy/weather/2026-10-12-module-aggregation.md>)。下文保留本阶段124项及实施历史，不表示已上线。

## 当前事实与目标

当前官方模型只识别两个群消息事件，正则与天气功能耦合，HTTP仅发送群纯文本；单聊仅观察。新链路已有96项离线测试。旧插件使用on_command／on_message、rule=to_me、priority及block；只借鉴概念，不引入NoneBot依赖或迁移旧AI／复读业务。

目标：业务输出和平台格式分层、结果类别集中模板化；用统一注册器完成命令／全匹配／正则匹配、别名、会话限制、规则、优先级与阻断；群／单聊经过同一可靠inbox→handler→outbox链路，偏好、去重与FIFO按会话类型隔离。

非目标：不新增多平台／OneBot兼容，不归档普通聊天，不改变JWT、月预算和Geo隐私规则，不启动真实服务或读取实际凭据，不冒称本应用具备Markdown／按钮权限。

## 实施方案与依据

1. 核实QQ群／C2C消息格式和权限；普通文本为默认无额外权限美化方式，富格式只显式配置启用。官方模板与本地业务模板是两层概念。
2. 新增本地结果模板与结构化回复，统一标题、字段、提示和数据来源；所有动态字段按输出格式转义。
3. 新增QQ消息上下文及匹配器注册表；接收阶段只保存匹配后的最小命令，不存原始事件、附件或鉴权数组。处理阶段按已持久化匹配名称路由，不重读原文。多匹配结果合并一次被动回复、固定seq，不突破原发送语义。
4. SQLite增加明确scene作用域并原子兼容迁移既有群数据；预算、缓存、城市库、AppID／时区绑定不重置。单聊偏好只在单聊中生效。
5. 核实C2C事件与发送接口后启用单聊天气；身份缺失、过期、机器人及普通聊天继续过滤。保守保留300秒期限，不借新文档更长窗口改变既有策略。
6. 新增合成事件、MockTransport、临时SQLite迁移／重启测试，重跑完整离线回归；同步当前文档并保留历史排查。

NoneBot参考：[事件响应器进阶](https://nonebot.dev/docs/advanced/matcher)，本轮已读取，priority小值优先，同级按注册顺序，block阻断下一优先级；不是完整兼容NoneBot的DI／临时会话框架。

## 风险与待验证

- 官方已开放所有机器人群／单聊自定义Markdown及按钮，新具体schema的模板ID已废弃；公开能力不等于本应用／具体群已联调。失败POST不自动换格式重发。官方来源与文档冲突见[模块记录](<../qq-integration/2026-10-11-matcher-templates.md>)。
- C2C用户OpenID与群成员OpenID不默认视为同一用户；数据库迁移需停进程并备份，保持既有月计数。
- 真实@正文、单聊权限、供应商实际响应仍待本机授权联调。
- web_search当前缺搜索工具凭据；改用公开官方页面／官方SDK核实，不读取项目凭据或请求受保护接口。

## 实施与验证

### 实际改动

- 新增[通用匹配器](<../../../../../../../src/jx3_bot/matching.py>)、[渲染层](<../../../../../../../src/jx3_bot/replies.py>)、[天气结果模板](<../../../../../../../src/jx3_bot/modules/weather/templates/weather.toml>)及[28项专项测试](<../../../../../../../tests/test_matching_replies.py>)；扩展现有QQ规范化、网关、业务、HTTP、worker、存储和启动组装，不新增依赖。
- 当前天气19类结果模板化，text／markdown可配置；新功能可分别创建模板模块与注册async handler。官方自定义Markdown／按钮开放及模板ID废弃，按具体新schema与FAQ核实，未实现媒体／按钮，详细URL与冲突见[QQ模块记录](<../qq-integration/2026-10-11-matcher-templates.md>)。
- 群与C2C有明确scene，去重／FIFO／主动偏好隔离；旧群三表换表原子迁移，预算与不确定回复保留。普通聊天继续不入库；Geo隐私／JWT-only／天气月限不变。
- 同步README、AGENTS、索引、部署指南、路线图与模块实施记录，历史单聊排查和日志方案保留并加后续链接。仅修改无密钥配置示例，未读取或改变实际配置、生产库、旧插件、私钥及现有服务。

### 已执行验证

```bash
PYTHONPATH=src .venv-official-uv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv-official-uv/bin/python -m compileall -q src/jx3_bot tests
PYTHONPATH=src .venv-official-uv/bin/python -m jx3_bot --help
UV_PROJECT_ENVIRONMENT=.venv-official-uv UV_CACHE_DIR="$PWD/.cache/uv" uv pip check --python .venv-official-uv/bin/python
```

最终完整124项通过（0.856秒），Python3.12.11，13项已安装依赖一致；CLI帮助和compileall通过。专项含群／单聊全链路、两格式与19模板、模块模板／路径安全、旧库／更早无attempts库／迁移中途失败／重启、固定seq和payload、Geo不入Markdown outbox、过期／不确定发送／拒绝Markdown不重发。

只读审查额外复现3项并修复、补回归：Token预检跨期限仍POST → 最后POST前复核；旧attempts ALTER事务外残留 → 三表事务内兼容复制；多个结果合并超长导致服务退出 → 单条友好提示，不截断／拆分。独立审查观察非冻结提交，最终回归以本轮主代理结果为准。

首次旧96项回归有1条旧日志断言失败：普通C2C由unsupported_event改为command_not_matched，按本轮新范围修正。首次新专项有1条模板数量断言误写19而当时为18，修正后又新增输出超长模板，最终19类全部测试覆盖。没有保留失败冒称通过。

收尾验证23份Markdown、391处本地链接、14个官方模块AST无NoneBot／OneBot运行导入，以及限定源代码／模板／配置示例格式通过；git diff --check通过。发现并修复一处历史虚拟环境配置文件已不存在的失效链接，只保留当时观察，不重建环境或推断删除原因。未跟踪的新文件由显式文件列表检查补足，未读取真实配置内容。

没有请求真实QQ Token／WSS／天气数据、启动或替换服务、读取实际凭据、重置预算或自动提交；公开文档／官方SDK研究与受保护API联调区分记录。

### 未完成与下一步

维护者停原官方进程并备份同数据库后升级重启；群与QQ单聊分别联调命令、偏好隔离、@正文／群主全量开关、Markdown客户端展示和实际供应商响应。旧schema迁移后不盲目回滚不兼容的旧官方代码，也不恢复旧计数快照绕预算。真实联调／正式切换及剑网3业务仍未完成。

### 变更历史

| 日期 | 变化 | 依据 |
| --- | --- | --- |
| 2026-10-11 | 用户授权模板美化及群／单聊通用匹配；完成代码、124项离线验证与当前状态同步 | 本轮明确需求、当前官方消息schema／FAQ／NoneBot概念、合成离线验证 |
