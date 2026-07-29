# Holdout-v10 future-label audit

## Scope and strict rule

- Label window: `[2026-04-01, 2026-07-01)` in mainland China.
- Universe: the eight manifest companies, all `company_type=foreign`.
- Accepted levels: Director, Senior Director, Executive Director, VP, CxO, or an explicit functional Head that demonstrably owns a standalone function.
- Excluded without exception: Manager, Expert, Engineer, ordinary Lead, Associate Director, mixed Associate Director/Director titles, and Head titles whose independent functional ownership could not be confirmed.
- Exact-date rule: relative ages such as “1 week ago” or “30+ days ago” were not converted into dates. A role was accepted only when an exact in-window date was publicly verifiable.
- Search time: `2026-07-28T08:10:02.1670104+08:00`.

## Results

Seventeen postings passed all strict checks: eleven at Novartis, five at Danaher, and one at GSK. Five companies had audited strict-empty results.

| Company | Exact title | Exact date | Location | Date and duty evidence |
|---|---|---:|---|---|
| Novartis | Portfolio Strategy and Partnership Head - China | 2026-04-23 | Shanghai | Official Novartis job detail, REQ-10075923 |
| Novartis | Head, TCO (Translational Clinical Oncology) China | 2026-04-28 | Shanghai | Official Novartis job detail, REQ-10075344 |
| Novartis | Site Quality Head Changping | 2026-04-28 | Beijing Changping | Official Novartis job detail, REQ-10076386 |
| Novartis | Director, Program Management | 2026-06-22 | Shanghai | Official Novartis job detail, REQ-10080264 |
| Novartis | TMDP China Head | 2026-06-22 | Shanghai | Official Novartis job detail, REQ-10076613 |
| Novartis | Director, Preclinical Safety (Toxicology) China Head | 2026-06-22 | Shanghai | Official Novartis job detail, REQ-10077754 |
| Novartis | Director - Chemistry Scientific Due Diligence | 2026-06-22 | Shanghai | Official Novartis job detail, REQ-10076995 |
| Novartis | Head ERC, China | 2026-06-22 | Shanghai | Official Novartis job detail, REQ-10063581 |
| Novartis | Marketing Head | 2026-06-22 | Shanghai | Official Novartis job detail, REQ-10081104 |
| Novartis | Head of New Product, International China | 2026-06-22 | Shanghai | Official Novartis job detail, REQ-10071116 |
| Novartis | PK Director (临床药理总监） | 2026-06-22 | Shanghai | Official Novartis job detail, REQ-10075924 |
| Danaher | Senior Director, Clinical Translation, Danaher China | 2026-04-10 | Shanghai | Exact Danaher Recruitment campaign + official Workday, R1307301 |
| Danaher | Senior Director, Technology Innovation, Danaher China | 2026-04-10 | Shanghai | Exact Danaher Recruitment campaign + official Workday, R1307300 |
| Danaher | Director, LBS CH R&D SHA | 2026-04-10 | Shanghai | Exact Danaher Recruitment campaign + official Workday, R1303140 |
| Danaher | Science and Technology Innovation Director, Danaher China | 2026-05-15 | Shanghai | Exact Danaher Recruitment campaign + official Workday, R1295430 |
| Danaher | Senior Director, Growth & Innovation (DBSL) | 2026-05-15 | Shanghai | Exact Danaher Recruitment campaign + official Workday, R1310428 |
| GSK | VP & Country Medical Director China | 2026-06-29 | Shanghai | Exact-date full public archive + official GSK job ID 443890 |

Full URLs, duties summaries, requisition IDs, query strings, date basis and per-record search timestamps are preserved in `jobs.json`.

## Uniform company-by-company audit

### Sanofi（赛诺菲中国） — strict empty

Queries covered the official China job inventory, English and Chinese career pages, Director/Head terms, the three-month window and title-specific follow-up. `Senior Director & Asia Pacific Regional Lead, External R&D` was verified in Beijing, but the official page exposed no exact posting date. `Marketing Director, Dupixent Dermatology` was posted on 2026-07-09, outside the window. No role was accepted.

### Merck KGaA（默克医药健康中国） — strict empty

Queries covered Merck Group careers, English and Chinese titles, Shanghai/Beijing and public LinkedIn archives. `Head of Regulatory Management China` showed substantive regulatory-function ownership, but only a relative public age was available. `OLED中国业务负责人` also lacked an exact date and belongs to Electronics rather than the manifest's Healthcare scope. No role was accepted.

### Pfizer（辉瑞中国） — strict empty

Queries covered Pfizer's official China inventory, Workday detail pages, Shanghai/Beijing Director terms and public LinkedIn archives. `Pfizer-Strategy & Innovation Director-SH` met the level and duty tests, but only relative posting ages could be verified. `Associate Director, Clinician` was excluded by level. No role was accepted.

### Medtronic（美敦力中国） — strict empty

Queries covered Medtronic Workday, Director/Head/GM titles, Shanghai and public LinkedIn pages. `General Manager, MiniMed Greater China` clearly owns the Greater China business and P&L, but the official ATS and LinkedIn exposed only relative ages; the current ATS age also indicates a July repost. `Surgical_Associate Market Access Director` was excluded as Associate Director. No role was accepted.

### Novo Nordisk（诺和诺德中国） — strict empty

Queries covered the official careers domain, Beijing/Shanghai/Tianjin, Director/Head terms and public LinkedIn pages. `Sourcing Director, China External Innovation` met the level and duty tests but lacked an exact date. `(Asst.) Director, Biomolecular Technologies` was excluded as a mixed Assistant Director/Director title and also lacked an exact date. No role was accepted.

### Novartis（诺华中国） — 11 accepted

The official career-search inventory was sorted and searched by China, date, division, Director and Head. Each accepted record has an official exact date, mainland-China location and duties. Associate Director and mixed `AD/D` postings were excluded. `Medical Hema Head` and `Medical TA Head-NS` were conservatively excluded because the public text did not clearly prove independent functional or people ownership.

### GSK（葛兰素史克中国） — 1 accepted

Official GSK careers and title-specific public archives were searched. `VP & Country Medical Director China` was accepted because the archive records an exact 2026-06-29 date, Shanghai location, complete China Medical-function ownership and job ID 443890; GSK's official URL independently corroborates the exact title and ID, although it now reports the job as filled.

### Danaher（丹纳赫中国） — 5 accepted

The official Workday inventory, Danaher China public recruitment campaigns, requisition IDs and title-specific public pages were searched. Exact Danaher Recruitment campaign dates on 2026-04-10 and 2026-05-15 were cross-checked against official Workday pages for title, Shanghai location and duties. `National Sales Director, Clinical Flow` and `Director, Strategy, Market Development & Microbiology BU` had exact campaign dates but were excluded because a sufficiently detailed official duty page could not be found. Relative-date-only roles were also excluded.

## Tool and independence audit

- The browser-act skill was invoked first as required, but the `browser-act` CLI was not installed. The search therefore used public web results, official ATS/career pages and read-only public job archives. No tool installation or authenticated browsing was performed.
- Only `evaluation/holdout-v10/evidence.json`, `manifest.json`, `pre-prediction-seal.json` and `pre-label-seal.json` were read before labeling.
- No holdout-v10 snapshot, prediction, diagnostic, prompt, or session description of predicted roles was opened or used.
- No v10 file other than `jobs.json` and `label-audit.md` was created or modified.
