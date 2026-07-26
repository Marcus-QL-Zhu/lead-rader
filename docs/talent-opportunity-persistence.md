# 人才主题—目标公司—岗位机会持久化

## 目标

每日 MiniMax 生成的人才主题不仅用于当日猎聘广告，也作为后续 Float 分析的市场机会库。系统不保存候选人简历；只持久化公开市场信号、目标公司、推测岗位、证据链接和猎聘 JSON。

## 快照边界

同一 `source_run_id` 在同一天可能因模型调优或人工重跑生成不同草稿。当前审批与飞书必须只读取最新 bundle 的精确草稿集合，不能按日期累加。

SQLite 使用四层记录：

- `talent_pool_current_snapshots`：当前日期/方向唯一指向一个已提交的 bundle 快照，即使 bundle 为空也有明确边界。
- `talent_pool_current_snapshot_drafts`：当前快照的精确草稿成员，用于飞书和审批。
- `talent_pool_bundle_snapshots`：不可变 bundle JSON 历史，包括模型、分析错误、公司岗位推断和全部草稿。
- `talent_pool_opportunity_links`：规范化的“人才主题/猎聘草稿 → 目标公司 → 公司岗位假设”关系；同时保存证据 URL 与完整猎聘 payload。新快照只把同日同方向的旧关系标为非当前，不删除历史。

旧草稿即使与新草稿共享 `source_run_id`，也不会再进入当前飞书或审批批次；已经发布的历史记录仍保留。

## 飞书内容

每条建议职位显示：

1. 草稿 ID 和建议职位标题；
2. `目标公司 → 对应公司岗位假设`；
3. 人才画像、吸引角度和 why-now；
4. 可直接交给 Liepin skill 的完整 `public_payload` JSON。

飞书只读取 SQLite 已原子提交的当前 bundle，不读取可能残留的旧 JSON 文件。通常汇总和 JSON 在一条消息内；超过飞书安全长度时，自动拆成汇总和逐条 JSON 消息，并分别做幂等记录。SQLite 同时承担审批状态和长期机会历史，因此同日多次运行不会混合。

## 后续 Float 查询

Agent/OpenClaw 可以调用：

```bash
python scripts/query_talent_opportunities.py \
  --state-db data/talent-pool.sqlite \
  --term 数据采集 \
  --term 具身智能
```

只查看当前快照：

```bash
python scripts/query_talent_opportunities.py \
  --state-db data/talent-pool.sqlite \
  --direction 具身智能 \
  --current-only
```

返回值包含目标公司、公司岗位假设、人才主题、人才画像、证据链接、猎聘 JSON、模型及快照时间。该查询只负责召回持久化机会，不建立原生结果评分；候选人与公司的最终匹配仍由 Agent 分析和人工判断。