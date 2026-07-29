# Holdout v12 独立标签搜索审计

## 范围与规则

- 公司范围：`manifest.json` 中全部 12 家公司，逐家公司采用相近的官方招聘站、官方 ATS、精确职级词与公开招聘归档查询。
- 地域：中国大陆。
- 日期窗口：`[2026-04-01, 2026-07-01)`；不向前或向后扩窗。
- 接受职级：真实 `Director+`、`VP`、`CxO`，或职责明确的 functional Head。
- 排除职级：Manager、Expert、Engineer、普通 Lead、Associate Director / 副总监、混合或模糊职级，以及职责不明确的 Head。
- 日期规则：只接受可由公开页面核验的精确发布日期；“数日前”“1 个月前”等相对日期不能单独作为接受依据。
- 来源优先级：公司官方 ATS / 招聘官网优先；LinkedIn 公开职位页等只用于补充精确日期或核对归档。

## 搜索结果

| 公司 | 命中数 | 结果 |
|---|---:|---|
| GE HealthCare（GE医疗） | 2 | `MA Director`（2026-05-08）；`Customs & Trade Compliance Director – China`（2026-06-10） |
| Agilent Technologies（安捷伦） | 1 | `Director of China Innovation Center`（2026-05-19；原始官方标题，后续 live page 改名见下文） |
| Nissan Motor（日产汽车） | 0 | 严格空结果 |
| PepsiCo（百事公司） | 0 | 严格空结果 |
| Nestlé（雀巢） | 0 | 严格空结果 |
| Swire Coca-Cola（太古可口可乐） | 0 | 严格空结果 |
| ebm-papst（依必安派特） | 0 | 严格空结果 |
| SKF | 0 | 严格空结果 |
| Solvay（索尔维） | 0 | 严格空结果 |
| Clariant（科莱恩） | 0 | 严格空结果 |
| Air Liquide（液化空气集团） | 0 | 严格空结果 |
| 幸星电子（泰州）有限公司 | 0 | 严格空结果 |

## 逐家公司审计

### 1. GE HealthCare（GE医疗）

