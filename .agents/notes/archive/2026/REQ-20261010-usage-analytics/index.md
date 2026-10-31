---
id: "REQ-20261010-usage-analytics"
title: "已撤销专项与早期聊天投影"
status: archived
approval: "unknown"
outcome: "superseded"
modules: ["usage-analytics", "chat-history"]
tags: ["usage-analytics", "chat-history", "legacy-migration"]
created: "2026-10-10"
updated: "2026-10-19"
archived: 2026-10-31
relations: []
---

# 已撤销专项与早期聊天投影

## 来源、范围与结论

原专项已撤销，旧要求正文已按维护者指示删除，不恢复被删除内容；早期短期聊天投影随后被四表方案替代。本包仅保存现存撤销与替代摘要，不是启用教程或后续开发授权。

来源为下表所列原任务记录中的维护者要求与当时验收结论，非本次重新批准或复验。原提交状态未核实；本次没有Git提交。

## 类别材料

以下历史正文只供追溯；其中“当前”“最新”“必须”“下一步”及历史测试数字均属于原时间语境，不自动生效。历史材料即使仍在 active，也不作为现行决策依据。

| 类别 | 原材料（保留正文，修正相对链接） |
| --- | --- |
| proposal | [2026-10-10-plan](<legacy/usage-analytics/2026-10-10-plan.md>) |
| implementation | [2026-10-12-chat-storage-implementation](<legacy/usage-analytics/2026-10-12-chat-storage-implementation.md>) |
| implementation（失效示例） | [analytics.example](<legacy/usage-analytics/analytics.example.toml>) |

[旧示例](<legacy/usage-analytics/analytics.example.toml>)保持原字节不变，只有注释、没有有效配置；注释中的旧相对路径及“under review”同样仅为历史文本，不是可用导航或当前审批状态。替代需求见[四表聊天数据库](<../REQ-20261013-chat-history/index.md>)。

## 验证与未覆盖范围

本次仅核对记录归组、正文完整性与链接，不重新执行上述业务测试，不读取真实配置／私钥／数据库，不运行服务；原阶段结果不能当作本次验证。

## 现行规范与关联

长期规则以[开发规范](<../../../../../AGENTS.md>)、[正式文档](<../../../../../docs/README.md>)及[路线图](<../../../../../docs/project/2026-10-10-roadmap.md>)为准，不依赖本包历史正文。


## 交接与收尾

已结束的原约定范围等待获授权的收尾提交；不继续实施旧正文中的待办。收尾前核对现行规范与范围，整包迁入归档、修正全部链接并同步索引。真实联调／运维等未验收事项由现行路线图与部署清单明确管理，不声称已经上线。

2026-10-19：首次按需求归组迁入；保持未提交状态，不推断历史提交哈希。
