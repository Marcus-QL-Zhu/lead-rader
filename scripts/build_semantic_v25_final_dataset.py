#!/usr/bin/env python3
"""Freeze the strictly unseen 40+20 Semantic v25 acceptance cohort."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import unicodedata
from typing import Any, Iterable

from ht_lead_radar.aggregate_adapters.document_router import (
    DOCUMENT_TYPES,
    route_document,
)
from ht_lead_radar.aggregate_adapters.entity_ledger import (
    build_article_entity_ledger,
)
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


ROOT = Path(__file__).resolve().parents[1]
DOC_TYPES = tuple(sorted(DOCUMENT_TYPES))
FORMAL_PER_TYPE = 8
RESERVE_PER_TYPE = 4
MAX_PER_SOURCE = 6
REQUIRED_EVENT_GROUPS = {
    "funding": frozenset({"funding"}),
    "executive_change": frozenset({"executive_change"}),
    "factory_or_capacity": frozenset({"factory_or_capacity"}),
    "major_order": frozenset({"major_order"}),
    "partnership": frozenset({"partnership"}),
    "technical_milestone": frozenset({"technical_milestone"}),
    "regulatory_or_clinical": frozenset({"regulatory_or_clinical"}),
    "merger_or_listing": frozenset({"merger_acquisition", "ipo_or_listing"}),
}


@dataclass(frozen=True)
class Case:
    key: str
    article: CleanArticle
    body_sha256: str
    normalized_sha256: str
    document_type: str
    company_keys: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    event_types: tuple[str, ...]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _article(payload: dict[str, Any]) -> CleanArticle:
    return CleanArticle(
        index=SourceArticleIndex(**dict(payload["index"])),
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


def _normalized_body(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def _company_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[\s()（）·‘’“”\"']", "", value)
    return re.sub(r"(?:股份有限公司|有限责任公司|有限公司|集团|科技)$", "", value)


def _shingles(value: str, size: int = 7) -> set[str]:
    if len(value) <= size:
        return {value} if value else set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _near_duplicate(left: str, right: str, threshold: float = 0.85) -> bool:
    if not left or not right:
        return False
    ratio = len(left) / len(right)
    if ratio < 0.75 or ratio > 1.34:
        return False
    left_set = _shingles(left)
    right_set = _shingles(right)
    union = left_set | right_set
    return bool(union) and len(left_set & right_set) / len(union) >= threshold


def _db_articles(path: Path) -> list[tuple[str, CleanArticle]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT source_id, source_article_id, article_json "
            "FROM aggregate_clean_articles ORDER BY source_id, source_article_id"
        )
        return [
            (f"{source_id}:{article_id}", _article(json.loads(raw)))
            for source_id, article_id, raw in rows
        ]
    finally:
        connection.close()


def _db_companies(path: Path) -> dict[str, tuple[str, ...]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    output: dict[str, set[str]] = {}
    try:
        for source_id, article_id, raw in connection.execute(
            "SELECT source_id, source_article_id, event_json "
            "FROM aggregate_semantic_events"
        ):
            payload = json.loads(raw)
            company = _company_key(str(payload.get("canonical_company") or ""))
            if company:
                output.setdefault(f"{source_id}:{article_id}", set()).add(company)
    finally:
        connection.close()
    return {key: tuple(sorted(values)) for key, values in output.items()}


def _exclusions(paths: Iterable[Path]) -> dict[str, Any]:
    keys: set[str] = set()
    urls: set[str] = set()
    hashes: set[str] = set()
    normalized: list[str] = []
    companies: set[str] = set()
    for path in paths:
        company_map = _db_companies(path)
        companies.update(value for values in company_map.values() for value in values)
        for key, article in _db_articles(path):
            keys.add(key)
            urls.add(article.index.canonical_url)
            hashes.update(
                value
                for value in (article.index.content_hash, article.content_hash)
                if value
            )
            normalized.append(_normalized_body(article.clean_body))
    return {
        "keys": keys,
        "urls": urls,
        "hashes": hashes,
        "normalized": normalized,
        "companies": companies,
    }


def _merge_bundle_exclusions(
    excluded: dict[str, Any], paths: Iterable[Path]
) -> dict[str, Any]:
    """Add frozen bundle articles to the strict anti-leakage set.

    Frozen bundles do not necessarily have a companion SQLite database.  Rebuild
    their deterministic article/entity projections so a later cohort cannot reuse
    an article, a near-duplicate, or the same operating company.
    """

    for path in paths:
        payload = _read_json(path)
        rows = payload.get("articles")
        if not isinstance(rows, list):
            raise ValueError(f"bundle articles must be a list: {path}")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("article"), dict):
                raise ValueError(f"invalid bundle article row: {path}")
            article = _article(dict(row["article"]))
            key = str(row.get("key") or "").strip()
            if not key:
                key = f"{article.index.source_id}:{article.index.source_article_id}"
            normalized = _normalized_body(article.clean_body)
            excluded["keys"].add(key)
            excluded["urls"].add(article.index.canonical_url)
            excluded["hashes"].update(
                value
                for value in (article.index.content_hash, article.content_hash)
                if value
            )
            excluded["normalized"].append(normalized)

            candidates = MiniMaxSemanticProcessor._event_candidates(article.clean_body)
            ledger = build_article_entity_ledger(article, candidates, ())
            excluded["companies"].update(
                _company_key(entity.canonical_name)
                for entity in ledger.eligible()
                if _company_key(entity.canonical_name)
            )
            excluded["companies"].update(
                _company_key(str(candidate.get("subject_hint") or ""))
                for candidate in candidates
                if _company_key(str(candidate.get("subject_hint") or ""))
            )
    return excluded


def _cases_one(fresh_db: Path, excluded: dict[str, Any]) -> tuple[list[Case], Counter[str]]:
    company_map = _db_companies(fresh_db)
    rejected: Counter[str] = Counter()
    output: list[Case] = []
    for key, article in _db_articles(fresh_db):
        normalized = _normalized_body(article.clean_body)
        body_sha = hashlib.sha256(article.clean_body.encode("utf-8")).hexdigest()
        normalized_sha = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if article.extraction_method == "source_pack_full_body":
            rejected["unvalidated_generic_extraction"] += 1
            continue
        if article.fetch_status != "ok" or len(article.clean_body.strip()) < 80:
            rejected["invalid_or_short"] += 1
            continue
        if (
            key in excluded["keys"]
            or article.index.canonical_url in excluded["urls"]
            or article.index.content_hash in excluded["hashes"]
            or article.content_hash in excluded["hashes"]
        ):
            rejected["exact_prior_overlap"] += 1
            continue
        likely = [
            prior
            for prior in excluded["normalized"]
            if prior
            and 0.75 <= len(normalized) / max(1, len(prior)) <= 1.34
            and normalized[:20] == prior[:20]
        ]
        if any(_near_duplicate(normalized, prior) for prior in likely):
            rejected["near_duplicate_prior"] += 1
            continue
        candidates = MiniMaxSemanticProcessor._event_candidates(article.clean_body)
        companies = set(company_map.get(key, ()))
        companies.update(
            _company_key(str(candidate.get("subject_hint") or ""))
            for candidate in candidates
            if _company_key(str(candidate.get("subject_hint") or ""))
        )
        if companies & excluded["companies"]:
            rejected["prior_company_overlap"] += 1
            continue
        output.append(
            Case(
                key=key,
                article=article,
                body_sha256=body_sha,
                normalized_sha256=normalized_sha,
                document_type=route_document(article).document_type,
                company_keys=tuple(sorted(companies)),
                candidate_ids=tuple(
                    sorted(
                        claim_id
                        for candidate in candidates
                        for claim_id in MiniMaxSemanticProcessor._required_claim_ids(
                            candidate
                        )
                    )
                ),
                event_types=tuple(
                    sorted({str(candidate["event_type"]) for candidate in candidates})
                ),
            )
        )
    return output, rejected


def _cases(
    fresh_db: Path | Iterable[Path], excluded: dict[str, Any]
) -> tuple[list[Case], Counter[str]]:
    paths = [fresh_db] if isinstance(fresh_db, Path) else list(fresh_db)
    by_key: dict[str, Case] = {}
    rejected: Counter[str] = Counter()
    for path in paths:
        rows, path_rejected = _cases_one(path, excluded)
        rejected.update(path_rejected)
        for row in rows:
            prior = by_key.get(row.key)
            if prior is None or row.article.index.discovered_at > prior.article.index.discovered_at:
                by_key[row.key] = row
    return sorted(by_key.values(), key=lambda row: row.key), rejected


def _select_jointly(
    cases: list[Case],
    *,
    seed: int,
    formal_per_type: int = FORMAL_PER_TYPE,
    reserve_per_type: int = RESERVE_PER_TYPE,
    attempts: int = 256,
) -> tuple[list[Case], list[Case], Counter[str]]:
    """Choose both splits together so the formal set cannot starve the reserve.

    The cohort is small (60 rows), while company-family and source-cap constraints
    make a single sequential greedy pass unreliable.  Deterministic restarts keep
    the implementation dependency-free and retain the best partial selection when
    the fresh crawl genuinely cannot satisfy the frozen quotas.
    """

    quota = {
        ("formal", document_type): formal_per_type
        for document_type in DOC_TYPES
    }
    quota.update(
        {
            ("reserve", document_type): reserve_per_type
            for document_type in DOC_TYPES
        }
    )
    normalized = {case.key: _normalized_body(case.article.clean_body) for case in cases}
    normalized_titles = {
        case.key: _normalized_body(case.article.index.title) for case in cases
    }
    near_duplicates: dict[str, set[str]] = {case.key: set() for case in cases}
    for index, left in enumerate(cases):
        for right in cases[index + 1 :]:
            same_substantive_title = (
                len(normalized_titles[left.key]) >= 12
                and normalized_titles[left.key] == normalized_titles[right.key]
            )
            if same_substantive_title or _near_duplicate(
                normalized[left.key], normalized[right.key]
            ):
                near_duplicates[left.key].add(right.key)
                near_duplicates[right.key].add(left.key)

    best: tuple[list[Case], list[Case], Counter[str]] = ([], [], Counter())
    best_score = (-1, -1, -1)
    target = sum(quota.values())
    for attempt in range(max(1, attempts)):
        rng = random.Random(seed + attempt * 104729)
        selected: dict[str, list[Case]] = {"formal": [], "reserve": []}
        selected_keys: set[str] = set()
        split_companies: dict[str, set[str]] = {"formal": set(), "reserve": set()}
        source_counts: Counter[str] = Counter()
        event_counts: Counter[str] = Counter()
        remaining = dict(quota)

        while sum(remaining.values()) > 0:
            eligible_by_slot: dict[tuple[str, str], list[Case]] = {}
            for slot, required in remaining.items():
                if required <= 0:
                    continue
                split, document_type = slot
                other_split = "reserve" if split == "formal" else "formal"
                eligible_by_slot[slot] = [
                    case
                    for case in cases
                    if case.document_type == document_type
                    and case.key not in selected_keys
                    and source_counts[case.article.index.source_id] < MAX_PER_SOURCE
                    and not (set(case.company_keys) & split_companies[other_split])
                    and not (near_duplicates[case.key] & selected_keys)
                ]

            viable_slots = [
                slot for slot, eligible in eligible_by_slot.items() if eligible
            ]
            if not viable_slots:
                break
            slot = min(
                viable_slots,
                key=lambda item: (
                    len(eligible_by_slot[item]) / remaining[item],
                    len(eligible_by_slot[item]),
                    item,
                ),
            )
            split, _ = slot
            candidates = eligible_by_slot[slot]
            formal_event_types = {
                event_type
                for selected_case in selected["formal"]
                for event_type in selected_case.event_types
            }
            missing_groups = {
                group
                for group, allowed in REQUIRED_EVENT_GROUPS.items()
                if not formal_event_types.intersection(allowed)
            }

            def required_group_gain(item: Case) -> int:
                if split != "formal":
                    return 0
                return sum(
                    bool(set(item.event_types).intersection(REQUIRED_EVENT_GROUPS[group]))
                    for group in missing_groups
                )

            rng.shuffle(candidates)
            candidates.sort(
                key=lambda item: (
                    -required_group_gain(item),
                    source_counts[item.article.index.source_id],
                    -sum(max(0, 2 - event_counts[event]) for event in item.event_types),
                    len(set(item.company_keys) & split_companies[split]),
                )
            )
            # Vary the choice among equally useful candidates across restarts.
            best_rank = (
                -required_group_gain(candidates[0]),
                source_counts[candidates[0].article.index.source_id],
                -sum(
                    max(0, 2 - event_counts[event])
                    for event in candidates[0].event_types
                ),
                len(set(candidates[0].company_keys) & split_companies[split]),
            )
            tied = [
                case
                for case in candidates
                if (
                    -required_group_gain(case),
                    source_counts[case.article.index.source_id],
                    -sum(
                        max(0, 2 - event_counts[event]) for event in case.event_types
                    ),
                    len(set(case.company_keys) & split_companies[split]),
                )
                == best_rank
            ]
            case = rng.choice(tied)
            selected[split].append(case)
            selected_keys.add(case.key)
            split_companies[split].update(case.company_keys)
            source_counts[case.article.index.source_id] += 1
            event_counts.update(case.event_types)
            remaining[slot] -= 1

        fulfilled_slots = sum(value == 0 for value in remaining.values())
        formal_event_types = {
            event_type
            for case in selected["formal"]
            for event_type in case.event_types
        }
        covered_event_groups = sum(
            bool(formal_event_types.intersection(allowed))
            for allowed in REQUIRED_EVENT_GROUPS.values()
        )
        score = (len(selected_keys), fulfilled_slots, covered_event_groups)
        if score > best_score:
            best = (selected["formal"], selected["reserve"], source_counts)
            best_score = score
        if (
            len(selected_keys) == target
            and covered_event_groups == len(REQUIRED_EVENT_GROUPS)
        ):
            return selected["formal"], selected["reserve"], source_counts
    return best


def _record(case: Case, split: str) -> dict[str, Any]:
    return {
        "key": case.key,
        "split": split,
        "source_id": case.article.index.source_id,
        "source_article_id": case.article.index.source_article_id,
        "canonical_url": case.article.index.canonical_url,
        "published_at": case.article.index.published_at,
        "title": case.article.index.title,
        "body_sha256": case.body_sha256,
        "normalized_sha256": case.normalized_sha256,
        "document_type": case.document_type,
        "company_keys": list(case.company_keys),
        "candidate_ids": list(case.candidate_ids),
        "candidate_event_types": list(case.event_types),
        "gold_status": "unlabelled",
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    excluded = _exclusions(args.exclude_db)
    excluded = _merge_bundle_exclusions(
        excluded, getattr(args, "exclude_bundle", ()) or ()
    )
    dataset_version = str(
        getattr(args, "dataset_version", "semantic-v25-final-v1")
        or "semantic-v25-final-v1"
    )
    formal_per_type = int(getattr(args, "formal_per_type", FORMAL_PER_TYPE))
    reserve_per_type = int(getattr(args, "reserve_per_type", RESERVE_PER_TYPE))
    if formal_per_type < 1 or reserve_per_type < 0:
        raise ValueError("formal_per_type must be positive and reserve_per_type non-negative")
    cases, rejected = _cases(args.fresh_db, excluded)
    formal, reserve, source_counts = _select_jointly(
        cases,
        seed=args.seed,
        formal_per_type=formal_per_type,
        reserve_per_type=reserve_per_type,
    )
    counts = {
        "formal": Counter(case.document_type for case in formal),
        "reserve": Counter(case.document_type for case in reserve),
    }
    selected_event_types = {
        event_type for case in formal for event_type in case.event_types
    }
    missing_event_groups = sorted(
        group
        for group, allowed in REQUIRED_EVENT_GROUPS.items()
        if not selected_event_types.intersection(allowed)
    )
    ready = (
        all(counts["formal"][kind] == formal_per_type for kind in DOC_TYPES)
        and all(counts["reserve"][kind] == reserve_per_type for kind in DOC_TYPES)
        and not missing_event_groups
    )
    records = [*(_record(case, "formal") for case in formal), *(
        _record(case, "reserve") for case in reserve
    )]
    bundle_payload = {
        "schema_version": 1,
        "dataset_version": dataset_version,
        "articles": [
            {
                "key": case.key,
                "split": split,
                "article": case.article.to_dict(),
            }
            for split, values in (("formal", formal), ("reserve", reserve))
            for case in values
        ],
    }
    bundle_sha = ""
    if ready:
        args.bundle.parent.mkdir(parents=True, exist_ok=True)
        args.bundle.write_text(
            json.dumps(bundle_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        bundle_sha = hashlib.sha256(args.bundle.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "dataset_version": dataset_version,
        "status": "frozen_unlabelled" if ready else "insufficient_fresh_unseen",
        "may_support_final_acceptance": ready,
        "selection_policy": {
            "formal_per_document_type": formal_per_type,
            "reserve_per_document_type": reserve_per_type,
            "maximum_per_source": MAX_PER_SOURCE,
            "required_event_groups": {
                group: sorted(values)
                for group, values in REQUIRED_EVENT_GROUPS.items()
            },
            "strict_prior_article_company_and_near_duplicate_exclusion": True,
            "excluded_frozen_bundles": [
                str(path) for path in (getattr(args, "exclude_bundle", ()) or ())
            ],
        },
        "availability": {
            "eligible_cases": len(cases),
            "by_document_type": dict(Counter(case.document_type for case in cases)),
            "by_source": dict(Counter(case.article.index.source_id for case in cases)),
            "rejected": dict(rejected),
        },
        "selected": {
            "formal": len(formal),
            "reserve": len(reserve),
            "formal_by_document_type": dict(counts["formal"]),
            "reserve_by_document_type": dict(counts["reserve"]),
            "source_counts": dict(source_counts),
            "formal_event_types": sorted(selected_event_types),
            "missing_event_groups": missing_event_groups,
        },
        "bundle_path": str(args.bundle) if ready else "",
        "bundle_sha256": bundle_sha,
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
    parser.add_argument("--fresh-db", type=Path, action="append", required=True)
    parser.add_argument(
        "--exclude-db",
        type=Path,
        action="append",
        default=[],
        help="optional prior capture database; frozen bundles remain the primary exclusion",
    )
    parser.add_argument("--exclude-bundle", type=Path, action="append", default=[])
    parser.add_argument("--dataset-version", default="semantic-v25-final-v1")
    parser.add_argument("--formal-per-type", type=int, default=FORMAL_PER_TYPE)
    parser.add_argument("--reserve-per-type", type=int, default=RESERVE_PER_TYPE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260801)
    return parser


def main() -> int:
    result = build(_parser().parse_args())
    print(json.dumps(result["selected"], ensure_ascii=False, indent=2))
    return 0 if result["may_support_final_acceptance"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
