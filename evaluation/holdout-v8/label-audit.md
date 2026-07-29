# Holdout v8 future-label audit

## Scope and decision rule

- Label window: `2026-01-01` inclusive through `2026-04-01` exclusive, interpreted in `Asia/Shanghai`.
- Universe: the eight companies in the sealed holdout-v8 manifest.
- Geography: publicly posted mainland-China roles.
- Accepted seniority: Director, Senior Director, VP, CxO, or an explicit functional Head that owns a standalone function.
- Excluded: Manager, Expert, Engineer, ordinary Lead, and ambiguous “Head” titles without clear independent functional ownership.
- Date gate: the source must expose an exact posting date inside the window. Relative labels such as “4 months ago” are not converted into calendar dates.
- No dates, titles, postings, or duties were inferred or fabricated.

The audit was completed at `2026-07-28T06:56:46.6447928+08:00`. Each company received a broad English/Chinese web query plus an official-domain or official-careers query. Senior-looking hits received exact-title follow-up searches and source-page checks.

## Result

| Company | Type | Accepted jobs | Strict result |
|---|---|---:|---|
| Mitsubishi Electric Intelligent Manufacturing Technology (China) Group Co., Ltd. | Foreign multinational / China operating group | 0 | No qualifying exact-date in-window posting found |
| Garrett Motion China | Foreign multinational / China operations | 0 | No qualifying exact-date in-window posting found |
| Ion Beam Applications S.A. China | Foreign multinational / China operations | 0 | One Manager role rejected; no qualifying role found |
| WIKA China | Foreign multinational / China operations | 0 | No qualifying exact-date in-window posting found |
| bioMérieux China | Foreign multinational / China operations | 0 | Two senior titles rejected because the in-window publication date could not be verified exactly |
| Döhler Group China | Foreign multinational / China operations | 0 | Senior Head hit displayed an age placing it before the window |
| GVS Group China | Foreign multinational / China operations | 0 | Official dated vacancy list contained no qualifying China role |
| Bayer China | Foreign multinational / China operations | 0 | Historical senior hits only; no exact-date Q1 2026 posting found |

Final strict label: **0 accepted jobs across 0 of 8 companies**.

## Company-by-company audit

### 1. Mitsubishi Electric Intelligent Manufacturing Technology (China) Group Co., Ltd.

Queries:

- `Mitsubishi Electric China Director Head job Shanghai 2026 January February March`
- `site:mitsubishielectric.com.cn 招聘 总监 2026 三菱电机 中国`

Sources reviewed included Mitsubishi Electric China corporate pages. Search results contained corporate, product and brand material but no public mainland-China Director+/VP/CxO or independently owned functional-Head vacancy with an exact date in the label window.

Decision: empty result.

### 2. Garrett Motion China

Queries:

- `Garrett Motion China Director Head job Shanghai Wuhan 2026 January February March`
- `site:careers.garrettmotion.com China Director 2026 job`

