# 融资信源扩容与 10 项随机覆盖审计

日期：2026-07-25  
适用范围：中国科技与硬科技公司的公开股权融资；同时保留全球科技融资补漏能力。

## 1. 结论

不存在能够稳定覆盖“所有融资”的单一公开网站。未官宣交易、只在付费数据库出现的交易、只在朋友圈或封闭公众号披露的信息，本身就不属于公开网络的可观测全集。Dealroom 的服务条款也不保证数据正确或完整；Crunchbase、PitchBook、IT 桔子等数据库的自动化使用必须通过正式 API/Data Feed 许可，不能用登录态网页抓取替代。

本轮将系统从原来的两个主要融资媒体入口扩展为以下组合：

1. **中国综合发现层**：36氪融资快报、投资界投资事件、投资界融资资讯、创业邦融资频道与最新资讯、猎云网归档。
2. **中国垂直补漏层**：动脉网投融资、甲子光年、智东西融投资消息。
3. **一手反向发现/核验层**：启明创投新闻中心，以及事件发现后再打开的公司官网、投资机构官网。
4. **全球公开补漏层**：TechCrunch Venture RSS、Crunchbase News Venture RSS。
5. **按需全网核验层**：Metaso，只在抽样审计、主动深研或 Float 时使用。
6. **可选结构化数据层**：取得合同后接入烯牛/CVSource/IT桔子中的至少两个中国库，以及 Dealroom/Tracxn 中的一个全球库；当前没有假设或绕过任何商业许可。

代码中的固定来源由 **29 个增至 40 个**，默认启用来源由 **19 个增至 30 个**。其中可发出融资信号的固定来源为 13 个。本轮生产健康检查中，这 13 个来源全部返回 `ok`，没有失败。

## 2. 已启用的融资渠道

| 层级 | 来源 | 固定入口 | 作用 | 生产验证 |
|---|---|---|---|---|
| 综合 | 36氪融资快报 | https://pitchhub.36kr.com/financing-flash | 近期科技融资、首发 | 63 个链接，正常 |
| 综合 | 投资界投资事件 | https://www.pedaily.cn/vcpeevent/ | 逐笔投资事件 | 70 个链接，正常 |
| 综合 | 投资界融资资讯 | https://www.pedaily.cn/i2826/ | 新闻与融资背景 | 58 个链接，正常 |
| 综合 | 创业邦融资频道 | https://capital.cyzone.cn/ | 融资频道与历史分页 | 64 个链接，正常 |
| 综合 | 创业邦最新资讯 | https://www.cyzone.cn/ | 近期单笔融资补充 | 204 个链接，正常 |
| 综合 | 猎云网归档 | https://lieyunpro.com/archives | 创业公司与硬科技补漏 | 130 个链接，正常 |
| 垂直 | 动脉网投融资 | https://www.vbdata.cn/articleList?tag=5512 | 医疗、脑科学、生物技术 | 32 个链接，正常 |
| 垂直 | 甲子光年 | https://www.jazzyear.com/ | AI、机器人、硬科技 | 79 个链接，正常 |
| 垂直 | 智东西融投资 | https://zhidx.com/p/category/%E8%9E%8D%E6%8A%95%E8%B5%84%E6%B6%88%E6%81%AF | 半导体、AI 硬件 | 42 个链接，正常 |
| 机构官方 | 启明创投新闻中心 | https://www.qimingvc.com/cn/newsroom?news_tid=All | 反向发现、一手投资事实 | 87 个链接，正常 |
| 全球 | Crunchbase News Venture RSS | https://news.crunchbase.com/sections/venture/feed/ | 全球融资新闻补漏 | 10 条 Feed，正常 |
| 全球 | TechCrunch Venture RSS | https://techcrunch.com/category/venture/feed/ | 全球科技融资补漏 | 19 条 Feed，正常 |
| 财经快讯 | 证券时报快讯 | https://www.stcn.com/article/list/kx.html | 上市主体、产业投资补充 | 92 个链接，正常 |

注意：

