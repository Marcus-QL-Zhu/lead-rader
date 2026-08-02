# Semantic v25 验收数据

## 当前结论

`shadow-diagnostic-manifest.json` 只用于 Claim/Span 迁移、评测器开发和错误分布分析，不能用于宣布 Semantic v25 最终通过。

原因：源数据库 `.acceptance/server-v23-live.sqlite` 的 1,511 篇文章均已有历史 semantic attempt；当前诊断抽样也只覆盖 `single_company_flash` 和 `long_feature` 两类路由。因此它既不是严格未见数据，也没有达到五类文档覆盖要求。

## 最终集冻结要求

最终集版本固定为 `semantic-v25-final-v1`，只能从 Claim/Span 合同、Gold 指南和生产投影全部冻结以后新抓取的文章中产生：

- 正式集 40 篇：五种文档类型各 8 篇；
- 预封存备选集 20 篇：五种文档类型各 4 篇；
- 至少 8 个不同来源，单一来源最多 6 篇；
- 至少覆盖融资、高管变动、产能、订单/采购/客户、合作、技术、监管/临床、并购/上市八组事件；
- 至少 8 篇包含需要拆分状态的复合事件；
- 至少 10 篇包含历史背景、能力描述、资金用途或行业泛化等 hard negative；
- 正式集与既有数据库、MiniMax 五轮实验、校准集之间不得出现文章、近重复正文、公司或事件簇泄漏。

## 去重顺序

1. `source_id + source_article_id`；
2. 内容哈希；
3. 规范化 canonical URL；
4. NFKC 并去除标点空白后的正文 SHA-256；
5. 7-character shingles，Jaccard `>= 0.85` 进入同一近重复簇；
6. 相同规范公司、事件日期、事件类型及金额/标题的事件簇人工复核。

多公司文章只要任一 Gold 主体与开发/校准公司重叠，整篇退出正式集。所有公司别名必须在标签打开前固定。

## Gold 与开标签顺序

Gold 遵守 `docs/semantic-event-gold-labeling-guide.md`，并保存 article、candidate disposition、gold event 和 annotation audit 四层记录。证据必须是连续原文 span；歧义样本不能作为硬正例。

顺序不可颠倒：

1. 冻结代码、Prompt、router、candidate、projection 和门槛；
2. 新抓取并封存 40+20 篇文章及原始响应；
3. 冻结预测输出；
4. 两名独立标注者生成 Gold，分歧由第三方裁决；
5. 一次性运行正式集；
6. 失败后只能使用预封存备选集开启下一轮，不能在同一正式集上调参后重新声称独立验收。

## 硬门槛

- 编造公司或事件：0；
- 无法映射回原文的最终事实：0；
- JSON/必填字段生产投影失败：0；
- candidate 静默遗漏：0；
- 公司主体准确率至少 98%；
- 强当前事件召回率至少 90%；
- `completed / started / target` 状态准确率至少 90%；
- 每种文档类型和主要事件类型单独报告，不得用总体平均掩盖失败切片。

`cumulative` 不是独立 Gold 状态；累计口径只进入 `cumulative_funding_amount`。最终 Gold 状态仅允许 `completed / started / target`。
