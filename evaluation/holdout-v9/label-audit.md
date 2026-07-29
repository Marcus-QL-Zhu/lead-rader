# Holdout v9 future-label audit

## Scope and guardrails

- Label window: `2026-04-01` inclusive through `2026-07-01` exclusive (`Asia/Shanghai`).
- Universe: the twelve companies in the sealed holdout-v9 manifest.
- Company type is copied exactly from the permitted manifest/evidence: `startup_private`, `listed`, or `foreign`.
- Geography: public job postings located in mainland China.
- Accepted seniority: Director, Senior Director, Executive Director, VP, CxO, or an explicit functional Head that owns a standalone function.
- Excluded: Manager, Expert, Engineer, ordinary Lead, Associate Director, mixed-level titles that do not guarantee Director level, and ambiguous Head titles without independent functional ownership.
- Exact-date gate: relative labels such as “2 days ago” or “1 month ago” were not converted into calendar dates.
- No dates, titles, duties or postings were inferred or fabricated.

Audit completed at `2026-07-28T07:39:29.5975860+08:00`.

The required `browser-act` skill was invoked first, but the CLI was not installed in the environment. The search therefore used public web indexing, official ATS/company careers pages, LinkedIn public job archives and read-only public job endpoints. The limitation is material only where a public archive showed a relative age but no exact date; those candidates were rejected.

## Final result

| Company | Manifest type | Accepted jobs | Strict result |
|---|---|---:|---|
| Momenta | startup_private | 0 | Senior title found, but exact publication date not verifiable |
| 宇树科技 | startup_private | 0 | Mixed Manager/Director title and no exact date |
| 元戎启行 | startup_private | 0 | Empty |
| 蓝箭航天 | startup_private | 0 | Empty |
| 小鹏汽车 | listed | 0 | Empty |
| 禾赛科技 | listed | 0 | Empty |
| 摩尔线程 | listed | 0 | Expert/Engineer or ambiguous Head hits rejected |
| 小马智行 | listed | 0 | Ambiguous seniority and relative-date hit rejected |
| 米其林（中国） | foreign | 0 | Empty |
| 阿斯利康（中国） | foreign | 6 | Six official ATS postings accepted |
| 恩骅力（中国） | foreign | 0 | Empty |
| 先正达集团（中国） | foreign | 0 | Ordinary Lead rejected; otherwise empty |

Final strict label: **6 accepted jobs across 1 of 12 companies**.

## Accepted postings

All accepted postings were independently verified on AstraZeneca's official Workday or China careers site.

