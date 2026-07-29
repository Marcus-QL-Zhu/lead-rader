# Strict holdout v4

This is a blinded multi-signal holdout created after v3 showed that isolated
single events were too weak to activate the deliberately conservative demand
gate. Candidate selection still excludes future jobs and model predictions.

The cutoff is 2026-04-01 and the validation window is
`[2026-04-01, 2026-07-01)`. Inputs exclude job ads, workforce clusters,
JOSINT, analyst notes and all post-cutoff information. All nine candidates
must remain in uniform future-label collection, including candidates for
which no later Director+ role is observed.
