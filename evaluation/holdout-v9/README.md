# Strict holdout v9

This is a recent-window, mixed-company-type holdout. A blinded agent selected
four startup/private companies, four listed companies and four foreign
companies from positive A-grade events before seeing future jobs or model
predictions.

The cutoff is `2026-04-01`; the validation window is
`[2026-04-01, 2026-07-01)`. Prediction excludes job ads, workforce clusters,
JOSINT, analyst notes and all post-cutoff information. Acceptance thresholds,
including all three company types and the 30-title diversity gate, are frozen
before model execution.
