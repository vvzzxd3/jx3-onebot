---
id: "REQ-20261016-official-only"
title: "官方服务单一入口与旧框架清理"
status: archived
approval: "approved"
outcome: "completed"
modules: ["project", "qq-integration"]
tags: ["project", "qq-integration", "legacy-migration"]
created: "2026-10-16"
updated: "2026-10-19"
archived: 2026-10-31
relations: [{"type": "follows-up", "target": "REQ-20261010-official-weather"}]
---

# 官方服务单一入口与旧框架清理

## 来源、范围与结论

维护者要求清理旧架构；工作树代码与元数据清理及离线验证完成。并未停止真实旧进程或完成生产切换，也不能消除Git索引／历史中的潜在凭据泄露。

来源为下表所列原任务记录中的维护者要求与当时验收结论，非本次重新批准或复验。原提交状态未核实；本次没有Git提交。

## 类别材料

以下历史正文只供追溯；其中“当前”“最新”“必须”“下一步”及历史测试数字均属于原时间语境，不自动生效。历史材料即使仍在 active，也不作为现行决策依据。

| 类别 | 原材料（保留正文，修正相对链接） |
| --- | --- |
| implementation | [2026-10-16-official-only-cleanup](<legacy/project/2026-10-16-official-only-cleanup.md>) |

## 验证与未覆盖范围

本次仅核对记录归组、正文完整性与链接，不重新执行上述业务测试，不读取真实配置／私钥／数据库，不运行服务；原阶段结果不能当作本次验证。

## 现行规范与关联

长期规则以[开发规范](<../../../../../AGENTS.md>)、[正式文档](<../../../../../docs/README.md>)及[路线图](<../../../../../docs/project/2026-10-10-roadmap.md>)为准，不依赖本包历史正文。

- `follows-up` → [REQ-20261010-official-weather](<../REQ-20261010-official-weather/index.md>)（仅表示需求关系，不继承历史约束）。

## 交接与收尾

已结束的原约定范围等待获授权的收尾提交；不继续实施旧正文中的待办。收尾前核对现行规范与范围，整包迁入归档、修正全部链接并同步索引。真实联调／运维等未验收事项由现行路线图与部署清单明确管理，不声称已经上线。

2026-10-19：首次按需求归组迁入；保持未提交状态，不推断历史提交哈希。
