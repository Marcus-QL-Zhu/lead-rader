# Document Route Gate v1

The ten high-value aggregate channels do not publish one homogeneous type of
article. Before semantic extraction, the pipeline now applies a deterministic
route gate. The gate keeps the legacy `document_type` used by the existing
ledgers, and adds a more useful `document_family`, `processing_mode`, confidence
and explicit signals.

## Source-shape inventory

| Source family | Typical article shapes | Route family | Pre-LLM handling |
| --- | --- | --- | --- |
| 36Kr financing flash | One-company financing flash; multi-company daily digest; market/policy noise | `single_company_funding`, `multi_company_funding_digest`, `multi_company_bulletin` | Keep one company as one unit; split adapter item boundaries for digests; semantic review only when the digest contains multiple funding cues |
| 投资界 / PE Daily | Company financing; fund/LP/GP and investor activity; investment commentary | `single_company_funding`, `institutional_funding` | Review the institution or target explicitly; do not treat a fund/person article as a company event automatically |
| 创业邦 | Single funding; mixed AI/industry commentary; multi-company digest | `single_company_funding`, `multi_company_bulletin` | Keep atomic funding flashes; split deterministic digest units |
| 猎云 | Funding, cooperation, IPO and industry roundups | `single_company_funding`, `multi_company_bulletin` | Route the transaction flash as one unit; split roundups |
| 动脉网 | Funding; interview; industry/clinical commentary; roundups | `single_company_funding`, `interview_commentary`, `long_feature` | Use the first 2,000 characters only when an event is present; otherwise skip the long commentary |
| 甲子光年 | Long feature, research/whitepaper, interview/commentary; embedded company event | `long_feature`, `interview_commentary` | Prefix-window review; preserve a company event only when its evidence is in the window |
| 智东西 | Funding/IPO; product or technical milestone; industry commentary | `single_company_funding`, `single_company_flash`, `commentary` | One-unit extraction for company news; route commentary separately |
| 证券时报 | Executive appointment, order, expansion, M&A, and compound bulletin pages | `compound_company_bulletin`, `single_company_flash`, `policy_market` | Split atomic company claims before semantic adjudication; market articles use market rules first, company override second |
| 财联社 | Company flash (order/capacity/product); telegraph/calendar digest; policy/market items | `single_company_flash`, `compound_company_bulletin`, `policy_market` | Split telegraph units; apply market/policy rules before allowing a company override |
| 工信部科技司 | Policy, standards, notices, lists and projects; occasional company mention | `policy_market` | Policy/standard rules first; company events require explicit company-action evidence |

The inventory is based on the ten adapter acceptance fixtures and their
dedicated regression tests under `.acceptance/aggregate-v2/` and
`tests/test_aggregate_*_adapter.py`. The two PE Daily and two 创业邦 channel IDs
are counted as one logical source family each.

## Gate contract

`route_document(article)` returns:

- `document_family`: the article-shape taxonomy above;
- `processing_mode`: `single_unit`, `split_units`, `split_atomic_claims`,
  `prefix_2000_if_event_else_skip`, `policy_rules_then_company_override`,
  `market_rules_then_company_override`, `institution_or_target_review`, or a
  compatibility review mode;
- `gate_confidence`: deterministic confidence (`high`, `medium`, `low`);
- `llm_gate_required`: whether the route needs semantic adjudication rather
  than deterministic rules alone;
- `gate_signals`: auditable reasons such as adapter item boundaries, funding,
  institution, interview, market, policy, compound and long-body cues.

The gate is deliberately conservative: it does not create an event, infer a
company, or discard source text. It decides how the immutable source units are
presented to the semantic processor. The route fields are included in the
MiniMax payload and in the semantic audit so a reviewer can explain why an
article was processed in a particular way.

## Acceptance coverage

`tests/test_document_route_gate.py` covers one representative shape for each
logical family and a synthetic multi-item 36Kr financing digest. The real DOM
replay case is covered by `tests/test_aggregate_kr36_long_digest.py`; its
MiniMax acceptance packet is documented in
`docs/long-article-window-case-study-v3-kr36-digest.md`.

## Real replay refinements

The route was replayed against the archived articles under
`.acceptance/aggregate-v2/` for all ten logical source families. Two adapter-level
fields are treated as high-value structural evidence:

- `company` / `company_mentions` means a newswire or financing adapter has
  already identified a target company; it prevents an investor name from
  overriding the company route.
- For MIIT, the same fields identify the issuing authority, not a recruiting
  target, so they are explicitly excluded from the target-company signal.

The replay also applies three guardrails: company?? pages from STCN/CLS do not
become policy articles merely because they contain the word ????; mixed CLS
news digests do not become financing digests just because several paragraphs
mention investment; and a 2,000-character-or-longer article falls into the long
article window unless a more specific deterministic route wins.
