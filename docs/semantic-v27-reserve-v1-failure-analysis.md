# Semantic V27 Reserve-v1 Failure Analysis

Status: frozen one-time prevalidation failure.

## Method

- `reserve-v1` contained 20 articles pre-frozen before V27 development.
- Two annotators independently labelled the articles without access to predictions.
- A third adjudicator resolved nine case-level disagreements from source text.
- The final Gold packet contains 41 events and 57 complete candidate dispositions.
- V27 predictions were generated once before Gold adjudication and are immutable by
  code/bundle/selection hashes.

## Result

| Metric | Result | Gate |
| --- | ---: | ---: |
| Exact precision | 58.33% | no unsupported events |
| Exact recall | 17.07% | diagnostic only |
| Strong-current recall | 21.88% | at least 90% |
| Company-subject precision | 66.67% | at least 98% |
| Status accuracy | 100.00% | at least 90% |
| Failed claims | 0 | 0 |
| Uncited/ungrounded/missing-host-ID events | 0 | 0 |

The host evidence contract is working, but the deterministic Action Claim
projection does not cover enough unseen language. The formal-v1 development score
therefore represented expression-level overfitting, not production generalization.

## Frozen decision

1. Do not rerun or tune against reserve-v1.
2. Do not inspect reserve-v1 case labels to add article-specific rules.
3. Build a new multi-source `development-v2` from articles, near-duplicates and
   companies absent from the complete V1 bundle.
4. Expand only host-owned entity/action coverage and generic claim contracts on
   `development-v2`; keep MiniMax as a bounded adjudicator.
5. After code freeze, capture a later, strictly unseen `final-v2`; label it blind
   and run exactly once.
6. Production and GitHub remain unchanged until `final-v2` and the engineering
   gates pass.
