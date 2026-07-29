# Historical dataset expansion v2

## Decision

The v1 dataset has enough companies to create files named train and test, but
not enough labelled companies to evaluate the system:

- train: 36 companies, only 10 companies with a positive label;
- calibration: 9 companies, only 7 companies with a positive label;
- test: 18 companies, zero strict positive labels;
- confirmed replayable negatives: zero.

Therefore v1 is a bootstrap dataset, not a valid train/test benchmark.

## Expansion cohort

`evaluation/training-v2/company-expansion-pool.json` freezes 60 companies before
their job outcomes are collected:

- 36 train, 6 calibration, 18 test;
- 20 startup/private, 20 listed, 20 foreign;
- semiconductor, embodied AI, robotics, autonomous driving, commercial space,
  fusion, industrial automation, batteries and advanced manufacturing.

Selecting and splitting companies before reading job outcomes prevents
positive-outcome selection bias. The v2 cohort does not replace v1; together
they provide 123 company identities, subject to alias de-duplication.

## Two label tracks

### Strict temporal benchmark

A row may enter the strict benchmark only when:

1. a Director+ job has an exact, defensible publication date;
2. prediction evidence was public before the simulated cutoff;
3. the job falls within the next three calendar months;
4. job evidence is excluded from the prediction payload;
5. source URL, capture time, raw artifact and SHA-256 are replayable.

Only this track can support headline test metrics.

### Current snapshot auxiliary set

Liepin guest company pages can label whether an eligible Director+ role was
active on the capture date. These rows are useful for company and role-family
coverage, but they are lower-weight auxiliary observations:

- `observed_at` is not `published_at`;
- a missing current role is a point-in-time absence, not proof that no role was
  advertised during the preceding three months;
- current job text is never shown to the prediction model.

The auxiliary set must never be silently merged into strict temporal metrics.

## News collection window

For the 2026-07-28 snapshot cohort, collect company events published from
2026-01-01 through 2026-06-30. Preserve the complete timeline and derive
separate cutoffs at the end of March, April, May and June where job dates allow.
The event ontology includes leadership changes, organization changes, funding,
capacity, orders, customer validation, products, partnerships, regulation,
geographic expansion and other positive/neutral operating signals. Negative
signals remain excluded.

## Minimum acceptance gate

Do not call the dataset train/test-ready until all of the following hold:

- at least 25 strict-positive train companies;
- at least 8 strict-positive calibration companies;
- at least 12 strict-positive test companies;
- at least 10 role families with strict positives;
- at least 4 strict-positive test companies in each company-type stratum;
- no company or alias crosses partitions;
- every evaluated positive has a replayable job artifact;
- every confirmed negative has replayable coverage for the complete horizon;
- all metrics are also reported by company type, sector and role family.

If the strict test gate remains unmet after the 60-company expansion, expand
the frozen cohort again rather than lowering the evidence standard.
