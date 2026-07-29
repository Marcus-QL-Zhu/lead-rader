# Post-V15 calibration round 1

Status: **rejected; no model promoted**.

This is the first data-driven iteration after the frozen V15 retrospective holdout. It is not a V16 holdout. V15, the final test partition, the prompt, the role ontology, and the matching protocol were not changed.

## Development boundary

- Input: `evaluation/training-v1/dataset.json`
- Allowed partitions: `train`, `calibration`
- Forbidden partition: `test`
- V15 is referenced only by immutable file hashes.
- The hiring-propensity model remains disabled because there are no replayable confirmed negatives.
- Provisional labels from `training-v3` are not used.

The manifest fixed the feature-policy, regularization, and learned/rule blend grid before the run. Promotion required strict improvement on at least two of Top-1, Precision@20, and macro-F1, with no company-type Top-1 regression above 12.5 percentage points.

## Result

The frozen current logistic baseline remained:

- Top-1: 20.83%
- Precision@20: 25.00%
- macro-F1: 2.87%
- foreign Top-1: 62.50%
- listed Top-1: 0%
- startup/private Top-1: 0%

No candidate passed the gate. The best diversity-oriented candidate (`portable-l2-0.03-learned-0.5`) improved macro-F1 to 4.92%, but reduced Top-1 to 12.50%, Precision@20 to 15.00%, and foreign Top-1 to 37.50%.

The dominant limitation is the labeled development distribution, not another prompt or hyperparameter defect: every positive company currently assigned to the training partition is foreign. The listed and startup/private positive companies occur only in calibration, so the learned ranker has no strict cross-company training examples for those company types.

## Decision

Keep the current model as the baseline and do not claim improvement. The next valid iteration must first add strict, replayable listed and startup/private Director+ labels to the training partition while preserving company isolation. Repartitioning current calibration companies after seeing their labels would contaminate the comparison and is therefore not used as a shortcut.