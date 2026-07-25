# Lead Rader

Michael Page 中国硬科技业务的证据优先 Lead Generator。系统从招聘广告之前的公开信号识别可能出现总监级以上组织需求的公司，默认返回最多 20 家并解释每一分的来源。

当前版本：`0.3.0`。

## 已交付能力

- 两个显式模式、一个统一后端：
  - `Market Scan`：行业/技术方向 → 公司 → 总监级岗位假设。
  - `Candidate Float`：临时候选人能力 → 公司 → 匹配与时机分析。
- 两个不可放宽的硬门槛：
  - 必须能形成 Director / Head / VP / GM / CxO 或同等组织责任岗位假设；
  - 必须至少有一条招聘广告之前的上游信号。
- 为补足 20 家只降低软分数门槛；不足 20 家时不虚构。招聘广告-only 公司只进入晚期机会附录。
- 每家 Lead 展示需求、岗位、时机、商业化、证据可信度、广告扣分和原始链接。
- 每日 Top 20 只做基础研究；用户主动要求深挖或运行 Float 时，才搜索投资人、Hiring Manager、HR/TA/HRBP 和创始团队。
- `SourceDocument → Statement → BusinessEvent → CanonicalEntity` 四层事实库，含四级去重、事件生命周期、规范证据、可撤销实体/事件判断。
- 六阶段幂等运行：`collect → normalize → eventize → score → basic_research → publish`，支持失败续跑和低成本 Replay。
- 飞书多维表格幂等增量投影；后端 SQLite 是事实源，飞书只是行动队列。
- 投资人/机构/公开人物缓存和关系图。
- 统一 suppression、审计、来源许可边界、监控、SQLite 在线备份。
- 不生成 BD/Float 触达话术，不发送消息，不抓取私人联系方式，不建设业务结果评分/训练系统。

## 固定信源与 Metaso

`config/source-packs.json` 当前登记 29 个已核验状态的公开来源、6 个来源包，覆盖：

- 中国大陆通用政策、项目、环评、招投标和融资；
- 脑机接口；
- 半导体；
- 商业航天；
- 核聚变；
- 具身智能。

19 个静态或公开列表源默认启用。验证码、412、动态渲染、未文档化接口或许可不清晰的来源保留为禁用状态，不绕过访问控制。

Metaso 只用于最高优先级公司的全网核验，不做常规发现。默认每天最多核验 3 家，按最坏 6 积分/次记账，项目日预算 30 积分；持久化账本同时硬性保护 Metaso 的 500 积分日上限。失败续跑不会重复付费调用。

## 使用

安装为可编辑包（可选；脚本本身不依赖第三方包）：

```bash
python -m pip install -e .
```

自然语言 Market Scan：

```bash
python scripts/run_lead_radar_v2.py ask \
  --question "最近脑机接口行业有哪些公司可能要招总监以上职位？" \
  --provider auto
```

传统方向入口：

```bash
python scripts/run_lead_radar_v2.py run \
  --direction 半导体 \
  --provider fixed
```

Candidate Float：

```bash
python scripts/run_lead_radar_v2.py float \
  --candidate "数据采集总监，负责多源数据采集体系和50人团队" \
  --direction 具身智能
```

单公司深度研究：

```bash
python scripts/run_lead_radar_v2.py deep-research \
  --company 示例科技 \
  --direction 脑机接口
```

来源健康、运行状态、监控和备份：

```bash
python scripts/run_lead_radar_v2.py source-health --direction 商业航天
python scripts/run_lead_radar_v2.py run-status --run-id RUN_ID
python scripts/run_lead_radar_v2.py monitor
python scripts/run_lead_radar_v2.py backup --backup-dir backups
```

确定性验收夹具：

```bash
python scripts/run_lead_radar_v2.py run \
  --direction 灵巧手 \
  --demo \
  --metaso-verify-limit 0
```

## 每日服务器任务

服务器入口为 `scripts/run_daily_fixed_sources.sh`，cron 使用 Asia/Shanghai 的每天 `05:00`：

```cron
0 5 * * * /home/admin/.openclaw/workspace/skills/hardtech-lead-radar/scripts/run_daily_fixed_sources.sh
```

每日方向通过 `HT_LEAD_DAILY_DIRECTION` 配置，默认 `具身智能`。每日任务使用固定来源包、Top 20、零软分门槛、JOSINT 晚期广告验证、Metaso 限额核验和健康报告。

## 数据边界

- Candidate Profile 只存在于当前进程内；不会写入事实库、运行 checkpoint、飞书或人物关系图。
- Float 结果文件可以留在用户工作区供当次查看，但系统不建设候选人数据库、简历生命周期或派生画像缓存。
- 投资人和企业决策者只保存公开职业信息、来源、核验日期、置信度及“事实/推断”标记。
- 飞书 `HT Lead Radar` 专用表及 13 个字段已创建；完整写入配置以权限 `600` 暂存，日任务保持 dry-run，待用户明确批准真实 Lead 写入后启用。

## 关键文档

- [Approved Alignment Spec](docs/alignment-spec.md)
- [Implementation Plan / Delivery Ledger](docs/implementation-plan.md)
- [外部最佳实践调研（15个核心、20个总样本）](docs/best-practices-landscape-2026-07-25.md)
- [方案蓝图](docs/solution-blueprint.md)
- [融资信源扩容与 10 项随机覆盖审计](docs/funding-source-coverage-2026-07-25.md)

## 验证

```bash
python -m pytest -q
python -m compileall -q src
```


## 生产交付验收

2026-07-25 已完成本地与 OpenClaw 服务器生产验收：

- 155 项自动测试通过；
- 真实脑机接口固定信源运行无机器人串榜，Metaso 消耗 0；
- 按需深研、关系缓存、监控和 8 个 SQLite 数据库一致性备份通过；
- cron 为每天 05:00 运行、每周日 05:45 备份。

[查看完整生产交付状态](docs/delivery-status-2026-07-25.md)
