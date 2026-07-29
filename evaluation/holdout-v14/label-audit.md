# Holdout v14 独立人工标签审计

## 搜索协议

- 协议版本：`uniform-director-plus-v1`
- 严格发布日期窗口：`[2026-04-01, 2026-07-01)`
- 搜索对象：manifest 中全部 12 家公司，逐家执行完全相同的两类检索：`official_careers` 与 `public_web_search`
- 地域：仅接受工作地点明确位于中国大陆的真实职位
- 层级：仅接受 Director / Senior Director / VP / SVP / EVP / CxO，或对完整职能具有明确所有权的 functional Head
- 排除：Manager、专家、工程师、普通 Lead、Associate / Assistant / Deputy Director、Associate / Assistant / Deputy Head、AVP、Deputy VP
- 证据：必须具有可核验 URL 和精确发布日期；未扩大窗口、未推断发布日期、未降低层级或职责所有权标准

## 独立性声明

本次标注仅读取 holdout-v14 的 `evidence.json`、`manifest.json`、`README.md`、`pre-prediction-seal.json`、`pre-label-seal.json`。未读取 holdout-v14 snapshot、diagnostics、predictions、prompts，未读取 matcher / ontology 源码，也未运行 evaluator。

官方 ATS / Careers 始终优先。官方页面不能稳定回放历史发布日期时，只有在公开职位记录提供精确日期且能与官方雇主/职位记录交叉核验的情况下才纳入。任何只有标题但缺少精确日期的结果均被排除。

## 最终结果

共接受 4 个职位，覆盖 4 家公司：

| 公司类型 | 命中公司 | 接受职位 |
|---|---|---|
| startup_private | 海辰储能 | Sales Director（North Africa） |
| listed | 零跑汽车 | 质量总监（电芯） |
| foreign | 空中客车 Airbus；士卓曼集团 Straumann Group | Commercial Director Services (CDS)；Head of Legal, Greater China |

三种公司类型均有覆盖；公司类型覆盖率为 3/3。公司命中率为 4/12。

## 接受职位证据链

### 海辰储能 — Sales Director（North Africa）

- 地点与层级：职友集的海辰储能职位记录显示厦门 `Sales Director（North Africa）`，标题直接达到 Director 层级。
- 精确日期：职位记录显示 `2026-04-24`。
- 职责所有权：区域市场与客户开发、销售策略及目标、关键客户关系、市场和竞争分析，构成明确的北非区域销售所有权。
- 交叉核验：海辰储能官网 Careers 页面指向其 Moka 社会招聘站。
- 来源：https://www.jobui.com/company/17423485/salary/j/ir/

### 零跑汽车 — 质量总监（电芯）

- 地点与层级：职友集职位记录显示杭州滨江 `质量总监（电芯）`，标题直接达到 Director 层级。
- 精确日期：职位记录显示 `2026-05-28`。
- 职责边界：只依据标题确认其电芯质量职能的总监级所有权；由于公开记录未展示完整 JD，没有扩写未披露的具体质量模块。
- 交叉核验：零跑汽车官方社会招聘页确认其官方招聘渠道。
- 来源：https://www.jobui.com/company/17255871/salary/j/zhiliang/

### 空中客车 Airbus — Commercial Director Services (CDS)

- 地点与层级：Airbus 官方 Workday 职位 `JR10404645` 显示北京、`Commercial Director Services (CDS)`。
- 精确日期：AeroContact 的同一 Airbus China 职位记录显示 `05/27/2026`，标准化为 `2026-05-27`。
- 职责所有权：领导服务销售活动、报价定价、谈判和合同签署，并负责销售漏斗、年度规划以及跨商业/市场/合同团队协调。
- 官方交叉核验：https://ag.wd3.myworkdayjobs.com/en-US/Airbus/job/Commercial-Director-Services--CDS-_JR10404645-1
- 日期来源：https://www.aerocontact.com/en/aerospace-aviation-jobs/customer-support-and-services-manager-hf~1124920.html

### 士卓曼集团 Straumann Group — Head of Legal, Greater China

- 地点与层级：官方 Careers 职位 `20752` 为上海 `Head of Legal, Greater China`；职位详情显示五名直接下属，并全面负责大中华区法律事务，符合 functional Head 的显式所有权要求。
- 精确日期：Straumann 招聘人员对该职位的 LinkedIn 招聘公告 activity ID 为 `7453058671900676096`。LinkedIn activity snowflake 的高位是 Unix 毫秒时间；`7453058671900676096 >> 22 = 1776947658515`，对应 `2026-04-23T12:34:18.515Z`，标准化发布日期为 `2026-04-23`。该转换是确定性的时间戳解码，不是日期猜测。
- 职责所有权：大中华区法律风险、合同、合规、监管变化、并购和业务发展投资支持，并作为区域管理层战略法律伙伴。
- 官方交叉核验：https://careers.straumann.com/global/zh/job/20752/Head-of-Legal-Greater-China
- 日期来源：https://www.linkedin.com/posts/anh-huynh-4a541616_were-hiring-activity-7453058671900676096-akc2

## 逐公司未命中与排除说明

### startup_private

- **海光芯创**：官方与公开结果以公司介绍、校园招聘和工程类岗位为主；未发现同时满足大陆地点、Director+层级和窗口内精确日期的职位。
- **苏州星宇智能制造有限公司**：官网和公开招聘入口存在一般岗位，但没有符合层级、职责所有权及精确日期要求的职位。
- **海辰储能**：命中，见上。
- **晶瞻半导体**：公开职位主要为工程师及经理级；未找到带窗口内精确日期的合格 Director+ / functional Head。

### listed

- **汇川技术**：公开聚合结果出现研发总监类标题，但对应页面未给出可核验的窗口内精确发布日期，严格排除。
- **零跑汽车**：命中，见上。
- **岚图汽车科技有限公司**：公开结果出现“车型总监”等标题，但缺少可核验的窗口内精确发布日期，严格排除。
- **安琪酵母股份有限公司**：窗口内可见信息以校园及一般岗位为主；未发现符合全部条件的职位。

### foreign

- **空中客车 Airbus**：命中，见上。
- **士卓曼集团 Straumann Group**：命中，见上。
- **路易达孚集团 Louis Dreyfus Company**：官方和公开检索均未发现窗口内发布、地点为中国大陆且具有合格层级和所有权的职位。
- **阿尔米拉尔 Almirall**：结果以公司业务及中国市场新闻为主；没有可核验的中国大陆 Director+ / functional Head 职位。

## 关键严格排除

- 海辰储能较早的 `Project Manage Director` 公开记录显示约八个月前发布，明确在窗口外，未纳入。
- 汇川技术、岚图汽车存在看似合格的“总监”标题，但缺少精确发布日期，未纳入。
- 任何 Manager、专家、工程师、普通 Lead 或副职标题均未因公司覆盖不足而放宽。
