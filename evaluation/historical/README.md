# Historical evaluation dataset

This directory contains the first frozen historical evaluation cohort for
Lead Rader. It is intentionally separate from runtime reports and JOSINT.

## Leakage boundary

- Prediction evidence is public information timestamped before each simulated
  cutoff. `event_date` records when the event happened; `published_at` or
  `observed_at` independently records when the evidence became public.
  Evidence without a defensible public-availability timestamp is excluded.
- Job advertisements are stored only in `jobs.json`; they are never included
  in a prediction packet.
- Workforce-cluster signals (manager, expert and engineer job clusters) remain
  implemented in production but are disabled in acceptance replay.
- The prediction snapshot must be written before validation. The CLI refuses
  to overwrite a frozen snapshot.
- Evidence URLs remain in the snapshot so a human can audit every input.
- Snapshots also freeze the complete prediction packets, prompt and response
  audit trail, prompt/input hashes, model identity, content hashes and explicit
  benchmark company-type labels.
- The historical prompt contains a temporal embargo: the model must ignore
  all post-cutoff memory and use only the frozen packet.

## Cutoffs

The initial acceptance run uses:

- `2026-01-01`: validates January through March.
- `2026-04-05`: validates April through early July.
- `2026-05-01`: validates May through July.

The candidate universe is defined by pre-cutoff organizational events across
robotics and semiconductors. It includes companies without a later observed
Director+ job so the replay is not made solely from winners. Later jobs were
collected from company career pages or job-ad aggregation pages after the
prediction evidence had been separated.

## Acceptance gates

The evaluator uses counts, not percentages:

- at least three historical cutoffs;
- at least two role-family matches in every cutoff;
- at least 30 distinct predicted Director+ titles across the snapshots;
- at least 30 canonical role keys and at least eight role families, so title
  wording variants cannot satisfy diversity alone;
- at least five distinct later Director+ job records across all cutoffs; actual job
  identities are globally deduplicated because the simulated three-month windows overlap;
- verified role matches covering startup/private, listed and foreign
  companies.

This is a pilot benchmark, not a claim that every unobserved prediction is
wrong. Public job pages have incomplete recall and often disappear. The
benchmark should be expanded over time without editing prior frozen snapshots.
## 2026-07-28 acceptance run

The final frozen MiniMax-M3 replay uses prompt version
`historical-demand-v5-required-functional-coverage`, temperature `0.0`, and
workforce precursors disabled. The three validation reports are:

- `.acceptance/v7c-backtest-2026-01-01.report.json`: 2 role matches;
- `.acceptance/v7-backtest-2026-04-05.report.json`: 3 role matches;
- `.acceptance/v7-backtest-2026-05-01.report.json`: 3 role matches.

The aggregate report `.acceptance/v7-aggregate.json` passes every gate with 74
distinct predicted titles, 72 canonical role keys, 16 role families, five
distinct matched later jobs, and verified matches across startup/private,
listed and foreign companies. These are count-based benchmark results, not a
precision percentage. Unobserved predictions remain unlabelled because public
job-ad recall is incomplete.
