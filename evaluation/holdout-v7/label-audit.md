# Holdout-v7 future-label search audit

## Scope and guardrails

- Label window: `2026-01-01` inclusive to `2026-04-01` exclusive.
- Search executed: `2026-07-28T06:17:50.9341622+08:00`.
- Candidate universe: the six companies frozen in `manifest.json`.
- Inclusion threshold: public China openings whose exact title is Director, Senior Director, Executive Director, VP, CxO, or an unambiguous functional Head who owns the function.
- Exclusions: managers, senior managers, leads, experts, specialists, engineers, individual contributors with inflated titles, ambiguous Head titles, internships, and postings outside the label window.
- Only `evidence.json`, `manifest.json`, `pre-prediction-seal.json`, and `pre-label-seal.json` were read. No prediction snapshot, prediction output, prompt content, acceptance artifact, or description of predicted roles was read.
- The same two-query baseline was applied to every company: a broad company/China/seniority/date query and an official-career-domain or public-job-page query. Extra exact-title and requisition-ID queries were used only to validate discovered candidates.
- The browser automation CLI required by the local browser skill was unavailable, so the audit used public web indexing and directly addressable official career pages. No authenticated or personalized job feed was used.

## Results summary

| Company | Included labels | Outcome |
|---|---:|---|
| Henkel China | 0 | No qualifying Director+/functional-Head posting verified in-window |
| Porsche China | 0 | No qualifying Director+/functional-Head posting verified in-window |
| Beckers Group China | 0 | No qualifying Director+/functional-Head posting verified in-window |
| Nouryon China | 0 | No qualifying Director+/functional-Head posting verified in-window |
| Qnity Electronics China | 0 | No qualifying Director+/functional-Head posting verified in-window |
| Dassault Systèmes Greater China | 2 | Two official Director postings verified |

## Company-by-company query audit

### Henkel China

Baseline queries:

1. `Henkel China Director job Shanghai "2026" January OR February OR March`
2. `site:henkel.com/careers/jobs-and-application China Director 2026 Shanghai`

Observed results and disposition:

- The official career results surfaced roles such as `Lead application engineer` and `Key Account Manager`. These are below the frozen Director+/functional-Head threshold and were excluded.
- A May 2026 Asia-Pacific president appointment appeared in results but is outside the label window and is an appointment announcement, not an in-window public vacancy.
- No Director, VP, CxO, or unambiguous functional-Head China vacancy with an exact in-window posting date was verified.

Evidence quality for the empty result: **B**. Official careers were indexed and inspected, but historical vacancies may have been removed from the live site.

### Porsche China

Baseline queries:

1. `Porsche China Director Head job Shanghai "2026" January OR February OR March`
2. `site:jobs.porsche.com China Director Head Shanghai 2026`

Observed results and disposition:

- Results were dominated by corporate announcements, China business news, dealer-network reporting, and assistant/internship material rather than public senior vacancies.
- The global CEO succession effective January 2026 was not a China vacancy and was excluded.
- No qualifying China posting with a verifiable publication date in the window was found.

Evidence quality for the empty result: **B-**. No qualifying official vacancy was indexed; removed historical vacancies remain a possible source limitation.

### Beckers Group China

Baseline queries:

1. `Beckers Group China Director job Shanghai 2026 January February March`
2. `site:linkedin.com/jobs/view Beckers Shanghai Director 2026`

Observed results and disposition:

- Searches returned Beckers corporate material and unrelated companies or roles, but no attributable Beckers China Director+/functional-Head vacancy.
- No exact title, posting date, and source URL meeting the threshold could be verified.

Evidence quality for the empty result: **B-**. Public indexing coverage for this smaller employer is limited; no label was inferred from absence.

### Nouryon China

Baseline queries:

1. `Nouryon China Director job Shanghai Tianjin 2026 January February March`
2. `site:careers.nouryon.com China Director Head Shanghai 2026`

Observed results and disposition:

- Official results included `Direct Category Manager` in Shanghai, closing `2026-02-28`, requisition `N0013360`. The page classifies it as `Professional/Experienced/Specialist`, and the role reports to the APAC Direct Procurement Manager; it is not a Director or functional one-head and was excluded.
- Other indexed China roles were managers, specialists, or operators and were excluded.
- No qualifying Director+/functional-Head posting was verified.

Evidence quality for the empty result: **B+**. Nouryon's official vacancies and hierarchy metadata were available and directly supported the exclusions.

### Qnity Electronics China

Baseline queries:

1. `Qnity Electronics China Director job Shanghai 2026 January February March`
2. `site:careers.qnityelectronics.com China Director Shanghai 2026`

Observed results and disposition:

- Official/current results included `Account Manager`, technical advisers, experts, engineers, and program managers. These titles do not meet the threshold.
- No China Director, VP, CxO, or unambiguous functional-Head vacancy posted in the label window was verified.

Evidence quality for the empty result: **B**. Qnity's Workday and public job pages were indexed, but the company was newly independent and historical indexing is not complete.

### Dassault Systèmes Greater China

Baseline queries:

1. `Dassault Systemes Greater China Director Head job Shanghai 2026 January February March`
2. `site:3ds.com/careers China Director Shanghai 2026 job`

Validation queries:

1. `site:3ds.com/careers/jobs/ "China," "Director" "Posted on:1/"`
2. `site:3ds.com/careers/jobs/ "China," "Director" "Posted on:2/"`
3. `site:3ds.com/careers/jobs/ "China," "Director" "Posted on:3/"`
4. `"APAC Financial Planning & Analysis Director" "Posted on"`
5. `"546567" "Posted on" Dassault`
6. `"Government Affairs Director, China" "546714"`
7. `"546714" "2/2/2026" Dassault`

Included postings:

1. **APAC Financial Planning & Analysis Director**
   - Employer brand: Medidata, a Dassault Systèmes company.
   - Location: Shanghai, China.
   - Posted: `2026-01-12`.
   - Requisition: `546567`.
   - Official URL: <https://www.3ds.com/careers/jobs/APAC-Financial-Planning-Analysis-Director-546567>
   - Corroborating brand URL: <https://www.medidata.com/en/careers/apac-financial-planning-analysis-director-546567/job/>
   - Qualification rationale: exact Director title; owns APAC FP&A planning, forecasting, governance, analysis, and business partnering.
   - Evidence quality: **A**.

2. **Government Affairs Director, China**
   - Employer brand: Medidata, a Dassault Systèmes company.
   - Location: Beijing, China.
   - Posted: `2026-02-02`.
   - Requisition: `546714`.
   - Official indexed URL: <https://www.3ds.com/fr/careers/jobs/government-affairs-director-china-546714>
   - Public corroboration: <https://cn.linkedin.com/jobs/view/government-affairs-director-china-at-dassault-syst%C3%A8mes-4368497128>
   - Qualification rationale: exact Director title; reports to VP, North Asia Sales and owns China government affairs, policy strategy, advocacy, and stakeholder relations.
   - Evidence quality: **A-**. The exact official job page was indexed with date, location, and requisition ID but later redirected after expiration; the public LinkedIn copy preserves the full role and employer attribution.

Other China roles found during validation, including `Partner Sales` and `Client Manager, CRRC Group`, were manager-level or otherwise below the threshold and were excluded.

## Final label count

- Qualifying jobs: **2**
- Distinct manifest companies with a qualifying job: **1**
- Empty-result companies: **5**
- No standard was relaxed and no missing posting was inferred or fabricated.
