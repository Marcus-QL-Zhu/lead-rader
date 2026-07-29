# Strict holdout v11

This is a fresh foreign life-science holdout after the historical-job adapter
was frozen with support for both `title`/`description` and independent-auditor
`exact_title`/`responsibilities_summary` fields. Eight companies were selected
without searching or opening future jobs.

The cutoff is `2026-04-01`; the validation window is
`[2026-04-01, 2026-07-01)`. Prediction, matching, job-field normalization,
accuracy and 30-title diversity gates are all frozen before label search.
