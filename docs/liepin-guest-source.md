# Liepin guest source

## Purpose

Liepin is used as a recruiting-label source for historical evaluation. It is
not prediction evidence. The model must never see Liepin job data from after a
simulated cutoff before making its prediction.

## Collection modes

Two read-only guest collectors are available:

- `scripts/collect_liepin_company_jobs.py` reads public mobile company pages
  over HTTP. It defaults to a three-second delay, fetches details only for
  Director+ jobs, rejects Liepin safety-verification redirects, and preserves
  an existing output if every request is blocked.
- The Codex in-app-browser path is the fallback when Liepin requires a human
  verification step. `scripts/normalize_liepin_browser_export.py` converts the
  browser export into the same Director+ classification shape.

Neither mode logs in, applies for a job, sends a message, or changes Liepin
state.

## Date semantics

The following fields must remain distinct:

- `observed_at`: when this project captured the public page.
- `displayed_update_text`: text shown by Liepin, such as `7月27日更新`.
- `published_at`: allowed only when an artifact states an exact original
  publication date.

An observation timestamp or page-level SEO update date must never be promoted
to `published_at`. A current active job can provide a July observation label,
but cannot by itself prove the job first appeared in July.

## Current local coverage

The manifest is
`evaluation/training-v1/liepin-company-manifest.json`. As of 2026-07-28:

- 20 of 27 job-discovery companies have a browser-verified company page.
- 377 current public job cards were captured.
- 12 titles passed the repository's Director+ deterministic gate.
- Full browser snapshots were captured locally for those 12 detail pages.

Seven companies still require an exact company-page mapping or a documented
fallback source. Missing mappings remain explicit; they are not converted into
negative labels.

## Example commands

```powershell
$env:PYTHONPATH = "src"
python scripts/collect_liepin_company_jobs.py `
  --manifest evaluation/training-v1/liepin-company-manifest.json `
  --output data/liepin-company-jobs.json

python scripts/normalize_liepin_browser_export.py `
  --input data/liepin-browser-current.json `
  --output data/liepin-company-jobs.json
```

Generated browser exports and normalized observations belong under `data/`,
which is already excluded from Git.
