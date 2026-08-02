# Semantic v25 优化实施计划

- 状态：`IN PROGRESS`
- 创建日期：2026-08-01
- 适用范围：聚合信源文章到可审计 `SemanticEvent` 的语义处理层
- 上游：专用聚合信源适配器与原始网页存档
- 下游：公司信号时间线、Director+ 岗位假设、每日 Top 20
- 基线记录：`experiments/minimax-input-loop/result.json`

## 1. 目标

把现有“让 MiniMax 同时阅读长文、发现事件、拆分动作、判断主体与状态、复制证据并填写字段”的流程，重构为确定性代码与 MiniMax 各自承担擅长任务的混合系统。

最终目标不是让原始 LLM 输出看起来完美，而是让生产系统稳定生成：

1. 主体正确、事件真实、状态合理的公司事件；
2. 每项事实均可回到不可变原文 Span；
3. 单个候选失败不丢弃同篇文章已经验证的事件；
4. 长文章、多公司快讯和路线图文章分别使用合适的处理路径；
5. 语义层输出能够稳定支持后续招聘需求推理和历史职位回测。

## 2. 已确认的基线结论

五轮 MiniMax 输入实验已经确认：

- 简洁的“完整正文 + 强候选 + 单次裁决”通常优于聚簇和自由两阶段生成；
- 字段事实锁定可以显著提升稳定性；
- 标题主事件守卫与复合句原子动作拆分分别解决不同类型错误；
- 通用字符切块和自由 locator → normalizer 两阶段成本高、召回不稳定；
- Prompt-only 路线已经出现明显边际收益递减；
- 实验曾直接评审原始 MiniMax 输出，而生产代码会执行 `_ground_quote`、主体校验和字段剔除，因此必须先修正评测链路。

## 3. 必须复用的现有模块

不重复实现以下能力：

- 专用信源适配器、原始响应审计和增量状态；
- `MiniMaxSemanticProcessor._event_candidates`；
- 规则事件种子与稳定事件 ID；
- 公司名称归一化、别名和主体校验；
- `_ground_quote` 原文还原；
- 融资金额、估值、累计口径和投资方校验；
- 内容哈希、Prompt 版本缓存、幂等与死信记录；
- `backtest.py`、`calibration.py` 和现有公司类型切片；
- OpenClaw/飞书/审批/职位发布链路。

## 4. 目标架构

```text
Source Adapter
  -> Immutable CleanArticle
  -> Document Router
  -> Deterministic Atomic Claims + Source Spans
  -> MiniMax Claim Adjudication
  -> Deterministic Projection and Field Grounding
  -> Per-claim Salvage / Dead Letter
  -> Cross-source Event Clustering
  -> Company Signal Timeline
  -> Director+ Hiring Inference
```

MiniMax 最终只负责：

- 接受、拒绝或标记歧义；
- 在候选类型之间做语义裁决；
- 判断 `completed / started / target`；
- 识别一个句子是否包含多个独立动作；
- 在正文明确支持时补充规则未召回的强事件。

宿主代码负责：

- 原文 Span、引文复制和字符级真实性；
- JSON 类型、必填字段和枚举；
- 确定性金额、轮次、投资方和时间字段；
- 候选完整性、重复合并和部分失败保留；
- 缓存、重试、死信、审计与统计。

## 5. 分阶段实施

### Phase 0 — 评测与生产投影对齐

状态：`COMPLETED`

- [x] 提供可复用的 production projection API，把已有 MiniMax JSON 经过生产校验、原文还原、字段剔除、主体校验和事件归一化。
- [x] 实验器同时保存 `raw_output` 与 `projected_output`，评审默认只评价生产投影。
- [x] 原始输出问题与生产结果问题分开统计，避免把可确定性修复的问题记为模型事实错误。
- [x] 固化 `docs/semantic-event-gold-labeling-guide.md`。
- [x] 增加回归测试：空格/引号还原、字段剔除和生产流程投影一致性；历史背景、主体失败和候选未裁决继续由现有语义回归套件覆盖。

退出条件：实验回放与直接调用生产投影得到完全一致的事件集合和审计结果。

验收记录：`tests/test_aggregate_semantic_v25.py` 验证离线投影与生产 `process()` 结果一致；五轮 45 份既有输出已零调用重放，详见 `experiments/minimax-input-loop/production-projection-replay.json`。仅 4 份通过生产投影，41 份失败，其中 24 份来自整篇级 rejection 校验，证明 Phase 1/2 的架构改造确有必要。

### Phase 1 — 文档路由与原子 Claim/Span 合同

