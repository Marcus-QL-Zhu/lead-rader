# Historical snapshot dataset v3

Status: local work in progress; not synchronized to the server or GitHub.

## What changed

- The company universe was frozen at 120 companies before collecting the new
  recruiting outcomes.
- Split sizes are 72 train, 12 calibration and 36 test.
- The 60-company addition is balanced across startup/private, listed and
  foreign companies and contains no company already present in training-v1.
- Search aliases are stored separately from the frozen split so English and
  Chinese company names can be matched without changing cohort membership.
- News search candidates, direct source artifacts and hashes are stored
  separately from recruiting labels.
- Job search captures are labels only. They never enter MiniMax prediction
  evidence.
- Manager, expert and engineer titles are excluded from the evaluation labels;
  the collector and system can still support those levels outside evaluation.

## Captured data

Across the v2 and v3 additions:

- 120 companies have a frozen split.
- 120 companies have a planned fixed-source/news collection task.
- 60 v2 and 60 v3 companies were covered by web discovery.
- 570 news search candidates were retained before verification.
- 50 high-quality, in-window news pages were selected for direct capture.
- 49 artifacts were captured; 24 passed company, date, window and A/B-source
  verification.
- For the simulated 2026-04-30 cutoff, 19 evidence rows across 17 companies
  remain after removing May-June evidence.
- 349 recruiting search candidates were retained.
- Six direct Director+ job-page candidates in the May-July horizon were found,
  covering five companies. Four are train and two are test after deduplication
  by company.

The exact counts used by the gate are in `readiness-report.json`.

## Current readiness

The dataset is not ready for a credible model comparison:

- provisional positive companies: train 13/25, calibration 7/8, test 2/12;
- companies with strict pre-cutoff evidence: 17/30;
- pre-cutoff event types: 7/10;
- replayable exact job-page labels from the new snapshot collection: 0/1.

Search-result captures are useful for feasibility review but are not silently
promoted to strict labels. Relative LinkedIn dates remain marked as estimates.

## Blocking source issue

Liepin guest access began redirecting repeated searches to its login page.
The browser was deliberately left at the login handoff instead of bypassing
the site control. This blocks both exact job-page archiving and trustworthy
negative coverage for many Chinese companies.

Expanding the universe again before stabilizing the recruiting-label channel
would add mostly unknown rows, not useful training examples. The next data
work should therefore be:

1. resume low-frequency Liepin company-page collection after the user completes
   the browser login, or connect another replayable job source;
2. archive exact job pages and full company result pages for both positives and
   confirmed snapshot negatives;
3. broaden fixed-source news queries by signal family for companies still
   lacking pre-cutoff evidence;
4. rerun `build_snapshot_backtest_inputs.py` and
   `assess_snapshot_training_readiness.py`;
5. call MiniMax only after the readiness gate has enough evaluable train,
   calibration and frozen test companies.

## Reproduction

The primary local commands are:

```powershell
$env:PYTHONPATH = "src"
python scripts/expand_company_pool_v3.py `
  --base evaluation/training-v2/company-expansion-pool.json `
  --output evaluation/training-v3/company-pool.json
python scripts/build_snapshot_backtest_inputs.py `
  --pool evaluation/training-v3/company-pool.json `
  --news data/training-v3-all-news-verified.json `
  --jobs evaluation/training-v3/all-exact-job-review-queue.json `
  --output evaluation/training-v3/snapshot-backtest-2026-04-30.json
python scripts/assess_snapshot_training_readiness.py `
  --historical evaluation/training-v1/dataset.json `
  --snapshot evaluation/training-v3/snapshot-backtest-2026-04-30.json `
  --output evaluation/training-v3/readiness-report.json
```
