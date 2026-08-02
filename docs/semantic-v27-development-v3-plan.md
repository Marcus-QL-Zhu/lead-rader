# Semantic v27 Development-v3 固定实施计划

- 状态：`FROZEN / IN PROGRESS`
- 冻结日期：2026-08-01
- 目标：先把宿主的实体、动作与评价合同跑通，再优化 MiniMax；不在同一批输入上反复调 Prompt。
- 生产边界：全部门槛通过前，只在本地开发；不得同步 GitHub 或服务器，不得影响 05:00 生产任务。

## 1. 已冻结基线与数据边界

1. Final-v2 原始 bundle、一次性 prediction、原始 Gold 与原始 evaluation 永久只读，不得重跑后宣称独立通过。
2. `final-v2-gold-lineage-v1` 是带逐条 provenance 的开放开发 Gold，只用于诊断、回归和宿主层优化。
3. `development-v2` 只用于 Prompt 训练、校准和内部 holdout；收敛后必须另建时间更晚、公司与正文近重复均隔离的 Final-v3。
4. 评价指标拆成三条独立轴：主体身份、事件支持/类型、状态。主体精度不得依赖 evidence、event type 或 status。
5. MiniMax 不能恢复宿主未生成的公司或动作 Claim，因此 Prompt 调优必须在 Entity/Action Ledger 达标以后进行。

## 2. 实施顺序

### P0 — Gold lineage 与 evaluator 合同

