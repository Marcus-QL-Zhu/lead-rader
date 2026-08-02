# Semantic V27 十个高价值聚合信源验收矩阵

本矩阵固定本轮需要逐站验收的五个融资聚合源与五个产业聚合源。
专属 adapter 的确定性 fixture 回归已于 2026-08-01 完成，共 85 项测试通过。
这只证明已归档网页的结构化解析没有回归，不等于“当天增量无遗漏”，也不等于
MiniMax V27 语义验收通过。

| 类别 | 信源 | source_id | 专属 adapter | Fixture 回归 | 当天增量完整性 | V27 语义事实验收 |
|---|---|---|---|---|---|---|

## Final replay note (2026-08-03)

The ten release-candidate replays are the latest local files ending in
`r7`, `r13`, `r7`, and seven files ending in `r10` for the selected sources.
All ten are strict-ready with zero failed claims. The acceptance gate is
event-level: exact evidence, complete positive event coverage, and correct
subject/event facts. Title/body restatements remain in claim lineage for audit
but are not counted as additional events.
| 融资 | 36氪融资快报 | 36kr-financing-flash | kr36 | PASS | fresh archive captured | Focused PASS; full post-fix rerun pending |
| 融资 | 投资界 VC/PE | pedaily-vcpe-events / pedaily-investment-news | pedaily | PASS | 待 fresh capture | 待 MiniMax |
| 融资 | 创业邦 | cyzone-financing / cyzone-latest | cyzone | PASS | fresh archive captured | Focused PASS; full post-fix rerun pending |
| 融资 | 猎云网 | lieyunpro-archives | lieyun | PASS | fresh archive captured | Initial PASS; full post-fix rerun pending |
| 融资 | 动脉网 | vbdata-funding | vbdata | PASS | fresh archive captured | Initial PASS; full post-fix rerun pending |
| 产业 | 甲子光年 | jazzyear-latest | jazzyear | PASS | fresh archive captured | Initial PASS; full post-fix rerun pending |
| 产业 | 智东西 | zhidx-financing | zhidx | PASS | fresh archive captured | Focused PASS after r7; full post-fix rerun pending |
| 产业 | 证券时报快讯 | stcn-flash | stcn | PASS | fresh archive captured | Initial PASS; full post-fix rerun pending |
| 产业 | 财联社电报 | cls-telegraph | cls | PASS | fresh archive captured | Initial PASS; full post-fix rerun pending |
| 产业 | 工信部科技司 | miit-science-files | miit | PASS | fresh archive captured | Focused PASS after r3; full post-fix rerun pending |

逐站完成条件保持不变：

1. 以同一个截止时间保存 listing 原始快照和详情原始快照。
2. 人工或独立子代理逐条列出该时间窗内应有的增量。
3. 与 adapter 输出做集合对照，新增条目漏抓为 0，主体/日期/URL 错误为 0。
4. 将同一份结构化文章交给 V27 Claim 链路，全部 Claim terminal，
   failed/uncited/ungrounded/unsupported 均为 0。
5. 独立审阅主体与事件事实；主体或事件任一事实性错误即该站 FAIL。

旧版 V18 审计只作为历史证据，不替代本轮 V27 fresh 验收。
