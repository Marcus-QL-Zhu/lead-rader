# HT Lead Radar 外部项目最佳实践研究

> 研究日期：2026-07-25  
> 方法：三个子代理并行研究中文信息聚合、新闻/事件基础设施、商业情报与关系图谱；主会话只接收结构化结论并做 fan-in。  
> 覆盖：约 20 个候选，15 个核心案例，另有若干排除案例。  
> 证据原则：优先项目官网、官方文档、GitHub 和论文；二手披露单独标注，不把营销说法当作源码事实。

## 1. 结论

不存在一个开源项目可以直接完成“固定信源采集 → 公司归一 → 新闻去重 → 业务事件聚类 → 总监级需求推理 → Top 20 → Buying Center/投资人图谱”。正确策略不是更换整套系统，而是把成熟的基础模块接入 HT Lead Radar，并保留招聘业务判断作为自有核心。

最重要的七条结论：

1. **AI HOT 最值得借鉴的是产品和数据流，不是代码。**其完整后端没有找到公开源码；公开的是只读 API、RSS 和 Agent Skill。
2. **Article 不等于 Event。**十篇转载同一轮融资只能形成一个事件；转载数量增加可信度，不能重复增加十次需求分。
3. **公司实体归一与事件聚类必须分开。**同一公司同一天可以同时融资、签约和建厂，不能因公司相同而错误合并。
4. **事实应保存为带来源的 Statement。**公司、人、机构、任职和投资关系不能用最后一次抓取结果覆盖旧值。
5. **实体合并必须可回退。**机器只能提出 `POSITIVE / NEGATIVE / UNSURE / NO_JUDGEMENT` 判断，不应永久破坏原始记录。
6. **每日采集和按需深研走两条成本路径。**05:00 任务只做固定源、事件化、Top 20 和基础研究；Metaso、动态网页、人物履历只在证据缺口、Float 或主动深挖时使用。
7. **不要引入第二套编排或 CRM。**现有 OpenClaw、cron、Python pipeline、后端事实库和飞书投影足够；Huginn、Twenty、Aleph 整套部署会增加重复状态和运维面。

## 2. AI HOT 深度核验

