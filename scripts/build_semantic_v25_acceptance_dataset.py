#!/usr/bin/env python3
"""Build a diagnostic Semantic v25 cohort from the already-used live corpus.

This cohort is useful for shadow migration and evaluator development.  It is
never the final unseen acceptance set because every article in the source
database has already had at least one semantic attempt.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import random
import re
import sqlite3
from typing import Any, Iterable

from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / ".acceptance" / "server-v23-live.sqlite"
DEFAULT_EXPERIMENT = (
    ROOT / "experiments" / "minimax-input-loop" / "dataset-manifest.json"
)
DEFAULT_OUTPUT = (
    ROOT / "evaluation" / "semantic-v25" / "shadow-diagnostic-manifest.json"
)
DEFAULT_BUNDLE = (
    ROOT / ".acceptance" / "semantic-v25" / "shadow-source-bundle.json"
)
DEFAULT_SEED = 20260801
CALIBRATION_COUNT = 12
TEST_COUNT = 36
CONTROL_COUNT = 12


@dataclass(frozen=True)
class Case:
    source_id: str
    source_article_id: str
    article: CleanArticle
    candidate_ids: tuple[str, ...]
    event_types: tuple[str, ...]
    document_type: str
    known_companies: tuple[str, ...]
    body_sha256: str

    @property
    def key(self) -> str:
        return f"{self.source_id}:{self.source_article_id}"

    @property
    def is_control(self) -> bool:
        return not self.candidate_ids

    def manifest_record(self, split: str) -> dict[str, Any]:
        return {
            "key": self.key,
            "split": split,
            "source_id": self.source_id,
            "source_article_id": self.source_article_id,
            "canonical_url": self.article.index.canonical_url,
            "published_at": self.article.index.published_at,
            "title": self.article.index.title,
            "index_content_hash": self.article.index.content_hash,
            "article_content_hash": self.article.content_hash,
            "body_sha256": self.body_sha256,
            "body_chars": len(self.article.clean_body),
            "document_type": self.document_type,
            "candidate_ids": list(self.candidate_ids),
            "candidate_event_types": list(self.event_types),
            "candidate_count": len(self.candidate_ids),
            "known_company_keys": list(self.known_companies),
            "gold_status": "unlabelled",
        }


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _excluded_keys(manifest: dict[str, Any]) -> set[tuple[str, str]]:
    train = {
        tuple(map(str, pair))
        for batch in manifest.get("train_rounds", {}).values()
        for pair in batch
    }
    holdout = {
        tuple(map(str, pair)) for pair in manifest.get("holdout", [])
    }
    return train | holdout


def _clean_article(payload: dict[str, Any]) -> CleanArticle:
    index = SourceArticleIndex(**dict(payload["index"]))
    return CleanArticle(
        index=index,
        clean_body=str(payload.get("clean_body") or ""),
        author=str(payload.get("author") or ""),
        tags=tuple(payload.get("tags") or ()),
        structured_data=dict(payload.get("structured_data") or {}),
        extraction_method=str(payload.get("extraction_method") or "exact"),
        adaptive_similarity=payload.get("adaptive_similarity"),
        evidence_locators=dict(payload.get("evidence_locators") or {}),
        fetch_status=str(payload.get("fetch_status") or "ok"),
        failure_reason=str(payload.get("failure_reason") or ""),
        content_hash=str(payload.get("content_hash") or ""),
    )


def _company_key(value: str) -> str:
    return re.sub(
        r"(?:股份有限公司|有限责任公司|有限公司|集团|科技)$",
        "",
        re.sub(r"[\s（）()“”\"'·]", "", value),
    ).casefold()


def _article_companies(
    connection: sqlite3.Connection,
) -> dict[tuple[str, str], tuple[str, ...]]:
    output: dict[tuple[str, str], set[str]] = {}
    rows = connection.execute(
        """
        SELECT source_id, source_article_id, event_json
        FROM aggregate_semantic_events
        """
    )
    for source_id, article_id, raw_event in rows:
        event = json.loads(raw_event)
        company = _company_key(str(event.get("canonical_company") or ""))
        if company:
            output.setdefault((source_id, article_id), set()).add(company)
    return {
        key: tuple(sorted(companies)) for key, companies in output.items()
    }


def _load_cases(
    database: Path,
    excluded: set[tuple[str, str]],
) -> tuple[list[Case], set[str]]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    company_map = _article_companies(connection)
    excluded_companies = {
        company
        for key in excluded
        for company in company_map.get(key, ())
    }
    output: list[Case] = []
    seen_bodies: set[str] = set()
    try:
        rows = connection.execute(
            """
            SELECT source_id, source_article_id, article_json
            FROM aggregate_clean_articles
            ORDER BY source_id, source_article_id
            """
        )
        for source_id, article_id, raw_article in rows:
            key = (str(source_id), str(article_id))
            if key in excluded:
                continue
            article = _clean_article(json.loads(raw_article))
            if (
                article.fetch_status != "ok"
                or len(article.clean_body.strip()) < 80
                or len(article.clean_body) > 30000
            ):
                continue
            body_hash = sha256(article.clean_body.encode("utf-8")).hexdigest()
            if body_hash in seen_bodies:
                continue
            seen_bodies.add(body_hash)
            known_companies = company_map.get(key, ())
            if excluded_companies.intersection(known_companies):
                continue
            candidates = MiniMaxSemanticProcessor._event_candidates(
                article.clean_body
            )
            route = route_document(article)
            output.append(
                Case(
                    source_id=key[0],
                    source_article_id=key[1],
                    article=article,
                    candidate_ids=tuple(
                        str(candidate["claim_id"]) for candidate in candidates
                    ),
                    event_types=tuple(
                        sorted(
                            {
                                str(candidate["event_type"])
                                for candidate in candidates
                            }
                        )
                    ),
                    document_type=route.document_type,
                    known_companies=known_companies,
                    body_sha256=body_hash,
                )
            )
    finally:
        connection.close()
    return output, excluded_companies


def _stable_order(cases: Iterable[Case], seed: int) -> list[Case]:
    cases = list(cases)
    random.Random(seed).shuffle(cases)
    return cases


def _select_cases(cases: list[Case], seed: int) -> list[Case]:
    controls = _stable_order(
        (case for case in cases if case.is_control),
        seed + 1,
    )
    signals = _stable_order(
        (case for case in cases if not case.is_control),
        seed + 2,
    )
    selected: list[Case] = []
    source_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    document_counts: Counter[str] = Counter()

    def take(case: Case) -> None:
        selected.append(case)
        source_counts[case.source_id] += 1
        document_counts[case.document_type] += 1
        event_counts.update(case.event_types)

    while signals and len(selected) < CALIBRATION_COUNT + TEST_COUNT - CONTROL_COUNT:
        case = max(
            signals,
            key=lambda item: (
                sum(max(0, 2 - event_counts[event]) for event in item.event_types),
                max(0, 3 - source_counts[item.source_id]),
                max(0, 4 - document_counts[item.document_type]),
                -len(item.candidate_ids),
                item.key,
            ),
        )
        signals.remove(case)
        take(case)
    for case in controls[:CONTROL_COUNT]:
        take(case)
    if len(selected) != CALIBRATION_COUNT + TEST_COUNT:
        raise RuntimeError(
            f"only selected {len(selected)} cases; "
            f"need {CALIBRATION_COUNT + TEST_COUNT}"
        )
    return selected


def _split_cases(cases: list[Case], seed: int) -> dict[str, list[Case]]:
    ordered = _stable_order(cases, seed + 3)
    parents = list(range(len(ordered)))

    def find(position: int) -> int:
        while parents[position] != position:
            parents[position] = parents[parents[position]]
            position = parents[position]
        return position

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    company_owner: dict[str, int] = {}
    for position, case in enumerate(ordered):
        for company in case.known_companies:
            if company in company_owner:
                union(position, company_owner[company])
            else:
                company_owner[company] = position
    groups_by_root: dict[int, list[Case]] = {}
    for position, case in enumerate(ordered):
        groups_by_root.setdefault(find(position), []).append(case)
    groups = list(groups_by_root.values())
    choices: dict[int, tuple[int, ...]] = {0: ()}
    for group_index, group in enumerate(groups):
        for total, chosen in sorted(choices.items(), reverse=True):
            new_total = total + len(group)
            if new_total <= CALIBRATION_COUNT and new_total not in choices:
                choices[new_total] = (*chosen, group_index)
    if CALIBRATION_COUNT not in choices:
        raise RuntimeError(
            "cannot create an exact company-group-disjoint calibration split"
        )
    calibration_group_ids = set(choices[CALIBRATION_COUNT])
    calibration = [
        case
        for group_index, group in enumerate(groups)
        if group_index in calibration_group_ids
        for case in group
    ]
    test = [
        case
        for group_index, group in enumerate(groups)
        if group_index not in calibration_group_ids
        for case in group
    ]
    if len(calibration) != CALIBRATION_COUNT or len(test) != TEST_COUNT:
        raise RuntimeError(
            f"invalid split sizes: calibration={len(calibration)}, "
            f"test={len(test)}"
        )
    return {"calibration": calibration, "test": test}


def _summary(splits: dict[str, list[Case]]) -> dict[str, Any]:
    all_cases = [case for values in splits.values() for case in values]
    return {
        "case_count": len(all_cases),
        "split_counts": {
            name: len(values) for name, values in splits.items()
        },
        "control_count": sum(case.is_control for case in all_cases),
        "source_counts": dict(
            sorted(Counter(case.source_id for case in all_cases).items())
        ),
        "document_type_counts": dict(
            sorted(Counter(case.document_type for case in all_cases).items())
        ),
        "candidate_event_type_counts": dict(
            sorted(
                Counter(
                    event_type
                    for case in all_cases
                    for event_type in case.event_types
                ).items()
            )
        ),
        "known_company_count": len(
            {
                company
                for case in all_cases
                for company in case.known_companies
            }
        ),
        "unknown_company_case_count": sum(
            not case.known_companies for case in all_cases
        ),
    }


def _write_bundle(path: Path, splits: dict[str, list[Case]]) -> str:
    records = []
    for split, cases in splits.items():
        for case in cases:
            records.append(
                {
                    "key": case.key,
                    "split": split,
                    "article": case.article.to_dict(),
                }
            )
    payload = {
        "schema_version": 1,
        "purpose": "semantic-v25-shadow-diagnostic-labelling",
        "articles": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return _sha256_file(path)


def build(args: argparse.Namespace) -> dict[str, Any]:
    prior_manifest = _load_json(args.experiment_manifest)
    excluded = _excluded_keys(prior_manifest)
    cases, excluded_companies = _load_cases(args.database, excluded)
    selected = _select_cases(cases, args.seed)
    splits = _split_cases(selected, args.seed)
    body_hashes = [
        case.body_sha256 for values in splits.values() for case in values
    ]
    if len(body_hashes) != len(set(body_hashes)):
        raise RuntimeError("duplicate body hash in acceptance cohort")
    calibration_companies = {
        company
        for case in splits["calibration"]
        for company in case.known_companies
    }
    test_companies = {
        company
        for case in splits["test"]
        for company in case.known_companies
    }
    if calibration_companies.intersection(test_companies):
        raise RuntimeError("known company leakage across acceptance splits")
    bundle_hash = _write_bundle(args.bundle, splits)
    records = [
        case.manifest_record(split)
        for split, values in splits.items()
        for case in values
    ]
    manifest = {
        "schema_version": 1,
        "dataset_version": "semantic-v25-shadow-diagnostic-v1",
        "status": "diagnostic_not_final",
        "seed": args.seed,
        "selection_policy": {
            "calibration_count": CALIBRATION_COUNT,
            "test_count": TEST_COUNT,
            "control_count": CONTROL_COUNT,
            "minimum_body_chars": 80,
            "maximum_body_chars": 30000,
            "exclude_prior_minimax_loop_articles": True,
            "exclude_known_prior_company_keys": True,
            "event_type_target_per_type": 2,
            "source_diversity_target_per_source": 3,
        },
        "inputs": {
            "database_path": _display_path(args.database),
            "database_sha256": _sha256_file(args.database),
            "prior_experiment_manifest": _display_path(args.experiment_manifest),
            "prior_experiment_manifest_sha256": _sha256_file(
                args.experiment_manifest
            ),
            "excluded_article_count": len(excluded),
            "excluded_known_company_count": len(excluded_companies),
            "source_bundle_path": _display_path(args.bundle),
            "source_bundle_sha256": bundle_hash,
        },
        "leakage_audit": {
            "body_hashes_unique": True,
            "known_company_keys_disjoint_between_splits": True,
            "unknown_company_cases_require_gold_recheck": True,
            "strictly_unseen": False,
            "reason": (
                "source database predates the claim-contract freeze and every "
                "article has an existing semantic attempt"
            ),
            "may_support_final_acceptance": False,
        },
        "summary": _summary(splits),
        "cases": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--experiment-manifest",
        type=Path,
        default=DEFAULT_EXPERIMENT,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser


def main() -> int:
    manifest = build(_parser().parse_args())
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
