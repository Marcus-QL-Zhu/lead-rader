# Semantic V27 Prompt Loop

The maximum-three-round experiment is defined by
`evaluation/semantic-v27/development-v3-split.json`.

`round-1/variant-a-prediction.json` is an invalid infrastructure-failure
artifact from an attempted sandboxed run. Every provider call failed with
`URLError`; it is retained only as failure provenance and must not count as
round 1, model output, or prompt-selection evidence.

Valid candidates must have `status=complete`, zero failed Claims, no provider
infrastructure errors, and a passing V27 evaluator report before blind review.

## Local completion record (2026-08-02)

- Round 1 valid outputs: A/B failed the unsupported-event gate; C passed.
- Round 2 valid outputs: A/B/C all passed. Three independent blind reviews
  selected anonymous candidate-gamma, mapped privately to variant B.
- Frozen winner: `final-winner-config.json`, prompt hash
  `e38779c3a0deabd680e44032e920e4bf83e89f025c95406e8a4839506d1315db`.
- Frozen holdout sequence: `stcn-flash:4052079`, `cyzone-latest:841774`,
  `jazzyear-research:162`. The final artifact passes all 3 consecutive cases;
  Cyzone is 35/35 exact supported events with no unsupported events.
- This was initially a local development result. The release gate was completed
  on 2026-08-02 after the fresh adapter replay and Director+ backtest passed;
  see `docs/semantic-v27-release-readiness.md` for the current evidence.

## Fresh adapter acceptance (2026-08-03)

- Initial ten-source citation audit: 22/22 evidence quotes were exact source
  substrings; all 10 articles were strict-ready and had zero failed claims.
- Focused post-fix checks passed for 36Kr, 创业邦, 智东西 and 工信部科技司.
  The latest 智东西 artifact is
  `.tmp-adapter-acceptance-fresh-zhidx-20260802-r7.json` and independently
  verifies both customer-validation facts and the B+ financing facts.
- The post-fix ten-source semantic rerun and Director+ backtest now pass. The
  raw acceptance JSON remains local-only; code, fixtures, tests, and the release
  record are the reviewable source of truth.

The release gate is event-level: all selected articles are strict-ready, all
source quotes are exact substrings, and the independent review found no missing
positive event or subject/event fact error. Claim-level title/body restatements
are retained in lineage for auditability and are not counted as additional
events.
