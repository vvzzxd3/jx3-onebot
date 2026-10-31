---
id: "REQ-20261012-multi-instance"
title: "多实例／多AppID后续待办"
status: "proposed"
approval: "pending"
outcome: null
modules: ["project", "qq-integration"]
tags: ["project", "qq-integration", "legacy-migration"]
created: "2026-10-12"
updated: "2026-10-19"
archived: null
relations: [{"type": "related-to", "target": "REQ-20261010-official-weather"}]
---

# 多实例／多AppID后续待办

## 来源、范围与结论

仅批准登记待办，不批准实施。当前暂不处理AppID更换，不改数据库绑定或现有数据。未来需确认部署形态、应用状态隔离、共享天气预算或保守分摊、共存／故障验证及排期；不得据旧记录直接开工。

来源为下表所列原任务记录中的维护者要求与当时验收结论，非本次重新批准或复验。原提交状态未核实；本次没有Git提交。

## 类别材料

以下历史正文只供追溯；其中“当前”“最新”“必须”“下一步”及历史测试数字均属于原时间语境，不自动生效。历史材料即使仍在 active，也不作为现行决策依据。

| 类别 | 原材料（保留正文，修正相对链接） |
| --- | --- |
| proposal | [2026-10-12-multi-instance-todo](<legacy/project/2026-10-12-multi-instance-todo.md>) |

## 验证与未覆盖范围

本次仅核对记录归组、正文完整性与链接，不重新执行上述业务测试，不读取真实配置／私钥／数据库，不运行服务；原阶段结果不能当作本次验证。

## 现行规范与关联

长期规则以[开发规范](<../../../../AGENTS.md>)、[正式文档](<../../../../docs/README.md>)及[路线图](<../../../../docs/project/2026-10-10-roadmap.md>)为准，不依赖本包历史正文。

- `related-to` → [REQ-20261010-official-weather](<../../archive/2026/REQ-20261010-official-weather/index.md>)（仅表示需求关系，不继承历史约束）。

## 交接与收尾

待维护者确认实施范围及验收后，在本包继续完善提案；不得自动进入实施，也不为新会话另建副本。

2026-10-19：首次按需求归组迁入；保持未提交状态，不推断历史提交哈希。
