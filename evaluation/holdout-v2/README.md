# Strict holdout v2

This is the first strict candidate-universe holdout. A separate agent selected
all ten companies only from pre-cutoff public signals and was explicitly
prohibited from searching jobs. The candidate list includes later positive and
unobserved controls by construction; future-job labels are collected only
after predictions are frozen.

The cutoff is 2026-04-01 and the validation window is [2026-04-01,
2026-07-01). Inputs exclude job ads, workforce clusters, JOSINT and analyst
notes. Every candidate is retained during uniform label collection even when
no later Director+ role is observed.