- 查询范围：官方 Workday 中国 Director 岗、上海/北京 Director 岗、`MA Director`、LinkedIn 公开归档。
- 官方来源：
  - [MA Director](https://gehc.wd5.myworkdayjobs.com/en-US/gehc_externalsite/job/Shanghai/MA-Director_R4040924-1)
  - [Customs & Trade Compliance Director – China](https://gehc.wd5.myworkdayjobs.com/en-US/GEHC_ExternalSite/job/Customs---Trade-Compliance-Director---China_R4042035)
- 命中：
  - `MA Director`，上海，官方结构化 `datePosted = 2026-05-08`。职责包括领导 4 人团队、负责华东八省全业务单元市场准入、跨 BU 带量采购项目和政府关系，满足 Director 和真实区域职能领导标准。
  - `Customs & Trade Compliance Director – China`，上海/北京，官方结构化 `datePosted = 2026-06-10`。职责包括搭建并领导中国贸易合规组织、制定中国战略、负责海关/进出口监管、审计与政府代表工作，满足 Director 标准。
- 主要未命中：
  - `Regulatory Affairs Director`：官方日期 2026-03-12，早于窗口。
  - `GA Senior Director`：官方日期 2026-07-03，晚于窗口。
  - `Director, East Asia Strategy and Growth Initiatives`：没有取得可接受的官方精确窗口内发布日期；后续 LinkedIn 日期为 2026-07-09。
  - `Strategic Project Director`：窗口后发布。
  - `China Supply Chain Strategy Director` 搜索归档对应的 live requisition 已改为 Senior Manager 且窗口后发布，未接受。

### 2. Agilent Technologies（安捷伦）

- 查询范围：官方 Workday 中国 Director / Head、上海 Director、LinkedIn 公开职位归档。
- 官方来源：[Director of China Innovation Center](https://agilent.wd5.myworkdayjobs.com/en-US/Agilent_Careers/job/China-Remote-Location-Shanghai/Director-of-China-Innovation-Center_4037976)
- 命中：
  - 原始官方标题 `Director of China Innovation Center`，上海，requisition `4037976`，官方结构化 `datePosted = 2026-05-19`。
  - 原始官方页面索引和 canonical URL slug 均明确记录 `Director of China Innovation Center`。职责包括制定中国创新战略、作为中国创新对全球的统一代表、领导约 16 人的多学科团队、外部创新生态合作、产品化/全球放大和 KPI 治理，满足 Director/functional-head 实质标准。
  - 审计披露：live page 后续把显示标题改为 `China Innovation Center Leader`，但保留同一 requisition、原始日期和带有原始 Director 标题的 canonical URL。本标签保存原始发布标题，没有把一个新出现的普通 Lead 岗位降标纳入。
- 主要未命中：
  - `Head, Global Supply Planning`：窗口前旧岗位，申请日期文字指向 2026-03-31。
  - 其他 Director 搜索结果位于中国大陆以外。

### 3. Nissan Motor（日产汽车）

- 查询范围：日产中国官方招聘页、官方职位搜索、Nissan/日产中国 Director 与 Head 的 LinkedIn 公开归档。
- 来源：[日产中国招聘页](https://www.nissanmotor.jobs/apac/ch/)。
- 结果：没有找到同时满足中国大陆、精确窗口日期和严格职级的岗位。
- 处理：不猜测、不以公司投资或组织活动代替真实职位，记为严格空结果。

### 4. PepsiCo（百事公司）

- 查询范围：PepsiCo 中国官方招聘站、上海/中国 Director 与 Head、LinkedIn 百事中国职位归档。
- 来源：[PepsiCo China](https://www.pepsicojobs.com/china) 与 [China locations](https://www.pepsicojobs.com/china/jobs/locations?lang=en-US)。
- 结果：未找到可接受岗位。
- 主要排除：多个 `Associate Director / 副总监`；`Revenue Management Manager` 等 Manager；`Global Executive Talent Acquisition Lead` 等普通 Lead 标题。均按规则排除。

### 5. Nestlé（雀巢）

- 查询范围：Nestlé 官方 jobdetails/全球职位搜索、上海/中国 Director 与 Head、LinkedIn 公开归档。
- 来源：[Nestlé jobs](https://www.nestle.com/jobs/search-jobs)。
- 结果：没有找到可核验精确窗口日期的中国大陆 Director+/明确 functional Head，记为严格空结果。

### 6. Swire Coca-Cola（太古可口可乐）

- 查询范围：Swire 官方 careers、Swire Coca-Cola 官网、太古可口可乐中国 Director/总监公开职位归档。
- 来源：[Careers at Swire](https://careers.swire.com/en) 与 [Swire Coca-Cola](https://www.swirecocacola.com/)。
- 结果：未找到中国大陆严格命中。
- 主要排除：`Cyber Security Director` 位于香港特别行政区；中国大陆结果为 Senior Manager、主管、代表等较低职级。

### 7. ebm-papst（依必安派特）

- 查询范围：ebm-papst 官方 jobs、China Director/Head、依必安派特总监公开职位。
- 来源：[ebm-papst jobs](https://jobs.ebmpapst.com/) 及 LinkedIn 中国公开职位。
- 结果：公开中国岗位主要为物联网 IT 专员、IT 软件应用工程师等；没有严格职级和精确窗口日期同时成立的岗位。

### 8. SKF

- 查询范围：SKF 官方中国职位入口、Shanghai Director/Head、LinkedIn 斯凯孚中国公开职位。
- 来源：[SKF Jobs](https://career.skf.com/)。
- 结果：未找到窗口内严格命中。
- 主要排除：`Global Industry Director – Wind` 为 2024 年岗位；`Head of Controlling, APAC – Automotive` 为约一年前旧岗位。

### 9. Solvay（索尔维）

- 查询范围：Solvay 官方 job opportunities、中国/上海 Director 与 Head、LinkedIn 精确职位页。
- 来源：[Director Government & Public Affairs China and APAC](https://www.solvay.com/en/career/job-opportunities/33661) 和 [LinkedIn exact-date page](https://cn.linkedin.com/jobs/view/director-government-public-affairs-china-and-apac-f-m-x-at-%E7%B4%A2%E5%B0%94%E7%BB%B4-4422774578)。
- 结果：该岗位本身职级和职责符合，但精确发布日期为 2026-07-07，晚于窗口，严格排除；没有扩窗。

### 10. Clariant（科莱恩）

- 查询范围：Clariant 官方 careers、上海/中国 Director 与 Head、LinkedIn 科莱恩公开职位。
- 来源：[Clariant Careers](https://careers.clariant.com/)。
- 结果：未找到严格命中。
- 主要排除：[Procurement Manager](https://careers.clariant.com/job/Shanghai-Procurement-Manager/1375530533/) 及其他 Manager/专家/工程师岗位均低于标准。

### 11. Air Liquide（液化空气集团）

- 查询范围：Air Liquide 官方 Workday 中国 Director/Head、LinkedIn 液空中国 Director 归档。
- 来源：
  - [Air Liquide external careers](https://airliquidehr.wd3.myworkdayjobs.com/AirLiquideExternalCareer)
  - [Director of Business Development](https://cn.linkedin.com/jobs/view/director-of-business-development-at-%E6%B6%B2%E7%A9%BA-4379759281)
  - [Business Development Director/Senior Manager](https://airliquidehr.wd3.myworkdayjobs.com/en-CA/AirLiquideExternalCareer/job/China-Shanghai/Business-Development-Director-Senior-Manager_R10086146)
- 结果：未接受。
- 主要排除：
  - `Director of Business Development` 精确发布日期 2026-03-27，早于窗口。
  - `Business Development Director/Senior Manager` 为显式混合职级，不能确认按 Director 招聘；严格排除。

### 12. 幸星电子（泰州）有限公司

- 查询范围：公司名 + 总监/负责人/总经理、智联公司职位页、泰州公开职位。
- 来源：[智联公司页](https://www.zhaopin.com/companydetail/jobs-CZ215338910/)。
- 结果：当前公司页仅显示韩语翻译岗位；没有中国大陆精确窗口日期的 Director+/VP/CxO/明确 functional Head，记为严格空结果。

## 工具与独立性声明

- 已按要求优先使用官方 ATS / 官方招聘官网，并用公开搜索与 LinkedIn 公开职位页补充历史标题或精确日期核验。
- `browser-act` 技能规定的 CLI 在本环境中不可用，因此没有安装或绕过；改用只读公开网页与官方页面元数据完成核验。
- 本次搜索只读取了任务明确允许的 v12 文件：`evidence.json`、`manifest.json`、`README.md`、`pre-prediction-seal.json`、`pre-label-seal.json`。
- 本次没有读取 `.acceptance/holdout-v12.snapshot.json`，没有读取任何 holdout-v12 diagnostic/report，没有读取模型预测、预测评估结果、系统提示词或源码中的预测内容，也没有运行预测评估。
- 未降低标准、未猜造岗位、未因找不到结果而扩展日期窗口。

## 汇总

- 审计公司：12
- 接受岗位：3
- 有命中公司：2
- 公司类型：foreign 3 个
- 严格空结果公司：10