| Exact title | Publication date | Location | Requisition | Source |
|---|---|---|---|---|
| Head of Central GA | 2026-04-09 | Beijing Yizhuang | R-249735 | [Official Workday](https://astrazeneca.wd3.myworkdayjobs.com/it-IT/Careers/job/Beijing-Yizhuang/Executive-Director--Central-Government-Affairs_R-249735) |
| Director, AI Solution Consulting | 2026-04-22 | Shanghai or Beijing | TP23180 | [AstraZeneca China careers](https://job-search.astrazeneca.cn/%E5%B7%A5%E4%BD%9C/%E4%B8%8A%E6%B5%B7/director-ai-solution-consulting/12977/94245123536) |
| Director, Project Lead for Cell Therapy | 2026-04-22 | Shanghai | TP21147 | [AstraZeneca China careers](https://job-search.astrazeneca.cn/%E5%B7%A5%E4%BD%9C/%E4%B8%8A%E6%B5%B7/director-project-lead-for-cell-therapy/12977/88927194592) |
| Marketing Director, GUGY | 2026-06-02 | Shanghai | TP23471 | [AstraZeneca China careers](https://job-search.astrazeneca.cn/%E5%B7%A5%E4%BD%9C/%E4%B8%8A%E6%B5%B7/marketing-director-gugy/12977/95113292016) |
| Executive Director – Central Market Access Strategy & Regional Market Access (North Region) | 2026-06-04 | Beijing Yizhuang | R-253742 | [Official Workday](https://astrazeneca.wd3.myworkdayjobs.com/en-US/Careers/job/Beijing-Yizhuang/Executive-Director---Central-Market-Access-Strategy---Regional-Market-Access--North-Region-_R-253742) |
| Senior Director/ Executive Director, Safety Sciences China | 2026-06-04 | Shanghai Jing'an | R-253635 | [Official Workday](https://astrazeneca.wd3.myworkdayjobs.com/Careers/job/Shanghai-JingAn-Office/Senior-Director--Executive-Director--Safety-Science-China_R-253635) |

Responsibilities:

1. **Head of Central GA** — leads the Central Government Affairs team and owns engagement with central Party/government bodies, major policy platforms, embassies, chambers and foundations; provides policy analysis and advocacy.
2. **Director, AI Solution Consulting** — leads AI consulting and presales for strategic China opportunities, including customer discovery, executive workshops, solution shaping and scalable AI-enabled clinical-development offerings.
3. **Director, Project Lead for Cell Therapy** — leads cell-therapy clinical-trial execution, cross-functional delivery, issue resolution and trial-budget planning.
4. **Marketing Director, GUGY** — owns product-portfolio and brand strategy, launch readiness, lifecycle planning, global/local alignment, staff leadership and budget/resource allocation.
5. **Executive Director – Central Market Access Strategy & Regional Market Access (North Region)** — owns national and North-region access strategy, reimbursement policy, tender/VBP execution and access-team leadership.
6. **Senior Director/ Executive Director, Safety Sciences China** — builds and leads the China Safety Sciences team, serves as China CPSS site head, owns toxicology strategy/study oversight and develops external CRO, academic and biotech partnerships.

## Uniform company-by-company audit

### Momenta — `startup_private`

Queries:

- `Momenta China Director Head job April May June 2026`
- `site:linkedin.com/jobs/view Momenta China Director Head 2026`
- `"解决方案高级总监" Momenta 2026`

`解决方案高级总监` in Beijing was substantively Director-level and covered integrated autonomous-delivery solutions, presales and POC leadership. The public LinkedIn archive exposed only a relative posting age, and a direct public-list check did not recover an exact date.

Decision: reject; exact date not verifiable.

### 宇树科技 — `startup_private`

Queries:

- `Unitree Robotics China Director Head job April May June 2026`
- `site:unitree.com/position 总监 宇树 科技`

The [official Unitree careers page](https://www.unitree.com/cn/position/) listed `大客户销售经理/总监(J10036)`. It is a mixed Manager/Director level and the page exposes no exact posting date.

Decision: reject; seniority and date gates not both satisfied.

### 元戎启行 — `startup_private`

Queries:

- `DeepRoute.ai China Director Head job April May June 2026`
- `site:linkedin.com/jobs/view DeepRoute.ai China Director Head 2026`

Official corporate and LinkedIn results surfaced company and technical updates but no mainland-China Director+/VP/CxO or independently owned functional-Head vacancy with an exact Q2 posting date.

Decision: empty.

### 蓝箭航天 — `startup_private`

Queries:

- `LandSpace 蓝箭航天 总监 招聘 2026 4月 5月 6月`
- `site:landspace.com 招聘 总监 蓝箭航天`

The [official join-us page](https://www.landspace.com/index.php?lang=gbjob) and indexed public results exposed no qualifying exact-date Q2 2026 senior vacancy.

Decision: empty.

### 小鹏汽车 — `listed`

Queries:

- `XPeng China Director Head job April May June 2026`
- `site:linkedin.com/jobs/view 小鹏汽车 总监 招聘 2026`

Current and historical public results did not produce a qualifying mainland-China Director+ posting with an exact in-window date. Historical mixed Expert/Senior Manager/Director roles were not treated as Q2 labels.

Decision: empty.

### 禾赛科技 — `listed`

Queries:

- `Hesai Technology China Director Head job April May June 2026`
- `site:linkedin.com/jobs/view 禾赛科技 总监 招聘 2026`

Company, investor-relations and public job archives were checked. No exact-date Q2 2026 Director+/VP/CxO or independently owned functional-Head posting was found.

Decision: empty.

### 摩尔线程 — `listed`

Queries:

- `Moore Threads 摩尔线程 总监 招聘 2026 4月 5月 6月`
- `site:linkedin.com/jobs/view 摩尔线程 总监 招聘 2026`

Rejected:

- `深度学习框架研发负责人/工程师（北京/上海/杭州/深圳）` — mixed Head/Engineer title; no independent function ownership.
- `高级平台技术专家` — Expert level, expressly excluded.

Decision: empty.

### 小马智行 — `listed`

Queries:

- `Pony.ai China Director Head job April May June 2026`
- `site:linkedin.com/jobs/view 小马智行 总监 招聘 2026`
- `"战略投资- AI Agent、具身智能方向" 小马智行 2026`

`战略投资- AI Agent、具身智能方向` in Beijing was classified by LinkedIn only as `总监/主管`, while the exact title itself did not establish Director level or independent functional ownership. The archive also exposed only a relative age.

Decision: reject; seniority and exact-date gates not satisfied.

### 米其林（中国） — `foreign`

Queries:

- `Michelin China Director Head job April May June 2026 Shanghai`
- `site:jobs.michelinman.com China Shanghai Director Head 2026`
- `site:linkedin.com/jobs/view Michelin China Director Head Shanghai 2026`

No qualifying exact-date Q2 2026 senior posting was found.

Decision: empty.

### 阿斯利康（中国） — `foreign`

Queries:

- `AstraZeneca China Director Head job April May June 2026 Shanghai Beijing`
- `site:astrazeneca.wd3.myworkdayjobs.com China Director Date Posted Apr-2026`
- `site:astrazeneca.wd3.myworkdayjobs.com China Director Date Posted May-2026`
- `site:astrazeneca.wd3.myworkdayjobs.com China Director Date Posted Jun-2026`
- `site:job-search.astrazeneca.cn Director China 2026 AstraZeneca`
- `site:job-search.astrazeneca.cn/工作/上海/director 发布日期 06 2026`

Six official postings passed all gates and are listed above.

Strict rejections included:

- `(Associate) Director, Pharmacometrics`, posted `2026-05-14` — mixed Associate Director/Director level did not guarantee the required level.
- `Associate Director, Regulatory Affairs - Cell Therapy`, posted `2026-06-30` — Associate Director is below the strict threshold.

Decision: 6 accepted.

### 恩骅力（中国） — `foreign`

Queries:

- `Envalior China Director Head job April May June 2026 Shanghai`
- `site:jobs.envalior.com China Director Head 2026 Shanghai`
- `site:linkedin.com/jobs/view Envalior China Director Head Shanghai 2026`

Current China openings surfaced at Account Manager, Engineer, production and support levels. No qualifying exact-date Q2 2026 senior vacancy was found.

Decision: empty.

### 先正达集团（中国） — `foreign`

Queries:

- `Syngenta Group China Director Head job April May June 2026 Shanghai Beijing`
- `site:jobs.syngenta.com China Director Head 2026 Shanghai`
- `site:linkedin.com/jobs/view Syngenta China Director Head 2026 Shanghai Beijing`
- `先正达 中国 招聘 总监 2026 4月 5月 6月`

`Team Lead - China Sourcing Team` was rejected as an ordinary Lead. Corporate executive appointments were not treated as public vacancies.

Decision: empty.

## Independence statement

Only `evaluation/holdout-v9/evidence.json`, `manifest.json`, `pre-prediction-seal.json`, and `pre-label-seal.json` were read to establish the sealed company universe and types. No v9 acceptance snapshot, prediction output, diagnostic, prediction prompt, or session description of predicted roles was read.
