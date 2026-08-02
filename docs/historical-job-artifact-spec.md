# Historical Director+ Job Artifact Specification

Status: frozen for Semantic v25/v26 historical evaluation.

## Purpose

Historical hiring labels are outcome evidence, not model inputs. A search-result
snippet, a company-page listing, or a manually copied title is never an
evaluation label. A positive label must be reproducible from a captured,
content-addressed job-detail artifact.

## Required artifact envelope

Every eligible job row must contain:

- identity: `artifact_id`, `source_platform`, `source_job_id`;
- retrieval: `requested_url`, `final_url`, `captured_at`,
  `http_status=200`, `mime_type`;
- raw artifact: root-relative path and SHA256;
- normalized UTF-8 text: root-relative path, SHA256, and
  `extractor_version`;
- exact evidence spans for job ID, title, employer, publication text, and at
  least one responsibility/scope passage;
- entity identity: `canonical_company_id`, `corporate_family_id`, and
  `employer_match_basis`;
- publication interval: original publication phrase, exact span, parse basis,
  half-open interval, parser version, and timezone;
- seniority decision: label, rule version, eligibility, and exclusion reason;
- human review: reviewer, review time, and `review_status=approved`.

Every span is `{text, char_start, char_end}` and must reproduce exactly from
the hashed normalized text. Paths must resolve inside the declared artifact
root.

## Eligibility

- Test labels include Director, VP, General Manager, and CXO roles.
- A Head title is eligible only when its source scope explicitly establishes
  team, organization, budget, P&L, business-result, department, business-unit,
  or strategy ownership.
- Manager, expert, engineer, Principal, Staff, and Chief Engineer titles are
  excluded from evaluation even if production continues to analyze them.
- Employer display must match the pool company. A subsidiary or brand needs a
  separately replayable relationship artifact and
  `employer_match_basis=verified_relationship_artifact`.
- A URL or platform/job-ID pair may not map to two canonical companies.
- All members of one `corporate_family_id` inherit one dataset split.

## Date semantics

- Explicit publication dates are preferred.
- Relative phrases produce a conservative half-open date interval; crawler
  capture time is not itself the publication date.
- A positive outcome is assigned only when the complete interval lies inside
  the future horizon.
- Any partial overlap with a horizon remains `unknown`, never positive or
  negative.

## Negative labels

A current company-page snapshot cannot prove that no job was posted and later
closed. A negative label therefore requires a complete daily Director+ listing
archive for every day of the horizon, with replayable hashed coverage
artifacts. Without that coverage, the label is `unknown`.

## Split and review gate

- Freeze by canonical company and corporate family before tuning.
- Test-set entity, date, and seniority decisions receive independent human
  review.
- Any artifact/hash/span mismatch, entity ambiguity, duplicate identity, or
  cross-family split leakage fails closed.
