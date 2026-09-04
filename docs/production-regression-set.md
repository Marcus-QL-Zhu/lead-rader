# Production regression-set export

The export script freezes the 14 daily reports from 2026-08-18 through
2026-08-31. It is intentionally offline: it reads local JSON reports and,
optionally, SQLite schema metadata through a read-only URI. It never opens a
network connection.

The output is canonical per-day JSON plus manifest.json. Each day and the
manifest have deterministic SHA-256 digests. The projection includes only the
safe report manifest, source report digest/size, four independent operational
statuses, gate counts, selected company/role/score hypotheses, date/type/source
evidence references (with evidence URLs hashed), cooldown counters, and
per-adapter health counters. It excludes raw database rows, prompts, HTML,
contact details, credential values, and all filesystem paths.

Historical data has no trustworthy new-style run-state record. Therefore every
export contains capture_version pre-hotfix and legacy null. The exporter does
not invent a success status or derive one metric from a different metric.
In particular, semantic attempt/accepted counts remain null unless those exact
fields were observed; rule-event and MiniMax-event counts are retained under
their own names. Legacy cooldown aliases are readable, but missing counts stay
null rather than being inferred from another field.

An evidence URL digest identifies the source location, not the semantic claim:
the same URL may legitimately support different event types. Only a completely
identical projected evidence tuple is duplicate data. Raw duplicates are
stably collapsed before export by retaining their first occurrence; the
validator still rejects any duplicate that survives into a final fixture.
Likewise, listing and discovered counts remain separate observations; one is
never substituted for the other.

Example PowerShell invocation:

    $sourceSha = git rev-parse HEAD
    python scripts/export_production_regression_set.py --reports-dir reports-daily --sqlite data/lead-radar.sqlite --generator-git-sha $sourceSha --output-dir evaluation/production-regression-20260818-31
    python scripts/export_production_regression_set.py --output-dir evaluation/production-regression-20260818-31 --validate-only

The default target is evaluation/production-regression-20260818-31. The output
must not be generated until all exporter and sanitizer source changes have been
committed as source commit A. The manifest records that exact source SHA; the
fixture is then added in a separate artifact commit B. A complete fixture has
exactly 15 regular files: fourteen canonical daily JSON files and
`manifest.json`, with no subdirectories or auxiliary files. The output is
generated evaluation material and must be reviewed for sanitization before
being shared; it is not a replacement for raw reports or the operational
database.

The manifest and daily contracts are documented in
docs/schemas/production-regression-set-v2.schema.json and
docs/schemas/production-regression-day-v2.schema.json. The Python validator is
authoritative for duplicate-key rejection, finite-number checks, digest
recomputation, exact directory contents, and the forbidden-key/value scan.