状态：`IN PROGRESS`

- [x] 新增 `document_type`：`single_company_flash`、`multi_company_bulletin`、`long_feature`、`roadmap`、`commentary`。
- [x] 适配器提供可选 item boundary；没有时由公共确定性路由器生成。
- [x] 每个候选形成稳定 `claim_id`、`span_id`、主体提示、动作提示、时间提示和原文偏移。
- [x] 标题形成待核线索，但标题永远不能作为事实证据。
- [x] 同句 `completed + started/target` 动作先确定性切分为独立 Claim。
- [x] MiniMax 新提示词要求引用 `claim_id/span_id`，宿主按引用恢复不可变原文 Span。
- [x] 新增 `LEAD_RADAR_AGGREGATE_STRICT_CLAIMS` 迁移开关；严格模式删除无引用的模型事件，并只把“模型引用 Claim”或“明确 rejection”视为模型已裁决，规则兜底不再掩盖模型遗漏。
- [x] 严格模式按未裁决 `claim_id` 做一次窄重试，只发送失败 Claim、不可变 Span 与已接受事件键。
- [ ] 在冻结后 40+20 集上达到本阶段门槛后，把生产开关从影子值 `0` 切为 `1`；切换前保留旧生产行为，但 audit 同时报告严格合同是否就绪。

退出条件：所有最终引文均由宿主从 Span 恢复；验收样本中无模型自由生成的证据文字。

### Phase 2 — 逐 Claim 验收与部分保留

状态：`COMPLETED`

- [x] 删除“任一 candidate 未映射则整篇退回 rules”的 all-or-nothing 行为。
- [x] 已通过 Claim 正常入库；失败 Claim 进入结构化 audit，并以 `semantic_claim:<claim_id>` 独立写入/解除 dead-letter。
- [x] 修复和重试只携带失败 Claim、不可变 Span 及已接收事件键，不重新发送完整文章和成功事件正文。
- [x] 同一文章的通过、拒绝、歧义和失败 ID 集合互斥且并集完整；audit 显式记录 disposition 完整性。
- [x] 长文章按 item/company/roadmap unit 处理，不再只按字符数切块。

退出条件：注入一个失败 Claim 不会改变同篇其他已验证事件；重复回放保持幂等。

验收记录：`tests/test_aggregate_semantic_v21.py` 覆盖“先保留成功 Claim，再仅重试遗漏 Claim并恢复同篇事件”；`tests/test_aggregate_semantic_cache.py` 覆盖逐 Claim dead-letter 的创建与解除生命周期。

### Phase 3 — 分层数据集与语义验收

状态：`IN PROGRESS`

- [x] 建立 40 篇严格未见正式集和 20 篇预封存备选集。
- [x] 覆盖五类文档和融资、高管变动、产能、订单、合作、技术、临床/监管、并购/上市等事件组。
- [x] 训练、校准、测试文章和公司均记录哈希并检查无重叠；正式集和备选集同时检查正文近重复与同标题泄漏。
- [x] 两名独立盲标者按固定指南生成 Gold，第三方只裁决分歧；任何一方不得读取 MiniMax 正式预测。
- [ ] 使用历史时间戳批量回放，不等待真实日历天数。

当前记录：诊断集仍只用于 Claim/Span 迁移，不参与通过判断。正式集与备选集已冻结为 `evaluation/semantic-v25/final-v1-bundle.jsonl` 和 `final-v1-manifest.json`，正式预测一次性冻结为 `final-v1-predictions.json`；正式集不得因失败调参后重跑，后续优化只能使用预封存备选集。双人盲标与第三方裁决已经完成。

正式结论：双人盲标和第三方裁决已完成，40 篇 Gold 全部通过结构校验；一次性正式评估未通过，主体准确率 18.97%、强当前事件召回 18.60%、状态准确率 80.00%，并有 110 个未裁决 candidate。失败已冻结，详见 `docs/semantic-v25-formal-v1-failure-analysis.md`。formal-v1 从此只作开发错误集，禁止重跑后宣称独立通过；v27 收敛后必须使用 reserve-v1 做一次预验，并再抓严格未见 final-v2 做最终验收。

V27 预验结论：formal-v1 上的第五轮开发结果虽达到精确率/召回率 100%，但 reserve-v1 双人盲标、第三方裁决后的唯一一次预验只达到精确率 58.33%、强当前事件召回率 21.88%、主体准确率 66.67%。这证明开发表达覆盖过窄，不能进入 final-v2 或生产。reserve-v1 的预测和 Gold 自此冻结，只作为失败审计，不读取逐条答案继续调参。补救路线固定为：从严格排除 V1 文章、近重复和公司主体的多源快照建立 `development-v2`，只在该开发集扩展 Action Claim 覆盖；冻结后再抓更晚且严格未见的 `final-v2` 做一次性最终验收。详见 `docs/semantic-v27-reserve-v1-failure-analysis.md`。