Sources reviewed included the [Garrett Motion careers site](https://careers.garrettmotion.com/). Corporate announcements and historical organization references were not treated as vacancies.

Decision: empty result.

### 3. Ion Beam Applications S.A. China

Queries:

- `Ion Beam Applications IBA China Director Head job Beijing 2026 January February March`
- `site:careers.iba-worldwide.com Beijing Director Head 2026`

The official careers domain surfaced `Irradiation Market Business Development Manager` in Beijing, requisition `3313`. It was rejected because it is explicitly Manager level.

Decision: empty result.

### 4. WIKA China

Queries:

- `WIKA China Director Head job Shanghai 2026 January February March`
- `site:wika.com career China Director Head 2026 Shanghai Suzhou`

WIKA corporate and China factory pages surfaced, but no qualifying public vacancy with an exact Q1 2026 posting date did.

Decision: empty result.

### 5. bioMérieux China

Queries:

- `bioMérieux China Director Head job Shanghai 2026 January February March`
- `site:careers.biomerieux.com China Shanghai Director 2026 bioMérieux`
- `site:careers.biomerieux.com "Senior Director, Commercial Operations Excellence"`
- `"Senior Director, Commercial Operations Excellence" bioMérieux Shanghai`
- `"Chief Technology Officer, Greater China" bioMérieux Shanghai Suzhou`
- `site:linkedin.com/jobs/view "Chief Technology Officer, Greater China"`
- `site:hiring.cafe "Chief Technology Officer, Greater China"`

Two titles passed the substantive seniority test but failed the exact-date gate:

1. `Senior Director, Commercial Operations Excellence` — Shanghai.

   Responsibilities: leads the integrated Commercial Excellence and Distribution Channel Management department in China; owns distributor governance, commercial capability building, CRM360, pricing/process excellence and digital commerce enablement.

   Sources: [LinkedIn public job page](https://cn.linkedin.com/jobs/view/senior-director-commercial-operations-excellence-at-biom%C3%A9rieux-4368604163), [LinkedIn public job endpoint](https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4368604163), and [HiringCafe listing](https://hiring.cafe/viewjob/2ava7rzlfs6ip9ej).

   Date evidence: the accessible LinkedIn page showed only `4 months ago`; the indexed HiringCafe snapshot showed only `Posted 3w ago`. The public HTML exposed no exact date metadata. These relative labels were not converted into a guessed date.

   Decision: reject for unverified exact publication date.

2. `Chief Technology Officer, Greater China` — Shanghai or Suzhou.

   Responsibilities: owns China R&D technology strategy and end-to-end R&D delivery; leads R&D operations; represents China R&D in global governance; oversees localization, portfolio, budget, IP, regulatory and clinical alignment.

   Sources: [bioMérieux official Shanghai search](https://careers.biomerieux.com/search/jobs/in/shanghai-sherbrooke) and a [public aggregation preserving the full duties](https://bebee.com/cn/jobs/chief-technology-officer-greater-china-biomerieux-website-shanghai--ss-cn-ytyg08).

   Date evidence: official indexed search pages confirmed title and location but not the exact posting date. A later aggregation reflected a later repost/expiry cycle and cannot prove a Q1 publication date.

   Decision: reject for unverified exact in-window publication date.

Strict company result: empty.

### 6. Döhler Group China

Queries:

- `Döhler Group China Director Head job Shanghai 2026 January February March`
- `site:careers.doehler.com/jobs China Shanghai Director Head Döhler`
- `site:linkedin.com/jobs/view Döhler China Shanghai Director Head 2026`

`Head of Sales - Beverage` in Shanghai clearly owns an independent sales function: team leadership, sales/channel strategy, annual planning, resource and budget control, customer/distributor development, and revenue/profit delivery. The [public LinkedIn page](https://cn.linkedin.com/jobs/view/head-of-sales-beverage-at-d%C3%B6hlergroup-4316921413) displayed `7 months ago` at the July 2026 audit, placing it before the strict Q1 2026 window; no exact in-window republication was found.

Decision: empty result.

### 7. GVS Group China

Queries:

- `GVS Group China Director Head job Suzhou 2026 January February March`
- `site:gvs.com careers China Suzhou Director Head 2026 GVS Group`

The [official GVS careers page](https://www.gvs.com/en/contact-us/careers/) publishes exact dates for its listed openings. No mainland-China Director+/VP/CxO or independently owned functional-Head vacancy in the label window was present in the indexed official vacancy set.

Decision: empty result.

### 8. Bayer China

Queries:

- `Bayer China Director Head job Shanghai Beijing 2026 January February March`
- `site:career.bayer.com OR site:jobs.bayer.com China Shanghai Director Head 2026 Bayer`
- `site:jobs.bayer.com/en/job/ Shanghai China Director Bayer 2026`
- `site:linkedin.com/jobs/view Bayer Shanghai Director China 2026 "Bayer"`
- `Bayer Shanghai Director job "Date posted" "2026"`
- `Bayer China Head job "Date posted" "2026" Shanghai`

Sources reviewed included the [Bayer jobs portal](https://jobs.bayer.com/viewalljobs/?locale=zh_CN). Search results returned historical senior roles such as `Digital Workplace Lead China` and `Head of China & North East Asia (NEA) Business Strategy, Bayer Crop Science`, but no evidence of an exact-date Q1 2026 public posting. Current 2026 search hits were below the accepted level or outside the window.

Decision: empty result.

## Tool and evidence notes

The required browser automation skill was invoked first, but the `browser-act` CLI was unavailable in the environment. The audit therefore used public search indexing, official careers/corporate pages and read-only public job endpoints. HiringCafe returned rate-limit/forbidden responses to direct metadata requests; its indexed public snippet was retained only as relative-date evidence and was not promoted to an exact date.

No holdout-v8 prediction output, prelabel diagnostic, acceptance snapshot, prediction prompt, or session description of predicted roles was read. Only the permitted sealed universe/evidence files were used to identify the eight-company search set.
