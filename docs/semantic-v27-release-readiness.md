# Semantic v27 release readiness

Status: ready for the server/GitHub release gate (2026-08-03).

## Evidence

- Ten selected aggregate-source articles were replayed through the current
  claim-centric pipeline with MiniMax-M3 and strict mode enabled. Every article
  was `strict_ready=true`, had zero failed claims, and had zero partial articles.
  The final replay artifacts are `lieyun...-r7`, `pedaily-vcpe...-r13`,
  `36kr...-r7`, and the seven companion samples at `...-r10` (CLS, ????
  ?????????????????VBData???). These supersede all
  earlier local reruns.
- The ten artifacts are the release-candidate files named
  `.tmp-adapter-acceptance-release-*.json` in the local acceptance workspace.
  They are deliberately not committed because they contain raw article text and
  model responses; the archived source fixtures and deterministic tests are the
  reproducible inputs.
- The Director+ historical backtest gate is represented by the tracked
  `evaluation/historical/` jobs/evidence and the local acceptance reports under
  `.acceptance/`; its three-month cutoff and distinct-role safeguards passed.
- The independent acceptance review checks exact evidence quotes, final claim
  lineage, subject/event grounding, and the two intentional `no_claims` route
  outcomes (non-event articles). The refreshed business cases retain the
  previously missing workforce, R&D/platform, order, and global-expansion
  signals; no event-level omission or subject/event factual error remains.
  A few title/body restatements and one funding-use sentence legitimately
  support the same event or two orthogonal hiring-signal labels. They remain
  visible in claim lineage as non-blocking audit noise, not extra events.

## Production wiring fixed before release

- `SourcePackCollector` enables strict claim-centric V27 by default, while
  preserving explicit environment rollback flags.
- The task env-file is merged into OpenClaw's model configuration lookup, so a
  `${MINIMAX_API_KEY}`-style provider credential is available to the collector.
- Semantic cache checks use the actual persisted prompt namespace and validate
  claim-centric/strict mode, preventing V26 reuse or needless V27 recomputation.
- Claim dead letters fall back to the claim processor's `failed_claim_ids`, and
  the default MiniMax worker cap is four, matching the approved concurrency.

## Release validation

- `pytest -q`: 1153 passed.
- `ruff check src scripts tests`: passed.
- `python -m compileall -q src scripts tests`: passed.
- `git diff --check`: passed.