- Crunchbase News 的公开 RSS 只是新闻源，不等同于 Crunchbase 商业数据库授权。
- 甲子光年首页和猎云归档混有非融资内容，继续依赖事件分类、日期窗口和跨源聚簇。
- TechCrunch/Crunchbase News 已补充英文融资事件识别；中文“融资”通用扫描会收录全球融资，行业扫描则仍需命中相应英文行业词。
- 交易所和巨潮仍属于“已确认入口、专用适配器未完成”，不能误报为已自动采集。

## 3. 复用了哪些项目

调研没有找到一个维护活跃、合规且能全量覆盖中国融资新闻的成品爬虫，因此没有引入脆弱的“融资爬虫大一统”项目。采用的是分层复用：

| 项目 | 复用内容 | 当前决定 |
|---|---|---|
| [RSSHub](https://github.com/DIYgod/RSSHub) | 将无原生 Feed 的站点转为 RSS、缓存与路由生态 | 后续自托管备援；源站直抓仍保留 |
| [Miniflux](https://github.com/miniflux/v2) | ETag/Last-Modified、游标、失败重试、Webhook | 后续作为 Feed 状态机，不重复造 scheduler |
| [gdeltdoc](https://github.com/alex9smith/gdelt-doc-api) | GDELT DOC 2.0 查询封装 | 作为全球高召回补漏候选；本轮直测遇到 429，未贸然启用 |
| [news-please](https://github.com/fhamborg/news-please) | 通用新闻正文、日期、作者抽取 | 作为正文 fallback 候选 |
| [Fundus](https://github.com/flairNLP/fundus) | publisher-specific parser 与抽取 benchmark | 核心媒体模板频繁失效时再引入 |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | 无 Feed 的公司/投资机构官网变更监控 | 用于高价值官网长尾，不扫全网 |
| [Firecrawl](https://github.com/firecrawl/firecrawl) | JS 页面与按需结构抽取 | 只用于主动深研/Float，不进每日热路径 |
| [Fire Enrich](https://github.com/firecrawl/fire-enrich) | 多 query 并行、保留 citations、最终合并 | 复用编排思想，不引入整套应用 |
| [Huginn](https://github.com/huginn/huginn) | 事件 DAG、重放与失败隔离 | 只借鉴设计；现有 OpenClaw + cron 已覆盖编排 |
| [Crunchbase4](https://github.com/ekohe/crunchbase4) | FundingRound/Investor 领域模型 | 只作未来持牌 API adapter 参考 |
| [FreshRSS](https://github.com/FreshRSS/FreshRSS) | Feed 聚合与人工阅读工作台 | 与 Miniflux 二选一，当前偏向 Miniflux |
| [VC Investment Data Extractor](https://gist.github.com/alexfazio/2671a628e4b10e08974aea4f561981ae) | Map → 文章 URL → 融资 schema 抽取 | 只复用流程和测试 schema，不部署原型 |

学术上的公开网络融资预测研究也说明，公开网页信息能补充商业数据库，但两者并非互相替代：[Where Do You Want To Invest?](https://arxiv.org/abs/2204.06479)。Fundus 的 ACL 系统论文则支持为高价值媒体维护显式 publisher parser，而非无限堆叠脆弱正则：[Fundus: A Simple-to-Use News Scraper](https://aclanthology.org/2024.acl-demos.8/)。

## 4. Metaso 随机 10 项验证

### 方法

- 候选池来自 36氪融资快报、投资界投资事件、创业邦最新资讯、猎云网归档、动脉网投融资，共 65 个通过过滤的近期融资条目。
- 排除“未融资”、证券两融、基金募资、月报/周报和汇总稿。
- 固定随机种子 `20260725`，抽取 10 项，保证可复现。
- 每项调用 1 次 Metaso，查询投资方、领投、跟投及官方证据。
- 实际调用 10 次，按保守值计费 60 积分；当日项目台账剩余 30/90，Metaso 供应商硬上限仍为 500。

### 结果

| # | 项目/轮次 | Metaso 核验后的公开投资方 | 固定来源覆盖 |
|---|---|---|---|
| 1 | 元码智药 Pre-A | 倚锋资本（倚锋灼华基金）、英矽智能 | **完整**；投资界正文包含两方 |
| 2 | 博顿光电 B+ | 毅达资本、中信建投资本、达武创投、友谊时光、南控基金 | **完整**；36氪正文包含五方；IO资本为财务顾问，不计投资方 |
| 3 | PrimalVerse/元昊动力 种子轮 | 联想之星、银杏谷资本、啟赋资本、卓源亚洲、精锋医疗 | **完整**；36氪正文包含五方 |
| 4 | 光邮星空 Pre-A / Pre-A+ | 元起资本；九合创投、同创伟业、中关村科学城 | **完整**；投资界正文区分两轮 |
| 5 | 蚂蚁国际 A轮 | 蚂蚁集团、阿里巴巴、未具名的多家国际投资机构 | **部分可观测**；固定来源与 Metaso 都只能确认两个具名股东，发行方未公开其余机构名称 |
| 6 | 屿西半导体 A+ | 成都市科创投、龙江基金 | **完整**；猎云正文/同轮转载一致 |
| 7 | 南京清普生物 C轮 | 联合领投：江苏高投、倚锋资本、阳光融汇资本、元禾控股；跟投：广药资本、江苏农垦生物技术基金、中信建投资本、泰珑投资、广东中医药大健康基金、国信弘盛、广州产投资本、湖南财信产业基金、中银资本、国聚创投；老股东追加：南京创新投资集团、盛景嘉成 | **完整**；动脉网正文包含全部 16 个具名参与方 |
| 8 | 西湖欧米 战略投资 | 瑞江投资旗下瑞江康圣基金 | **完整**；动脉网正文明确独家战略投资 |
| 9 | 两仪万象 A+ | 领投：君联资本；跟投：基石创投、上海科创、中信建投投资、海棠基金；追加：顺为资本、科大讯飞 | **完整**；36氪正文包含七方 |
| 10 | 月泉仿生 Pre-A+ | 长发基金、华控基金、华夏基金、国力民生、星科创投基金；老股东中关村启航投资 | **完整**；36氪正文包含六方 |

审计结论：

- **事件覆盖：10/10。**
- **所有公开具名投资方覆盖：10/10。**
- **完整法律参与方名单：9/10 可确认完整，1/10（蚂蚁国际）因发行方只披露“多家国际投资机构”而不可观测。**
- 因此不能宣称“所有投资方 100% 覆盖”；该缺口不是新增媒体可以解决的普通漏报，而是源头未披露。

原始、未改写的 Metaso 搜索结果保存在 `reports/funding-coverage-benchmark-2026-07-25.json`，包含每个查询、标题、URL、摘要和日期，便于复核轮次混淆。

## 5. 生产运行设计

每日 05:00 的基础研究继续只做：

1. 轮询固定融资列表与 RSS；
2. 解析公司、轮次、金额、投资方；
3. 公司别名归一；
4. 按公司 + 轮次 + 时间窗口聚簇；
5. 保存最小证据摘录、原文 URL 与来源等级；
6. 将融资作为招聘需求的上游信号，不自动生成触达话术，不发送消息。

主动深研或 Float 才做：

1. 搜索公司官网和本轮投资机构官网；
2. 查找为本次投资发表评论的人；
3. 若无人名，定位该机构对应赛道的合伙人/MD；
4. 复用并更新投资人图谱；
5. 查找企业 Hiring Manager、HR 和创始团队公开履历。

## 6. 尚未完成

以下内容没有伪装成“已完成”：

1. 自托管 RSSHub + Miniflux 尚未部署；当前使用源站公开 HTML/RSS 和已有增量状态库。
2. Top 50 硬科技投资机构 newsroom/portfolio 注册表尚未系统化，只先启用启明创投作为生产样例。
3. 公司官网 newsroom 自动发现与 adapter 缓存尚未完成。
4. GDELT 生产适配器尚未启用；本轮服务器直测返回 429，必须先做限频、缓存和退避。
5. 巨潮、沪深北交易所公告专用适配器尚未完成。
6. 烯牛、CVSource、IT桔子、Dealroom、Tracxn、Crunchbase、PitchBook 均未签署或接入正式 API/Data Feed。
7. 当前抽样是一次 10 项横截面，不能替代持续的周/月回溯审计。后续应固定记录事件召回、具名投资方完整性、发现延迟和来源故障，而不是承诺不可验证的“绝对全量”。
