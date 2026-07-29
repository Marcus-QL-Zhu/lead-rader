# Strict holdout v5

This is a foreign-company confirmation holdout created after v4 exposed a
general English commercial-ontology gap. All six companies are new and were
selected by a separate agent without access to model predictions or
post-cutoff jobs.

The cutoff is 2026-04-01 and the validation window is
`[2026-04-01, 2026-07-01)`. Inputs exclude job ads, workforce clusters,
JOSINT, analyst notes and post-cutoff information. The matcher, candidate
coverage gate and all prediction code must be frozen before label search.
