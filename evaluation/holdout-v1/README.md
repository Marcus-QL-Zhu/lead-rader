# Frozen holdout v1

This cohort is the first out-of-sample check created after the
`historical-demand-v5-required-functional-coverage` prompt was frozen.
None of these companies or later job labels appeared in the v3-v7 pilot
iterations.

## Pre-registered protocol

- Cohort A simulates `2026-01-01` and validates through `2026-04-01`.
- Cohort B simulates `2026-04-01` and validates through `2026-07-01`.
- MiniMax receives only timestamped, non-recruiting evidence published before
  the relevant cutoff.
- Job advertisements, later job titles, analyst notes and JOSINT are excluded
  from prediction packets.
- Workforce-cluster precursors stay disabled.
- The frozen prompt, maximum of five role hypotheses, deterministic
  validation code and role ontology may not be tuned after prediction results
  are opened.

The holdout passes only if all of the following were registered before the
first MiniMax call:

- snapshot audit passes for both non-overlapping cohorts;
- each cohort produces at least one correct Director+ role-family match;
- at least three distinct later Director+ job records match in total;
- matched jobs cover listed, startup/private and foreign companies.

The test deliberately uses count gates rather than a precision percentage.
The public-job label set is incomplete, so an unmatched prediction is
unlabelled rather than automatically false.
## Result

The frozen MiniMax-M3 snapshots produced 9 hypotheses in cohort A and 6 in
cohort B. Initial validation exposed a general ASCII tokenization defect:
`cto` was matching inside `direCTOr`. The model outputs and registered gates
were not changed. The evaluator was corrected to use token boundaries and a
general application-solutions family, then both frozen snapshots were
revalidated.

The corrected reports contain three matches in cohort A and one in cohort B:
four distinct later jobs across listed, startup/private and foreign companies.
The aggregate is a mechanical pass only. This cohort is scientifically invalid
as final acceptance because the matcher changed after labels were opened, all
four companies were selected from known positive outcomes, and one evergreen
source lacked a historical availability capture. It is retained as a failed
diagnostic; strict acceptance moves to holdout v2 with a pre-label candidate
universe, controls and cryptographic input seals.