语义层硬门槛：

- 验收集编造主体或事件：0；
- 验收集无法映射回原文的最终事实：0；
- JSON/必填字段投影失败：0；
- candidate 静默遗漏：0；
- 公司主体准确率：至少 98%；
- 强当前事件召回率：至少 90%；
- `completed / started / target` 准确率：至少 90%；
- 每个文档类型和主要事件类型单独报告，不允许总体平均掩盖失败切片。

### Phase 4 — 公司时间线与招聘预测回测

状态：`PENDING`

- [x] MiniMax 招聘推理读取公司 90/180 天事件包，而不是单篇文章；生产与历史回放共用 `company-timeline-v1`，并以 parity 测试校验同一事实包得到相同哈希和 evidence。
- [x] 新增公司隔离的多月历史快照面板：同一家公司可按多个 cutoff 形成样本；每个样本只读取 cutoff 之前的非招聘证据。
- [x] 正例只允许来自可重放的精确职位原件（文件、哈希、摘录、雇主与 Director+ 证据齐全）；负例只允许来自完整覆盖预测窗口的公司职位页快照；其余一律为 `unknown`。
- [x] 冻结 `docs/historical-job-artifact-spec.md`：搜索摘要只作发现线索；职位 ID、标题、雇主、职责和发布日期必须从哈希原件按精确 span 回放。
- [x] 增加集团级 split 隔离、半开发布日期区间及“跨窗口只能 unknown”约束；负例必须拥有覆盖预测窗口每一天的完整 Director+ 归档。
- [ ] 责任映射覆盖技术、制造、供应链、质量、交付、商业化、销售、海外、监管、财务、战略和 HR。
- [ ] 继续只输出 Director+ 岗位假设；经理、专家和工程师信号可以用于内部组织判断，但不进入测试结果。
- [ ] 使用现有历史职位数据验证未来三个月公司与岗位族命中。
- [ ] 复用现有 calibration gate：至少两个主要指标优于基线，任一公司类型 Top-1 回退不超过 12.5%。
- [ ] 继续由人类判断 Top 20，不建设原生结果质量评分或自动触达。

### Phase 5 — 运维验收与三端发布

状态：`PENDING`

- [ ] 历史日批回放无未解释遗漏和高价值死信。
- [ ] 10 个高价值聚合信源逐个完成“原始列表/详情归档 → MiniMax 严格语义输出 → 独立主体/事件核对”；不得用 rules-only 输出冒充 MiniMax 验收。
- [ ] 日任务端到端预算不超过 45 分钟，确保 05:50 OpenClaw 汇报有稳定输入。
- [ ] MiniMax 调用按 article hash + prompt version 缓存；失败重试有界。
- [ ] full pytest、Ruff、compileall、diff-check、凭证扫描通过。
- [ ] 按 `AGENTS.md` 使用独立子代理做 full code review。
- [ ] 用户批准后才提交、推送、等待 exact SHA CI、部署并运行生产 smoke test。

当前记录：服务器 `/tmp/lead-radar-semantic-v25-final-raw` 已确认保留列表页、adapter fetch/post、详情页和 rules-only semantic 归档；本地独立首审发现 `nbd-vcpe-weekly`、`cena-industry-analysis`、`jiqizhixin-industry-analysis` 的正文无明显结构截断，但规则语义存在多处非公司主体和事件误判，因此三者尚未通过。`scripts/capture_dedicated_sources.py --use-openclaw-llm --strict-claims` 已补成独立 MiniMax 验收入口，正式通过仍需对新输出逐项盲核。

## 6. 明确不做

- 不通过增加长 Prompt 掩盖候选和切分问题；
- 不为了通过测试降低事实门槛；
- 不把三篇连续通过当作稳定性的充分证明；
- 不用第二个模型替代确定性证据和结构校验；
- 不重构飞书、OpenClaw、审批和猎聘发布链路；
- 不自动生成或发送 BD 触达信息；
- 不把候选人简历写入系统事实库。

## 7. 变更控制

任何新方案必须说明：复用了什么、替换了什么、增加了什么、删除了什么，以及对应的验收证据。若实验结果未通过本计划的硬门槛，只保留审计产物，不进入生产 Prompt、服务器或 GitHub。