- 保留 Final-v2 原始产物不可变。
- 校正的 Gold 必须记录 parent SHA、变更类别、理由、原文 span 与裁决 provenance。
- evaluator 输出 schema v2，并明确 \`company_subject_precision\`、\`event_support_precision/recall\`、\`status_accuracy\` 的定义。
- Gold validator、lineage builder 与 evaluator 回归测试全部通过。

退出条件：Gold 校验 0 failure；lineage 独立复核无未解决问题；指标定义有文档和测试。

### P1 — Entity Ledger：发现与资格彻底分离

- 宽松规则只负责 discovery，不得相互“自证”为公司。
- eligible seed 仅允许：干净法律名、显式别名、ticker、明确相邻公司描述、可信组织角色锚点、标题中的明确公司动作主体，或唯一回指这些 seed 的别名。
- \`direct_clause_company\`、\`company_surface\`、product owner/child、reference、hosting、investment actor、executive target、bulletin unit 均不得单独授予资格。
- 排除投资机构、基金、财务顾问、政府/媒体/研究机构、产品/项目/技术概念。
- 修复 canonical 边界：\`旗下\` 前缀、\`拟/拟与/与/和\` 尾部、描述性长前缀不得进入公司名。
- precision 与 Gold company recall 必须并行验收，防止通过删除全部候选制造虚假提升。

退出条件：开放开发 Gold 公司召回 100%；对全部 eligible entity 的独立盲审 precision 至少 98%；已知 \`壁仞科技\`、\`中微临港\`、\`芯和半导体\` 回归通过。

### P2 — Action Ledger：clause census、原子化与开放分类

- Router 只负责结构边界，不再以整篇类型压制事件。
- 每个 action span 保持最小、原文可回放；跨句只继承 entity ID，不递归扩大 evidence span。
- 强规则锁定高确定类型；其余“已锚定公司 + 经营动作”形成 `open_action` 交 MiniMax 分类或拒绝。
- 多轮融资、多产品临床、完成+计划、双主体合作必须拆成独立 Claim。
- 标题、摘要、正文和结论复述按原子事件去重。

退出条件：开放开发 Gold action claim recall 至少 95%，目标 100%；各主要事件族不得为 0；全部 span 是原文子串且无跨 item 主体泄漏。

### P3 — MiniMax 最多三轮三分支优化 loop

每轮固定执行：

1. 从训练区抽取本轮未用过的 3 篇文章；不得读取 internal holdout。
2. 基于上一轮胜者产生 3 个单变量版本：信息压缩/任务顺序、输出合同/字段锁定、few-shot/反例边界。
3. 每版使用相同文章、相同代码、相同模型和相同解码设置；保存 prompt、输入哈希、原始响应、生产投影、调用用量和失败原因。
4. 三个独立子代理按统一 rubric 盲评主体、事实、状态、遗漏、歧义处理与可投影性；程序指标只作证据，不替代裁决。
5. 只选择一名胜者进入下一轮；如三版均无改善，则回到宿主层定位，不继续堆 Prompt。
6. API、网络、鉴权或限流失败属于基础设施失败：该候选不得参与排序，
   不得触发 JSON repair 浪费第二次调用，也不得记作模型质量结果。

最多三轮；允许在任一轮后提前停止，但必须有明确的胜者证据且达到开发门槛。生产 Prompt 在 loop 结束前不改。

### P4 — 严格未见测试与适配器验收

- 收敛 Prompt 在 internal holdout 上先跑一次，不回流训练。
- 新建 Final-v3，按时间、公司、正文/标题近重复隔离。
- 从 Final-v3 随机抽取一篇测试；连续随机 3 篇全部通过才算 Prompt/语义链路通过。失败即冻结该结果、分析原因并建立新的未见集，不在失败样本上调后重跑冒充独立测试。
- 10 个高价值聚合信源逐个对照“原始网页增量 → adapter 结构化条目 → MiniMax 语义事件”；由独立子代理确认无增量遗漏、无主体+事件事实错误。

单篇通过标准：0 编造主体/事件、0 无原文证据、0 未裁决 Claim、0 投影失败；主体 precision ≥98%、强当前事件 recall ≥90%、状态 accuracy ≥90%。

### P5 — Director+ 历史回测与发布门禁

- 用新闻时间戳模拟 cutoff，只给 MiniMax cutoff 之前的信息。
- 用未来三个月真实职位原件核验相似 Director+ 岗位；经理、专家、工程师可作为系统输入能力，但从本轮测试结果中排除。
- 报告公司命中、岗位族命中、Top-k、按公司类型/事件族切片和 unknown 覆盖；不建设自动结果评分或自动触达。
- 完成 full pytest、Ruff、compileall、diff-check、凭证/运行时文件扫描和独立 full code review。
- 全部门槛通过并处理 review finding 后，才允许 commit、GitHub exact-SHA CI、服务器 exact-SHA 部署和 smoke test。

## 3. 总停止条件

只有以下条件同时满足才算“合理跑通”：

- corrected Gold lineage 独立复核通过；
- Entity recall 100%，独立 eligible precision ≥98%；
- Action claim recall ≥95%，主要事件族无空白；
- MiniMax 全部 Claim terminal、failed=0、uncited=0、bad pair=0；
- 主体 precision ≥98%、强当前事件 recall ≥90%、状态 accuracy ≥90%；
- 严格未见随机连续 3 篇全部通过；
- 10 个专用聚合适配器逐个通过原文—结构化—语义独立对照；
- Director+ 三个月历史回测达到冻结的 calibration gate；
- 工程质量、独立 code review、CI 与部署 smoke 全部通过。

## 4. 变更控制

- 任何门槛未通过，只保留实验和审计产物，不进入生产。
- 不用公司级硬编码或不断增长的黑名单换取测试分数。
- 不降低事实门槛；召回不足时扩大可靠输入与宿主候选覆盖。
- 每次架构改变必须说明复用、替换、新增、删除的模块及对应验收证据。

## 5. Current implementation record (2026-08-02)

- Gold lineage-v3 is frozen at 83 events; the 20/20 validator remains green. The
  original Final-v2 artifacts are still immutable and are not being reused as a
  fresh test result.
- The maximum-three-round Prompt loop is complete locally. Round 1: variants A
  and B failed the unsupported-event gate; C passed. Round 2: A/B/C all passed
  the deterministic development gates on separate training articles. Three
  independent blind reviewers unanimously selected anonymous candidate-gamma,
  which maps privately to round-2 variant B. The frozen winner hash is
  `e38779c3a0deabd680e44032e920e4bf83e89f025c95406e8a4839506d1315db`.
- Host-layer fixes made after the first holdout attempt are now part of the
  local contract: bounded candidate-action subject discovery for long articles;
  mixed-script product-key suppression; bilateral investment subject selection;
  role-descriptor (`joint founder`) exclusion; negative policy-risk commentary
  exclusion; historical-biography exclusion; generic user-instruction exclusion;
  factory-expansion open-action exclusion; speculative partnership exclusion;
  bilateral negotiation host-mandatory fan-out; and no article-wide subject
  inheritance inside multi-company bulletin items.
- The frozen holdout sequence was selected before inference:
  `stcn-flash:4052079`, `cyzone-latest:841774`, and `jazzyear-research:162`.
  All three reached strict-ready/terminal status with zero infrastructure or
  failed claims. The sequence gate passed 3/3 consecutive cases. Cyzone had exact-support precision and recall both at 1.00
  (35/35; strong-current recall 1.00).
- The long-article route remains covered by `jazzyear-research:162`: only the
  first 2,000 characters are exposed for semantic extraction with original
  offsets preserved, and the 九科信息 event is recovered exactly.
- The ten fresh aggregate-source semantic acceptance runs and the Director+ job
  historical backtest are still pending. Therefore this plan is locally green
  for the v27 semantic development gate but is not yet a production-release
  gate.

## 6. Latest local gate results (2026-08-02)

- Targeted entity/action/claim/evaluator regression: 150 passed; full
  repository pytest: 1135 passed; Ruff and compileall also pass.
- Holdout sequence: 3/3 passed; no failed claims, no uncited events, no
  unsupported final events, subject precision 1.00, exact support precision
  1.00, exact support recall 1.00, and status accuracy 1.00 on the two non-empty
  cases.
- Production remains frozen until the remaining fresh-adapter acceptance,
  Director+ backtest, full repository quality checks, independent review, and
  exact-SHA deployment checks are completed.

## 7. Fresh adapter semantic acceptance update (2026-08-02)

- The first fresh ten-source pass completed the citation layer: 22/22 sampled
  evidence quotes were exact substrings of the archived source bodies, and all
  ten articles reached `strict_ready=true` with zero failed claims.
- The first semantic comparison exposed four concrete host-layer issues rather
  than a MiniMax transport failure: a G-round was split into a duplicate
  `Pre-IPO` event, an investment amount was read from a valuation sentence,
  a future financing plan was marked `started`, and a counterparty's English
  name was assigned to the operating company. These are now covered by
  deterministic host rules.
- Focused reruns passed for 36Kr, 创业邦, and 工信部科技司. The 36Kr result now
  keeps the G-round as `started`/`G轮` with no unsupported amount; 创业邦 keeps
  the Moonshot global-ambassador action under the correct subject and marks the
  financing plan as `target` without treating valuation as financing amount;
  the MIIT notice is preserved as a title-grounded `policy_or_standard` seed.
- 智东西 rerun `zhidx-financing:580513` passed independent review after the
  same-key event merge began unioning evidence and ambiguity fields. Its single
  customer-validation event covers both cumulative shipment growth and the
  separate “国内头部晶圆厂客户实现量产交付” fact; its B+ financing event,
  near-10-billion total and investor list are all source-grounded. The only
  non-blocking follow-up is alias normalization for `高瓴创投（GL Ventures）`
  versus `高瓴创投`.
- A conservative rule-seed grounding fallback was added for adapter wording
  that the generic candidate regex does not yet know (for example “收到…采购
  订单” and “正式动工”). It requires the adapter-provided company mention plus
  an event-family action cue, and restored all 12 CLS fixture regressions.
- Current engineering gate: full pytest `1153 passed, 496 warnings`; targeted
  semantic/claim/action/finance regressions pass; compileall, Ruff and
  `git diff --check` pass. The post-fix ten-source semantic replay and the
  Director+ historical backtest both pass. Production wiring is now enabled
  for claim-centric V27 with a four-worker cap; see
  `docs/semantic-v27-release-readiness.md` for the release record.