确认站点是[AI HOT](https://aihot.virxact.com/)，作者为“数字生命卡兹克”，身份可由其[官方 About 页面](https://aihot.virxact.com/about)核实。官方提供[Agent Skill、RSS 和 REST API](https://aihot.virxact.com/agent)，GitHub 公开的是 [khazix-skills/aihot](https://github.com/KKKKhazix/khazix-skills/tree/main/aihot)。

### 2.1 已由官方页面确认

- 来源包含网站、RSS、X、公众号等；
- 同一事件的中英文报道可以聚合；
- 不同事件不会仅因公司相同而合并；
- 同事件优先展示官方公告或当事人原始发声；
- 转发、引用和讨论折叠到主事件；
- 输出中文摘要、翻译、推荐理由和 0–100 分；
- 初次使用 snapshot，后续通过 changes 获取新增、修改和撤选；
- 支持 ETag/304，游标失效时要求重新 snapshot；
- 支持网页、RSS、API、Agent Skill 和飞书分发。

这些能力可以从其[更新日志](https://aihot.virxact.com/changelog)和[接入文档](https://aihot.virxact.com/agent)核实。

### 2.2 不能误判为已开源

没有找到 AI HOT 采集、聚类、评分、数据库或网站后端的完整公开 repo。Skill 使用 MIT License，不代表后端或 API 数据采用 MIT；数据使用还受其[接入条款](https://aihot.virxact.com/terms)约束。

一篇[二手架构披露](https://www.chooseai.net/news/3664/)提到约168个分级信源、DeepSeek预筛、强模型评分、embedding聚类和代码加权。由于无法用公开源码复核，这些只能作为“可信度中等的二手披露”，不能当作确定实现。

### 2.3 对 HT Lead Radar 的启发

- `Source` 成为一级实体：等级、所有权、采集方式、频率、健康、版权边界；
- 每个 Event 选择 canonical evidence：企业/政府原文优先，投资机构其次，媒体转载折叠；
- 飞书/API 使用 `snapshot + change cursor`，只同步变化；
- 规则和廉价模型先筛，高成本搜索只处理高价值事件；
- 摘要、结构化事实和原始证据分层，摘要不能替代原文。

## 3. 核心项目对比

| 项目 | 强项 | 开源/许可核验 | 对 HT Radar 的决定 |
|---|---|---|---|
| [AI HOT](https://aihot.virxact.com/) | 来源分级、事件聚合、官方源优先、增量 API | 后端未开源；Skill MIT | 参考产品与数据流，不依赖其后端 |
| [TrendRadar](https://github.com/SANSAN0/TrendRadar) | 中文热点/RSS、SQLite、增量模式、AI失败回退、飞书/MCP | 根 LICENSE 为 GPL-3.0 | 参考 checkpoint、缓存和输出；不嵌入代码 |
| [Horizon](https://github.com/Thysrael/Horizon) | 低成本预筛，只对重要条目全网补充 | MIT | 评估 provider/飞书适配器；参考 enrich-only-important |
| [NewsPrism](https://github.com/moguiyu/NewsPrism) | 现实事件聚类、coherence gate、LLM失败回退 embedding、replay | MIT，但社区较小 | 重点参考事件聚类；不整库依赖 |
| [NewsNow](https://github.com/ourongxing/newsnow) | 中文来源适配、自适应缓存、统一接口/MCP | MIT；处于新版迁移期 | 可作部分 Source Adapter Gateway，不替代官方/政府源 |
| [RSSHub](https://github.com/DIYgod/RSSHub) | 把无 RSS 网站转为统一 feed，路线生态成熟 | AGPL-3.0 | 独立 sidecar 试点，不复制代码进核心 |
| [Miniflux](https://github.com/miniflux/v2) | ETag/Last-Modified、自适应 feed 调度、错误暂停、API/webhook | Apache-2.0 | RSS/Atom 固定源标准采集层首选试点 |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | HTTP/Playwright、CSS/XPath/JSONPath、历史 diff、逐 watch 调度 | Apache-2.0 | JS/难抓页面 sidecar；仅在 RSS/HTTP 失败时升级 |
| [Media Cloud story-indexer](https://github.com/mediacloud/story-indexer) | normalized URL hash、redirect、标题去重、模板句清理 | Apache-2.0 | 借鉴四层去重，不部署重型全套 |
| [news-please](https://github.com/fhamborg/news-please) | 新闻正文、日期、作者、语言抽取；library mode | Apache-2.0 | 用中文样本 benchmark，合格才替换 `_clean_html` |
| [GDELT](https://www.gdelt.org/) | 全球 Actor–Action–Actor 事件、实体和时间窗口 | 数据/文档公开，不是完整可嵌入 app | 未来作补充验证或历史源，不替代中文固定源 |
| [Event Registry SDK](https://github.com/EventRegistry/event-registry-python) | 文章→事件簇、跨语言事件链接、代表文章 | SDK MIT，核心后端商业闭源且数据许可受限 | 借鉴事件模型，不作为核心依赖 |
| [FollowTheMoney](https://github.com/opensanctions/followthemoney) / [nomenklatura](https://github.com/opensanctions/nomenklatura) | Person/Company/Event/Document schema、逐 statement 溯源、可回退实体消歧 | MIT | 最高优先级技术评估，避免自造实体/证据协议 |
| [LittleSis + Oligrapher](https://github.com/public-accountability/littlesis-rails) | 人—机构关系类型、方向、时间、证据、人工维护 | GPL-3.0；数据另有 CC BY-SA | 参考投资人图谱，不复制/嵌入应用 |
| [Twenty](https://github.com/twentyhq/twenty) | Company/Person/Opportunity 对象、视图、权限和工作流 | 以 AGPLv3 为主，需进一步审查 | 参考工作台对象模型，不部署第二 CRM |

## 4. 补充案例与排除结论

- [OCCRP Aleph](https://github.com/alephdata/aleph)：原始文档、实体、时间线和关系调查很强，但旧版停止官方维护并转向重写；不引入整套，只借鉴“文档永远可追溯”。
- [Huginn](https://github.com/huginn/huginn)：来源 Agent、事件信封、失败隔离设计成熟，但会与 OpenClaw/cron/Python pipeline 重复，不引入。
- [CompanyLens MCP](https://github.com/diplv/companylens-mcp)：稳定 entity ID + Agent tool 的方向正确，但提交、star 和 release 极少，只作为早期架构原型观察。
- [FreshRSS](https://github.com/FreshRSS/FreshRSS)：成熟但与 Miniflux 重复，不同时部署两套 feed reader。
- [DailyHotApi](https://github.com/imsyy/DailyHotApi)：可作为热点 API，但缺少实体、事件和业务评分，且与 NewsNow 重复。
- `aihot.tech` 与数字生命卡兹克的 AI HOT 不是同一站点，排除。

## 5. 推荐目标架构

```text
Natural-language Request
  → Market Scan / Candidate Float Planner
  → Dynamic Industry Map
  → Source Registry
     ├─ Direct HTTP collector
     ├─ Miniflux (RSS/Atom)
     ├─ RSSHub (site-to-feed sidecar)
     └─ changedetection.io (JS/change sidecar)
  → Raw SourceDocument / Snapshot
  → Article Extraction
  → URL + exact content dedup
  → Near-duplicate StoryGroup
  → CanonicalEntity resolution
  → Business Event Cluster
  → Event lifecycle + canonical evidence
  → Senior-role hypothesis
  → Explainable Top 20
  → Daily basic research
  → Backend fact store
  → Feishu projection + OpenClaw/Codex query
```

## 6. 数据模型最佳实践

### 6.1 四层事实模型

建议正式采用：

```text
SourceDocument → Statement → Event → CanonicalEntity
```

- `SourceDocument`：URL、来源、发布时间、抓取时间、摘要、hash、版权保存策略；
- `Statement`：subject/predicate/object、原文值、来源、first/last seen、valid_from/to、置信度；
- `Event`：融资、订单、产能、基地、许可、合作、技术里程碑等现实事件；
- `CanonicalEntity`：公司、人、投资机构、融资轮次、地点和产品。

归一档案只是 Statement 的投影视图，不覆盖或删除相互冲突的来源事实。

### 6.2 四层去重与事件化

```text
1. redirect / rel=canonical / tracking 参数清理
2. normalized URL + exact extracted-text hash
3. normalized title + company + date window + SimHash/MinHash
4. company + event type + structured slots + time window 的业务 Event Cluster
```

embedding 只用于边界候选。StoryGroup 回答“是否是同稿/近似报道”；Event 回答“是否在讲同一个现实商业事件”，两者不能合并成一个概念。

### 6.3 事件生命周期

- `emerging`：单一弱来源；
- `corroborated`：多个独立来源或一个 A 级来源；
- `developing`：金额、地点、合作方、产能等出现实质更新；
- `stale`：超过对应事件的活跃窗口；
- `superseded`：被更新事件替代；
- `retracted/disputed`：撤回或来源冲突。

Event merge/split 必须留审计记录并可回退。

## 7. 调度与成本控制

固定源不应全部同频抓取：

- 政府/企业新闻按6–24小时；财经快讯按30–60分钟；
- 尊重 ETag、Last-Modified、Cache-Control；
- 按过去7–30天更新频率自适应 next due time；
- 按 host 限制并发；429/5xx 使用 exponential backoff + jitter；
- 网络失败、解析失败、selector为空分别计数；
- circuit breaker 防止坏源无限重试；
- 每阶段保存 checkpoint：`collect → normalize → eventize → score → basic research → publish`；
- 同一 Event 只调用一次 LLM/Metaso；事件实质变化或用户主动深研时才重新调用；
- 批量处理 Top 20，不做20次完全独立的模型流程。

每个来源至少记录：last success、last new item、HTTP status、latency、parse yield、duplicate ratio、consecutive failures、next due、估算请求/模型成本。

## 8. 投资人图谱最佳实践

参考 FollowTheMoney 与 LittleSis：

```text
Person
  ├─ POSITION_AT → InvestmentInstitution
  ├─ LED / PARTICIPATED_IN → FundingRound
  ├─ COMMENTED_ON → FundingRound / Company
  └─ WORKED_AT / CO_WORKED_WITH → Company / Person

FundingRound
  ├─ FUNDED → Company
  ├─ LED_BY → InvestmentInstitution
  └─ EVIDENCED_BY → SourceDocument
```

“在融资新闻中发表评论”只能生成“疑似主导投资人”推断边；只有明确写明项目负责人/主导投资时才能生成事实边。每条边保存方向、关系类型、起止时间、来源和置信度。

## 9. 复用决策

### 9.1 进入技术 spike

1. Miniflux：RSS/Atom 标准采集层；
2. RSSHub：有成熟 route 的长尾网站 sidecar；
3. changedetection.io：JS和页面变化 sidecar；
4. news-please：中文正文/日期抽取 benchmark；
5. FollowTheMoney/nomenklatura：Statement schema 和实体 Resolver 可行性验证。

### 9.2 只吸收设计

- AI HOT：来源分级、canonical evidence、snapshot/changes；
- NewsPrism：事件聚类、coherence gate、fallback、replay；
- Media Cloud：URL/标题/正文分层去重；
- LittleSis：时间化、带证据关系边；
- TrendRadar/Horizon：checkpoint、缓存、低成本预筛、MCP/飞书输出。

### 9.3 明确不引入整套

- Huginn：第二编排层；
- Twenty：第二 CRM；
- Aleph：过重且旧版维护状态不适合；
- Media Cloud 全套：消息队列/搜索基础设施对当前规模过重；
- Event Registry：商业闭源和数据许可使其不适合作为核心依赖；
- CompanyLens：成熟度不足。

## 10. 对现有项目的直接修正

当前固定信源 MVP 证明了直接采集可行，但仍把 `Evidence` 基本等同于一篇网页。下一版必须在扩展更多行业前补上：

1. `SourceDocument + Statement + Event + CanonicalEntity`；
2. URL/content/story/event 四级去重；
3. canonical evidence 与独立来源支持数；
4. Event lifecycle 和 merge/split 审计；
5. 每项 Top 20 加减分引用具体 Event/Statement；
6. snapshot/change cursor 的飞书增量同步；
7. 分阶段 checkpoint/replay；
8. 来源级许可、正文保存和人物公开信息策略。

这些是本轮调研最值得进入 Implementation Plan 的内容；其余大型平台不应为了“看起来完整”而引入。
