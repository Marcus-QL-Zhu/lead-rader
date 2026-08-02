#!/usr/bin/env python3
"""Run blinded MiniMax prompt/input experiments on frozen aggregate articles."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
from hashlib import sha1
import json
from pathlib import Path
import random
import re
import sqlite3
import time
from typing import Any
import unicodedata

from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
)
from ht_lead_radar.collectors import load_env_file
from ht_lead_radar.openclaw_llm import (
    OpenClawConfiguredLLMRunner,
    OpenClawLLMConfig,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "experiments" / "minimax-input-loop" / "dataset-manifest.json"
DEFAULT_OUTPUT = ROOT / ".acceptance" / "minimax-input-loop"
DEFAULT_DB = ROOT / ".acceptance" / "server-v23-live.sqlite"
DEFAULT_ENV = ROOT.parent / "personal development app" / ".env"

EVENT_TYPES = (
    "funding,executive_change,factory_or_capacity,major_order,partnership,"
    "technical_milestone,new_site_or_entity,regulatory_or_clinical,"
    "policy_or_standard,procurement_tender,customer_validation,"
    "merger_acquisition,ipo_or_listing,enterprise_system"
)
REJECTION_CODES = (
    "funding_use_or_plan,historical_or_reference,generic_commentary,"
    "capability_description,invalid_subject,duplicate_summary"
)

FUTURE_CAPACITY_RE = re.compile(
    r"(?:\u8ba1\u5212|\u62df|\u5c06|\u9884\u8ba1|\u76ee\u6807|\u529b\u4e89)"
    r".{0,24}(?:\u91cf\u4ea7|\u6295\u4ea7|\u6269\u4ea7|\u4ea7\u80fd|\u4ea7\u7ebf)"
)

R2_CAPACITY_RULE = (
    "Type precedence: an explicit plan or target for mass production, "
    "production launch, capacity expansion, capacity growth, or production-line "
    "construction must be factory_or_capacity. Use target when it has not begun, "
    "or started when construction or execution has begun. Do not classify it as "
    "technical_milestone merely because a product or technology is mentioned. "
    "A named product that has already achieved mass production, without a factory, "
    "line, expansion, or capacity action, may be technical_milestone. "
)

R3_TIME_RULE = (
    "Use published_at as the only time anchor. An event before publication is not "
    "automatically historical: an explicit event in the same calendar year is a "
    "current report unless the article marks it as retrospective with words such "
    "as previously, looking back, formerly, or in an earlier round. A company "
    "formed in the first half of the publication year is a completed "
    "new_site_or_entity event. An action already underway (talks, contacts, "
    "fundraising, construction, negotiation) is started even when the same sentence "
    "states a target amount. Use target only when the action itself has not begun. "
)

R3_REFERENCE_RULE = (
    "Resolve a company alias only when the article explicitly defines it. A pronoun "
    "or generic company reference may point only to the nearest unique operating "
    "company earlier in the same paragraph; stop at a paragraph boundary or another "
    "plausible operating company. Never use an investor, person, government, media, "
    "or association as the company. Every evidence quote must be copied as one "
    "continuous verbatim substring of article_text: never insert, delete, normalize, "
    "correct, paraphrase, or join words. The first quote must contain both an explicit "
    "company antecedent and the event action. If no such continuous span exists, "
    "reject invalid_subject or report ambiguity instead of guessing. "
)

R3_FIELD_RULE = (
    "Before output, silently validate each event. company, event_type, event_status, "
    "evidence_quotes, and covered_candidate_ids must exist and be non-null; the first "
    "three must be non-empty and both list fields must be arrays. Every non-empty "
    "round, amount, cumulative amount, and investor must appear verbatim in that "
    "event's evidence quotes. Fill cumulative_funding_amount only when the evidence "
    "explicitly says cumulative, total to date, in total, as of now, or since founding "
    "next to the amount. A current-round amount belongs only in funding_amount. "
    "Multiple rounds without an explicit aggregate amount leave cumulative amount "
    "empty. Every evidence quote must be a continuous verbatim article substring. "
)

R4_ACTION_RULE = (
    "Status follows whether the specific action occurred. Affirmative joined, has "
    "joined, entered, fully committed to, took office, formally established, "
    "launched, released, and went live are completed. Use started only for an "
    "explicit process already underway, and target only for an action not yet begun. "
    "In the publication year, a formally established strategy together with a "
    "concrete launched product, platform, model, or system is a current completed "
    "technical_milestone. Extract at least the concrete launch; do not turn a pure "
    "strategy slogan without a completed product or technical action into an event. "
)

R4_SECTION_RULE = (
    "input.article.sections covers the complete original article in order. Scan every "
    "section before adjudicating candidates; do not stop after the first event and do "
    "not inspect only sections linked to candidates. section_id and character offsets "
    "are locators, not source text. Every evidence quote must be copied only from a "
    "section.text and must remain a continuous substring of the unlabelled original "
    "article. Never include a section label or JSON syntax in evidence. "
)

R4_LOCATOR_RULE = (
    "Locate all strong current events in the complete article and adjudicate every "
    "candidate exactly once. Do not extract investors or financing amounts yet. "
    "Each accepted event needs a unique locator_id, non-empty company, event_type, "
    "event_status, a continuous verbatim primary_quote containing both company and "
    "action, and covered_candidate_ids. Scan the entire article. A confirmed joined, "
    "entered, took office, formally established, launched, released, or went live "
    "action is completed; an explicit process underway is started; an action not yet "
    "begun is target. "
)

R5_TITLE_RULE = (
    "The input title is an attention cue, never evidence. If it affirmatively names "
    "an operating company and a concrete event action, find body evidence and "
    "adjudicate that real action instead of silently omitting it. A question, opinion, "
    "prediction, list, or generic industry title does not create an event. The body "
    "always controls company, action, and status, and evidence quotes must come only "
    "from article_text. If an affirmative title claim cannot be grounded, report an "
    "ambiguity rather than hallucinating it. "
)

R5_ATOMIC_RULE = (
    "Split a compound sentence into atomic company actions before assigning status. "
    "A completed financing, approval, trial, or launch and a separate next-round, "
    "clinical, construction, expansion, production, or listing plan are separate "
    "events when each action can stand alone. Do not propagate completed to the later "
    "action. An already launched process is started; a stated future milestone is "
    "target. A concrete company roadmap target is valid, while an industry vision or "
    "media prediction is not. Explicit prior-year or retrospective background is not "
    "a current event, even if technically notable. "
)

R5_LEDGER_RULE = (
    "title, lead, highlight_ledger, and compact_strong_candidates are attention and "
    "location aids, not evidence. The full article appears once in article_text. "
    "Adjudicate every h_ and c_ id exactly once through one event's "
    "covered_candidate_ids or one rejection. Several ids may cover one real event, "
    "but one id may not be copied across events. A title highlight must be grounded in "
    "the body; a question or commentary title should be rejected. A timeline highlight "
    "may be current, future target, or historical background and must be classified by "
    "the body. body_span is a locator only. Evidence remains a continuous verbatim "
    "article_text substring. Scan the full article for uncued strong events too. "
)

R5_TIME_RE = re.compile(
    r"20\d{2}\u5e74|\u4eca\u5e74|\u672c\u6708|\u8fd1\u65e5|"
    r"\u8fd1\u671f|\u65e5\u524d|\u4e0a\u534a\u5e74|\u4e0b\u534a\u5e74|"
    r"\u5e74\u521d|\u5e74\u5e95|\u660e\u5e74|\u672a\u6765|"
    r"\u6b64\u524d|\u66fe\u4e8e|\u53bb\u5e74|\u56de\u987e|\u5f53\u65f6"
)
R5_ACTION_RE = re.compile(
    r"\u878d\u8d44|\u6536\u8d2d|\u5e76\u8d2d|\u52a0\u76df|"
    r"\u52a0\u5165|\u5165\u5c40|\u6295\u8eab|\u8f9e\u4efb|"
    r"\u51fa\u4efb|\u4efb\u547d|\u53d1\u5e03|\u63a8\u51fa|"
    r"\u4e0a\u7ebf|\u83b7\u6279|\u4e34\u5e8a|\u5efa\u8bbe|"
    r"\u6295\u4ea7|\u91cf\u4ea7|\u6269\u4ea7|\u4ea7\u80fd|"
    r"\u4e2d\u8bd5|\u4ea7\u7ebf|\u8ba2\u5355|IPO|\u4e0a\u5e02"
)

COMMON_RULES = (
    "只依据输入正文，不使用外部知识。company 必须是承担动作的经营公司；"
    "不能是投资方、媒体、政府、协会、人物或多个主体拼接。"
    "completed 表示已完成；started 表示已经启动；target 表示明确的将、拟、"
    "计划、预计。静态能力、融资资金用途、行业评论和明确历史回顾不是当前事件。"
    "公司、轮次、金额、投资方和证据必须能在正文逐字找到；不确定字段留空。"
    "第一条证据必须是正文连续原文，并同时支持主体和事件动作。"
    f"event_type 仅限 {EVENT_TYPES}。拒绝码仅限 {REJECTION_CODES}。"
    "只输出一个严格 JSON 对象，不要 Markdown。"
)

POSITIVE_EXAMPLE = {
    "input": {
        "published_at": "2026-07-01",
        "article_text": (
            "星河芯片宣布完成1亿元A轮融资，由远山资本领投。公司计划明年建设新产线。"
        ),
    },
    "output": {
        "events": [
            {
                "company": "星河芯片",
                "event_type": "funding",
                "event_status": "completed",
                "funding_round": "A轮",
                "funding_amount": "1亿元",
                "cumulative_funding_amount": "",
                "investors": ["远山资本"],
                "evidence_quotes": ["星河芯片宣布完成1亿元A轮融资，由远山资本领投。"],
            },
            {
                "company": "星河芯片",
                "event_type": "factory_or_capacity",
                "event_status": "target",
                "funding_round": "",
                "funding_amount": "",
                "cumulative_funding_amount": "",
                "investors": [],
                "evidence_quotes": ["公司计划明年建设新产线。"],
            },
        ],
        "rejections": [],
        "ambiguities": [],
    },
}

NEGATIVE_EXAMPLE = {
    "input": {
        "article_text": (
            "公司将本轮资金用于扩产。其产品具备量产交付能力。"
            "回顾来看，公司于2024年完成天使轮融资。"
        )
    },
    "output": {
        "events": [],
        "rejections": [
            {"id": "c_1", "reason_code": "funding_use_or_plan"},
            {"id": "c_2", "reason_code": "capability_description"},
        ],
        "ambiguities": ["2024年融资为历史回顾。"],
    },
}


@dataclass(frozen=True)
class ExperimentArticle:
    source_id: str
    source_article_id: str
    article: dict[str, Any]
    rule_events: tuple[dict[str, Any], ...]

    @property
    def key(self) -> str:
        return f"{self.source_id}:{self.source_article_id}"

    @property
    def body(self) -> str:
        return str(self.article.get("clean_body") or "")

    @property
    def index(self) -> dict[str, Any]:
        return dict(self.article.get("index") or {})


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_articles(
    db_path: Path,
    keys: list[list[str]],
) -> list[ExperimentArticle]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    output: list[ExperimentArticle] = []
    try:
        for source_id, article_id in keys:
            row = connection.execute(
                """
                SELECT article_json
                FROM aggregate_clean_articles
                WHERE source_id = ? AND source_article_id = ?
                """,
                (source_id, article_id),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"frozen article missing: {source_id}:{article_id}")
            rule_rows = connection.execute(
                """
                SELECT event_json
                FROM aggregate_semantic_events
                WHERE source_id = ? AND source_article_id = ?
                  AND processor LIKE 'rules%'
                ORDER BY event_key
                """,
                (source_id, article_id),
            ).fetchall()
            output.append(
                ExperimentArticle(
                    source_id=source_id,
                    source_article_id=article_id,
                    article=json.loads(row["article_json"]),
                    rule_events=tuple(
                        json.loads(rule_row["event_json"]) for rule_row in rule_rows
                    ),
                )
            )
    finally:
        connection.close()
    return output


def _candidates(article: ExperimentArticle) -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "hint_event_type": item["event_type"],
            "funding_round_hint": item.get("funding_round", ""),
            "subject_hint": item.get("subject_hint", ""),
            "evidence_window": item["quote"],
        }
        for item in MiniMaxSemanticProcessor._event_candidates(article.body)
    ]


def _rule_seeds(article: ExperimentArticle) -> list[dict[str, Any]]:
    output = []
    for item in article.rule_events:
        event = dict(item)
        material = "\0".join(
            (
                str(event.get("canonical_company") or ""),
                str(event.get("event_type") or ""),
                str(event.get("funding_round") or ""),
                str(event.get("event_status") or ""),
                str((event.get("evidence_quotes") or [""])[0]),
            )
        )
        output.append(
            {
                "id": f"rs_{sha1(material.encode('utf-8')).hexdigest()[:10]}",
                "company": event.get("canonical_company", ""),
                "event_type": event.get("event_type", ""),
                "funding_round": event.get("funding_round", ""),
                "event_status": event.get("event_status", ""),
                "evidence_quotes": event.get("evidence_quotes", []),
            }
        )
    return output


def _as_clean_article(article: ExperimentArticle) -> CleanArticle:
    payload = dict(article.article)
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


def _as_semantic_event(payload: dict[str, Any]) -> SemanticEvent:
    allowed = {field.name for field in fields(SemanticEvent)}
    values = {key: value for key, value in payload.items() if key in allowed}
    for key in (
        "company_mentions",
        "industry_tags",
        "investors",
        "evidence_quotes",
        "ambiguities",
        "claim_ids",
        "span_ids",
    ):
        values[key] = tuple(values.get(key) or ())
    return SemanticEvent(**values)


def _production_projection(
    article: ExperimentArticle,
    raw_output: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(raw_output, dict):
        return {"events": [], "audit": {}, "error": "raw output is not an object"}
    processor = MiniMaxSemanticProcessor(None)
    try:
        events = processor.project_payload(
            _as_clean_article(article),
            [_as_semantic_event(item) for item in article.rule_events],
            raw_output,
            raw_response=json.dumps(raw_output, ensure_ascii=False),
        )
    except Exception as exc:
        return {
            "events": [],
            "audit": dict(processor.last_audit),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "events": [event.to_dict() for event in events],
        "audit": dict(processor.last_audit),
        "error": "",
    }


def _projected_output(
    raw_output: dict[str, Any] | None,
    projection: dict[str, Any],
) -> dict[str, Any]:
    raw = raw_output if isinstance(raw_output, dict) else {}
    audit = projection.get("audit") or {}
    rejected_ids = {
        str(item)
        for key in ("rejected_candidate_ids", "explicitly_rejected_seed_ids")
        for item in (audit.get(key) or [])
    }
    rejections = [
        item
        for item in (raw.get("rejections") or [])
        if isinstance(item, dict) and str(item.get("id") or "") in rejected_ids
    ]
    return {
        "events": list(projection.get("events") or []),
        "rejections": rejections,
        "ambiguities": list(raw.get("ambiguities") or []),
    }


def _projection_mechanical(projection: dict[str, Any]) -> dict[str, Any]:
    audit = projection.get("audit") or {}
    error = str(projection.get("error") or "")
    missing = list(audit.get("unmapped_candidate_ids") or [])
    return {
        "json_object": not error,
        "event_count": len(projection.get("events") or []),
        "candidate_count": int(audit.get("candidate_count") or 0),
        "coverage_complete": not error and not missing,
        "missing_ids": missing,
        "projection_error": error,
    }


def _span(body: str, quote: str) -> tuple[int, int]:
    start = body.find(quote)
    return (start, start + len(quote)) if start >= 0 else (-1, -1)


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    if left[0] < 0 or right[0] < 0:
        return False
    return max(left[0], right[0]) <= min(left[1], right[1]) + 120


def _evidence_clusters(article: ExperimentArticle) -> list[dict[str, Any]]:
    candidates = _candidates(article)
    spans = [_span(article.body, item["evidence_window"]) for item in candidates]
    parents = list(range(len(candidates)))

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            left_item, right_item = candidates[left], candidates[right]
            if left_item["hint_event_type"] != right_item["hint_event_type"]:
                continue
            if (
                left_item["hint_event_type"] == "funding"
                and left_item["funding_round_hint"]
                and right_item["funding_round_hint"]
                and left_item["funding_round_hint"] != right_item["funding_round_hint"]
            ):
                continue
            if (
                left_item["subject_hint"]
                and right_item["subject_hint"]
                and left_item["subject_hint"] != right_item["subject_hint"]
            ):
                continue
            if _overlaps(spans[left], spans[right]):
                union(left, right)

    groups: dict[int, list[int]] = {}
    for position in range(len(candidates)):
        groups.setdefault(find(position), []).append(position)
    clusters: list[dict[str, Any]] = []
    for members in groups.values():
        items = [candidates[position] for position in members]
        evidence: list[dict[str, Any]] = []
        seen_quotes: set[str] = set()
        for position in members:
            quote = candidates[position]["evidence_window"]
            if quote in seen_quotes:
                continue
            seen_quotes.add(quote)
            evidence.append({"span": list(spans[position]), "text": quote})
        event_type = items[0]["hint_event_type"]
        round_values = [
            item["funding_round_hint"] for item in items if item["funding_round_hint"]
        ]
        material = "\0".join(
            [event_type, round_values[0] if round_values else ""]
            + sorted(item["id"] for item in items)
        )
        clusters.append(
            {
                "cluster_id": f"ec_{sha1(material.encode('utf-8')).hexdigest()[:12]}",
                "event_type_hint": event_type,
                "funding_round_hint": round_values[0] if round_values else "",
                "subject_hints": list(
                    dict.fromkeys(
                        item["subject_hint"] for item in items if item["subject_hint"]
                    )
                ),
                "member_candidate_ids": [item["id"] for item in items],
                "evidence": evidence,
            }
        )
    return sorted(
        clusters,
        key=lambda item: min(
            (span["span"][0] for span in item["evidence"] if span["span"][0] >= 0),
            default=10**12,
        ),
    )


def _variant_a(article: ExperimentArticle) -> tuple[str, str]:
    system = (
        COMMON_RULES + "逐项裁决 strong_candidates：每个 id 必须恰好被一个 event 的 "
        "covered_candidate_ids 覆盖，或被 rejection 拒绝。允许补充正文明确支持的漏检事件。"
    )
    payload = {
        "few_shot": {"positive": POSITIVE_EXAMPLE, "negative": NEGATIVE_EXAMPLE},
        "input": {
            "published_at": article.index.get("published_at", ""),
            "article_text": article.body,
            "strong_candidates": _candidates(article),
        },
        "output_schema": {
            "events": [
                {
                    "company": "",
                    "event_type": "",
                    "event_status": "",
                    "funding_round": "",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "evidence_quotes": [],
                    "covered_candidate_ids": [],
                }
            ],
            "rejections": [{"id": "", "reason_code": ""}],
            "ambiguities": [],
        },
    }
    return system, json.dumps(payload, ensure_ascii=False)


def _candidate_key(item: dict[str, Any]) -> str:
    text = unicodedata.normalize("NFKC", str(item.get("evidence_window") or ""))
    return re.sub(
        r"[\s,;:]+", "", text.translate(str.maketrans("\uff0c\uff1b\uff1a", ",;:"))
    )


def _compressed_candidates(article: ExperimentArticle) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in _candidates(article):
        grouped.setdefault(_candidate_key(item), []).append(item)
    output: list[dict[str, Any]] = []
    for items in grouped.values():
        evidence = str(items[0].get("evidence_window") or "")
        if FUTURE_CAPACITY_RE.search(evidence):
            chosen = min(items, key=lambda item: str(item["id"]))
            output.append(
                {
                    **chosen,
                    "hint_event_type": "factory_or_capacity",
                }
            )
            continue
        by_type: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_type.setdefault(str(item["hint_event_type"]), []).append(item)
        for same_type in by_type.values():
            output.append(min(same_type, key=lambda item: str(item["id"])))
    return sorted(
        output,
        key=lambda item: (
            article.body.find(str(item.get("evidence_window") or "")),
            str(item["id"]),
        ),
    )


def _candidate_output_schema() -> dict[str, Any]:
    return {
        "events": [
            {
                "company": "",
                "event_type": "",
                "event_status": "",
                "funding_round": "",
                "funding_amount": "",
                "cumulative_funding_amount": "",
                "investors": [],
                "evidence_quotes": [],
                "covered_candidate_ids": [],
            }
        ],
        "rejections": [{"id": "", "reason_code": ""}],
        "ambiguities": [],
    }


def _round2_variant(
    article: ExperimentArticle,
    variant: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    candidates = (
        _compressed_candidates(article) if variant == "A" else _candidates(article)
    )
    adjudication = (
        "Adjudicate every strong_candidates id exactly once: cover it in one "
        "event.covered_candidate_ids or reject it once. Candidate hints are recall "
        "aids, not facts or final classifications. Events clearly supported by the "
        "full article may be added even without a candidate. "
    )
    if variant == "A":
        system = COMMON_RULES + adjudication + R2_CAPACITY_RULE
        payload = {
            "few_shot": {"positive": POSITIVE_EXAMPLE, "negative": NEGATIVE_EXAMPLE},
            "input": {
                "published_at": article.index.get("published_at", ""),
                "article_text": article.body,
                "strong_candidates": candidates,
            },
            "output_schema": _candidate_output_schema(),
        }
    elif variant == "B":
        system = COMMON_RULES + adjudication + R2_CAPACITY_RULE
        boundary_example = {
            "input": {
                "article_text": (
                    "\u661f\u6cb3\u82af\u7247\u8ba1\u5212\u660e\u5e74"
                    "\u5b9e\u73b0\u65b0\u4ea7\u7ebf\u91cf\u4ea7\u3002"
                ),
                "strong_candidates": [
                    {
                        "id": "c_demo",
                        "hint_event_type": "technical_milestone",
                        "evidence_window": (
                            "\u661f\u6cb3\u82af\u7247\u8ba1\u5212\u660e\u5e74"
                            "\u5b9e\u73b0\u65b0\u4ea7\u7ebf\u91cf\u4ea7\u3002"
                        ),
                    }
                ],
            },
            "output": {
                "events": [
                    {
                        "company": "\u661f\u6cb3\u82af\u7247",
                        "event_type": "factory_or_capacity",
                        "event_status": "target",
                        "funding_round": "",
                        "funding_amount": "",
                        "cumulative_funding_amount": "",
                        "investors": [],
                        "evidence_quotes": [
                            "\u661f\u6cb3\u82af\u7247\u8ba1\u5212\u660e\u5e74"
                            "\u5b9e\u73b0\u65b0\u4ea7\u7ebf\u91cf\u4ea7\u3002"
                        ],
                        "covered_candidate_ids": ["c_demo"],
                    }
                ],
                "rejections": [],
                "ambiguities": [],
            },
        }
        payload = {
            "input": {
                "published_at": article.index.get("published_at", ""),
                "article_text": article.body,
                "strong_candidates": candidates,
            },
            "few_shot": boundary_example,
            "output_schema": _candidate_output_schema(),
        }
    elif variant == "C":
        decision_table = (
            "Decision table: (1) factory_or_capacity when the core action concerns "
            "a factory, base, plant, production line, mass-production plan, capacity "
            "target, expansion, or construction; (2) technical_milestone when the "
            "core action is a product or technology release, approval, validation, "
            "breakthrough, or completed delivery without a capacity action; "
            "(3) when both appear in one passage, future or executing capacity action "
            "takes precedence unless two independently evidenced actions exist. "
        )
        system = COMMON_RULES + decision_table + R2_CAPACITY_RULE + adjudication
        payload = {
            "few_shot": {"positive": POSITIVE_EXAMPLE, "negative": NEGATIVE_EXAMPLE},
            "input": {
                "published_at": article.index.get("published_at", ""),
                "article_text": article.body,
                "strong_candidates": candidates,
            },
            "output_schema": _candidate_output_schema(),
        }
    else:
        raise ValueError(f"unknown Round 2 variant: {variant}")
    return system, json.dumps(payload, ensure_ascii=False), candidates


def _round3_variant(
    article: ExperimentArticle,
    variant: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    parent_system, parent_prompt, candidates = _round2_variant(article, "B")
    if variant == "A":
        delta = R3_TIME_RULE
    elif variant == "B":
        delta = R3_REFERENCE_RULE
    elif variant == "C":
        delta = R3_FIELD_RULE
    else:
        raise ValueError(f"unknown Round 3 variant: {variant}")
    return parent_system + delta, parent_prompt, candidates


def _article_sections(body: str, max_chars: int = 900) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    start = 0
    punctuation = set(".!?;\n\u3002\uff01\uff1f\uff1b")
    while start < len(body):
        target = min(len(body), start + max_chars)
        if target < len(body):
            lower = min(target, start + max_chars // 2)
            split = max(
                (
                    position + 1
                    for position in range(lower, target)
                    if body[position] in punctuation
                ),
                default=target,
            )
        else:
            split = target
        section_id = f"s{len(sections) + 1:04d}"
        sections.append(
            {
                "section_id": section_id,
                "char_start": start,
                "char_end": split,
                "text": body[start:split],
            }
        )
        start = split
    return sections


def _sectioned_candidates(
    article: ExperimentArticle,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in _candidates(article):
        start, end = _span(article.body, str(item["evidence_window"]))
        section_ids = [
            str(section["section_id"])
            for section in sections
            if start >= 0
            and max(start, int(section["char_start"]))
            < min(end, int(section["char_end"]))
        ]
        output.append(
            {
                **item,
                "section_ids": section_ids,
                "evidence_char_start": start,
                "evidence_char_end": end,
            }
        )
    return output


def _round4_single_variant(
    article: ExperimentArticle,
    variant: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    parent_system, parent_prompt, candidates = _round3_variant(article, "C")
    if variant == "A":
        return parent_system + R4_ACTION_RULE, parent_prompt, candidates
    if variant != "B":
        raise ValueError(f"unknown Round 4 single-call variant: {variant}")
    payload = json.loads(parent_prompt)
    sections = _article_sections(article.body)
    sectioned_candidates = _sectioned_candidates(article, sections)
    payload["input"] = {
        "published_at": article.index.get("published_at", ""),
        "article": {
            "source_length": len(article.body),
            "sections": sections,
        },
        "strong_candidates": sectioned_candidates,
    }
    return (
        parent_system + R4_ACTION_RULE + R4_SECTION_RULE,
        json.dumps(payload, ensure_ascii=False),
        sectioned_candidates,
    )


def _round4_locator(
    article: ExperimentArticle,
) -> tuple[str, str, list[dict[str, Any]]]:
    candidates = _candidates(article)
    system = COMMON_RULES + R2_CAPACITY_RULE + R4_ACTION_RULE + R4_LOCATOR_RULE
    payload = {
        "input": {
            "published_at": article.index.get("published_at", ""),
            "article_text": article.body,
            "strong_candidates": candidates,
        },
        "output_schema": {
            "accepted_events": [
                {
                    "locator_id": "l_1",
                    "company": "",
                    "event_type": "",
                    "event_status": "",
                    "primary_quote": "",
                    "covered_candidate_ids": [],
                }
            ],
            "rejections": [{"id": "", "reason_code": ""}],
            "ambiguities": [],
        },
    }
    return system, json.dumps(payload, ensure_ascii=False), candidates


def _locked_slots(
    article: ExperimentArticle,
    stage_1: dict[str, Any],
) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in stage_1.get("accepted_events") or []:
        if not isinstance(item, dict):
            continue
        company = str(item.get("company") or "")
        event_type = str(item.get("event_type") or "")
        event_status = str(item.get("event_status") or "")
        quote = str(item.get("primary_quote") or "")
        start = article.body.find(quote)
        if not company or not event_type or not event_status or start < 0:
            continue
        material = "\0".join((article.key, str(start), company, event_type))
        event_id = f"e_{sha1(material.encode('utf-8')).hexdigest()[:16]}"
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        slots.append(
            {
                "event_id": event_id,
                "company": company,
                "event_type": event_type,
                "event_status": event_status,
                "funding_round": "",
                "funding_amount": "",
                "cumulative_funding_amount": "",
                "investors": [],
                "evidence_quotes": [quote],
                "covered_candidate_ids": list(item.get("covered_candidate_ids") or []),
            }
        )
    return slots


def _round4_normalizer(
    article: ExperimentArticle,
    slots: list[dict[str, Any]],
) -> tuple[str, str]:
    system = (
        "You are a locked event-field normalizer. Return every event slot exactly "
        "once, in the same order. Never add, drop, merge, split, reject, reclassify, "
        "or change event_id, company, event_type, event_status, evidence_quotes[0], or "
        "covered_candidate_ids. Unknown optional strings are empty and unknown "
        "investors are an empty array. Every additional quote is a continuous verbatim "
        "article substring. Every non-empty factual field must appear verbatim in this "
        "event's quotes. cumulative_funding_amount requires explicit cumulative or "
        "total-to-date wording. Output one strict JSON object only. "
    )
    payload = {
        "article_text": article.body,
        "expected_event_ids": [slot["event_id"] for slot in slots],
        "event_slots": slots,
        "output_schema": {
            "events": [{**_candidate_output_schema()["events"][0], "event_id": ""}]
        },
    }
    return system, json.dumps(payload, ensure_ascii=False)


def _merge_locked_events(
    article: ExperimentArticle,
    slots: list[dict[str, Any]],
    stage_2: dict[str, Any],
) -> list[dict[str, Any]]:
    returned = {
        str(item.get("event_id") or ""): item
        for item in stage_2.get("events") or []
        if isinstance(item, dict)
    }
    output: list[dict[str, Any]] = []
    for slot in slots:
        item = returned.get(str(slot["event_id"])) or {}
        locked_match = all(
            item.get(field) == slot[field]
            for field in (
                "company",
                "event_type",
                "event_status",
                "covered_candidate_ids",
            )
        )
        raw_quotes = item.get("evidence_quotes") if locked_match else None
        quotes = [slot["evidence_quotes"][0]]
        if isinstance(raw_quotes, list) and raw_quotes[:1] == quotes:
            quotes.extend(
                str(quote)
                for quote in raw_quotes[1:]
                if isinstance(quote, str) and quote and quote in article.body
            )
        evidence_text = " ".join(quotes)

        def grounded_string(field: str) -> str:
            value = str(item.get(field) or "") if locked_match else ""
            return value if value and value in evidence_text else ""

        investors = []
        if locked_match and isinstance(item.get("investors"), list):
            investors = [
                str(value)
                for value in item["investors"]
                if str(value) and str(value) in evidence_text
            ]
        event = {key: value for key, value in slot.items() if key != "event_id"}
        event.update(
            {
                "funding_round": grounded_string("funding_round"),
                "funding_amount": grounded_string("funding_amount"),
                "cumulative_funding_amount": grounded_string(
                    "cumulative_funding_amount"
                ),
                "investors": investors,
                "evidence_quotes": quotes,
            }
        )
        output.append(event)
    return output


def _round5_title_payload(
    article: ExperimentArticle,
    parent_prompt: str,
) -> str:
    payload = json.loads(parent_prompt)
    original = payload["input"]
    payload["input"] = {
        "published_at": original.get("published_at", ""),
        "title": article.index.get("title", ""),
        "article_text": original.get("article_text", ""),
        "strong_candidates": original.get("strong_candidates", []),
    }
    return json.dumps(payload, ensure_ascii=False)


def _sentence_spans(body: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"[.!?;\n\u3002\uff01\uff1f\uff1b]", body):
        end = match.end()
        if body[start:end].strip():
            spans.append((start, end))
        start = end
    if body[start:].strip():
        spans.append((start, len(body)))
    return spans


def _lead(body: str, limit: int = 220) -> dict[str, Any]:
    end = min(len(body), limit)
    if end < len(body):
        punctuation = [
            position + 1
            for position in range(end)
            if body[position] in ".!?;\n\u3002\uff01\uff1f\uff1b"
        ]
        if punctuation:
            end = punctuation[-1]
    return {"body_start": 0, "body_end": end, "text": body[:end]}


def _round5_ledger(
    article: ExperimentArticle,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    title = str(article.index.get("title") or "").strip()
    highlights: list[dict[str, Any]] = []
    if title:
        highlights.append(
            {
                "id": f"h_title_{sha1(title.encode('utf-8')).hexdigest()[:10]}",
                "kind": "title_main",
                "body_span": None,
                "anchor": title,
                "type_hints": [],
            }
        )
    candidates = _candidates(article)
    for start, end in _sentence_spans(article.body):
        sentence = article.body[start:end]
        time_match = R5_TIME_RE.search(sentence)
        if time_match is None or R5_ACTION_RE.search(sentence) is None:
            continue
        overlapping = [
            item["hint_event_type"]
            for item in candidates
            if _overlaps((start, end), _span(article.body, item["evidence_window"]))
        ]
        material = f"timeline\0{start}\0{end}\0{time_match.group(0)}"
        highlights.append(
            {
                "id": f"h_time_{sha1(material.encode('utf-8')).hexdigest()[:10]}",
                "kind": "timeline",
                "body_span": [start, end],
                "time_marker": time_match.group(0),
                "type_hints": list(dict.fromkeys(overlapping)),
            }
        )
    compact: list[dict[str, Any]] = []
    for item in candidates:
        start, end = _span(article.body, str(item["evidence_window"]))
        anchor_start = max(0, start) if start >= 0 else 0
        compact.append(
            {
                "id": item["id"],
                "hint_event_type": item["hint_event_type"],
                "funding_round_hint": item.get("funding_round_hint", ""),
                "subject_hint": item.get("subject_hint", ""),
                "body_span": [start, end] if start >= 0 else None,
                "anchor": article.body[anchor_start : anchor_start + 48]
                if start >= 0
                else str(item["evidence_window"])[:48],
            }
        )
    adjudication = [*candidates, *[{"id": item["id"]} for item in highlights]]
    return highlights, compact, adjudication


def _round5_variant(
    article: ExperimentArticle,
    variant: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    parent_system, parent_prompt, candidates = _round4_single_variant(article, "A")
    if variant == "A":
        return (
            parent_system + R5_TITLE_RULE,
            _round5_title_payload(article, parent_prompt),
            candidates,
        )
    if variant == "B":
        return parent_system + R5_ATOMIC_RULE, parent_prompt, candidates
    if variant != "C":
        raise ValueError(f"unknown Round 5 variant: {variant}")
    parent_payload = json.loads(parent_prompt)
    highlights, compact, adjudication = _round5_ledger(article)
    payload = {
        "input": {
            "published_at": article.index.get("published_at", ""),
            "title": article.index.get("title", ""),
            "lead": _lead(article.body),
            "highlight_ledger": highlights,
            "compact_strong_candidates": compact,
            "article_text": article.body,
        },
        "few_shot": parent_payload.get("few_shot"),
        "output_schema": parent_payload.get("output_schema"),
    }
    return (
        parent_system + R5_TITLE_RULE + R5_ATOMIC_RULE + R5_LEDGER_RULE,
        json.dumps(payload, ensure_ascii=False),
        adjudication,
    )


def _variant_b(article: ExperimentArticle) -> tuple[str, str]:
    system = (
        COMMON_RULES
        + "输入 evidence_clusters 已把重复正则命中聚合。每个 cluster_id 必须恰好"
        "裁决一次：输出一个 event，或一个 rejection；不得新增、拆分或忽略证据簇。"
        "同一簇的多个 member_candidate_ids 不需要分别输出。"
    )
    payload = {
        "few_shot": {
            "input": {
                "article": "星河芯片完成A轮融资。",
                "evidence_clusters": [
                    {
                        "cluster_id": "ec_1",
                        "event_type_hint": "funding",
                        "evidence": [{"text": "星河芯片完成A轮融资。"}],
                    }
                ],
            },
            "output": {
                "events": [
                    {
                        "cluster_id": "ec_1",
                        "company": "星河芯片",
                        "event_type": "funding",
                        "event_status": "completed",
                        "funding_round": "A轮",
                        "funding_amount": "",
                        "cumulative_funding_amount": "",
                        "investors": [],
                        "evidence_quotes": ["星河芯片完成A轮融资。"],
                    }
                ],
                "rejections": [],
                "ambiguities": [],
            },
        },
        "input": {
            "source": {
                "published_at": article.index.get("published_at", ""),
            },
            "article": article.body,
            "evidence_clusters": _evidence_clusters(article),
        },
    }
    return system, json.dumps(payload, ensure_ascii=False)


def _variant_c_stage_1(article: ExperimentArticle) -> tuple[str, str]:
    system = (
        COMMON_RULES + "你只做事件定位和裁决，不提取金额、投资方、标签或摘要。"
        "每个 candidate 和 rule_seed id 必须恰好出现在 accepted_items.covers、"
        "rejections 或 ambiguities 中。accepted_items 的 primary_quote 必须是正文"
        "连续原文。"
    )
    payload = {
        "input": {
            "published_at": article.index.get("published_at", ""),
            "article": article.body,
            "candidate_ledger": _candidates(article),
            "rule_seed": _rule_seeds(article),
        },
        "output_schema": {
            "accepted_items": [
                {
                    "locator_id": "l_1",
                    "company": "",
                    "event_type": "",
                    "event_status": "",
                    "primary_quote": "",
                    "covers": {"candidate_ids": [], "rule_seed_ids": []},
                }
            ],
            "rejections": [{"id": "", "reason_code": ""}],
            "ambiguities": [],
        },
    }
    return system, json.dumps(payload, ensure_ascii=False)


def _context(body: str, quote: str) -> list[str]:
    position = body.find(quote)
    if position < 0:
        return [quote]
    return [body[max(0, position - 300) : position + len(quote) + 300]]


def _variant_c_stage_2(
    article: ExperimentArticle,
    stage_1: dict[str, Any],
) -> tuple[str, str]:
    accepted = []
    for position, item in enumerate(stage_1.get("accepted_items") or [], start=1):
        if not isinstance(item, dict):
            continue
        quote = str(item.get("primary_quote") or "")
        accepted.append(
            {
                "event_id": str(item.get("locator_id") or f"l_{position}"),
                "company": str(item.get("company") or ""),
                "event_type": str(item.get("event_type") or ""),
                "event_status": str(item.get("event_status") or ""),
                "primary_quote": quote,
                "evidence_context": _context(article.body, quote),
            }
        )
    system = (
        "你是事件字段规范器。只能为 accepted_events 中每个 event_id 输出恰好"
        "一个事件；不得新增、删除、合并、拆分、重新分类或拒绝事件。company、"
        "event_type、event_status、primary_quote 必须原样回显。所有非空事实字段"
        "必须逐字出现于 primary_quote 或 evidence_context；不确定则留空。"
        "evidence_quotes[0] 必须等于 primary_quote。只输出严格 JSON。"
    )
    payload = {
        "accepted_events": accepted,
        "output_schema": {
            "events": [
                {
                    "event_id": "",
                    "company": "",
                    "event_type": "",
                    "event_status": "",
                    "funding_round": "",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "evidence_quotes": [],
                    "field_notes": [],
                }
            ]
        },
    }
    return system, json.dumps(payload, ensure_ascii=False)


def _runner(env_file: Path, timeout: float) -> OpenClawConfiguredLLMRunner:
    env = load_env_file(env_file)
    endpoint = env["MINIMAX_REASONING_BASE_URL"].rstrip("/")
    suffix = "/chat/completions"
    base_url = endpoint[: -len(suffix)] if endpoint.endswith(suffix) else endpoint
    return OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M3",
            base_url=base_url,
            api_kind="openai-completions",
            api_key=env["MINIMAX_API_KEY"],
        ),
        timeout_seconds=timeout,
    )


def _call(
    runner: OpenClawConfiguredLLMRunner,
    system: str,
    prompt: str,
    session_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    raw = ""
    error = ""
    parsed: dict[str, Any] | None = None
    try:
        raw = runner.run(prompt, session_id=session_id, system_prompt=system)
        parsed = MiniMaxSemanticProcessor._parse_json(
            raw,
            allow_syntax_repair=True,
        )
    except Exception as exc:  # experiment records provider and parse failures
        error = f"{type(exc).__name__}: {exc}"
    return {
        "system_prompt": system,
        "user_prompt": prompt,
        "prompt_chars": len(system) + len(prompt),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "raw_response": raw,
        "parsed_response": parsed,
        "error": error,
    }


def _mechanical(
    variant: str,
    article: ExperimentArticle,
    response: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"json_object": False, "coverage_complete": False}
    events = response.get("events") or []
    rejections = response.get("rejections") or []
    result: dict[str, Any] = {
        "json_object": True,
        "event_count": len(events) if isinstance(events, list) else -1,
        "rejection_count": len(rejections) if isinstance(rejections, list) else -1,
    }
    if variant == "A":
        expected = {item["id"] for item in _candidates(article)}
        covered = {
            str(candidate_id)
            for event in events
            if isinstance(event, dict)
            for candidate_id in event.get("covered_candidate_ids") or []
        }
        rejected = {
            str(item.get("id") or "") for item in rejections if isinstance(item, dict)
        }
    elif variant == "B":
        expected = {item["cluster_id"] for item in _evidence_clusters(article)}
        covered = {
            str(event.get("cluster_id") or "")
            for event in events
            if isinstance(event, dict)
        }
        rejected = {
            str(item.get("cluster_id") or "")
            for item in rejections
            if isinstance(item, dict)
        }
    else:
        expected, covered, rejected = set(), set(), set()
    result.update(
        {
            "expected_ids": sorted(expected),
            "covered_ids": sorted(covered),
            "rejected_ids": sorted(rejected),
            "coverage_complete": expected == covered | rejected,
            "unknown_ids": sorted((covered | rejected) - expected),
            "missing_ids": sorted(expected - covered - rejected),
        }
    )
    return result


def _mechanical_candidates(
    response: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {"json_object": False, "coverage_complete": False}
    events = response.get("events") or []
    rejections = response.get("rejections") or []
    expected = {str(item["id"]) for item in candidates}
    covered = {
        str(candidate_id)
        for event in events
        if isinstance(event, dict)
        for candidate_id in event.get("covered_candidate_ids") or []
    }
    rejected = {
        str(item.get("id") or "") for item in rejections if isinstance(item, dict)
    }
    return {
        "json_object": True,
        "event_count": len(events) if isinstance(events, list) else -1,
        "rejection_count": len(rejections) if isinstance(rejections, list) else -1,
        "expected_ids": sorted(expected),
        "covered_ids": sorted(covered),
        "rejected_ids": sorted(rejected),
        "coverage_complete": expected == covered | rejected,
        "unknown_ids": sorted((covered | rejected) - expected),
        "missing_ids": sorted(expected - covered - rejected),
    }


def _run_variant(
    variant: str,
    article: ExperimentArticle,
    runner: OpenClawConfiguredLLMRunner,
    round_number: int,
) -> dict[str, Any]:
    session_base = f"minimax-input-loop:r{round_number}:{variant}:{article.key}"
    if round_number == 5:
        system, prompt, candidates = _round5_variant(article, variant)
        call = _call(runner, system, prompt, session_base)
        parsed = call["parsed_response"]
        return {
            "variant": variant,
            "article_key": article.key,
            "calls": [call],
            "normalized_output": parsed,
            "mechanical": _mechanical_candidates(parsed, candidates),
        }
    if round_number == 4 and variant in {"A", "B"}:
        system, prompt, candidates = _round4_single_variant(article, variant)
        call = _call(runner, system, prompt, session_base)
        parsed = call["parsed_response"]
        return {
            "variant": variant,
            "article_key": article.key,
            "calls": [call],
            "normalized_output": parsed,
            "mechanical": _mechanical_candidates(parsed, candidates),
        }
    if round_number == 4 and variant == "C":
        system_1, prompt_1, candidates = _round4_locator(article)
        call_1 = _call(runner, system_1, prompt_1, session_base + ":locate")
        stage_1 = call_1["parsed_response"] or {}
        slots = _locked_slots(article, stage_1)
        system_2, prompt_2 = _round4_normalizer(article, slots)
        call_2 = _call(runner, system_2, prompt_2, session_base + ":normalize")
        stage_2 = call_2["parsed_response"] or {}
        normalized = {
            "events": _merge_locked_events(article, slots, stage_2),
            "rejections": stage_1.get("rejections") or [],
            "ambiguities": stage_1.get("ambiguities") or [],
        }
        return {
            "variant": variant,
            "article_key": article.key,
            "calls": [call_1, call_2],
            "normalized_output": normalized,
            "mechanical": _mechanical_candidates(normalized, candidates),
        }
    if round_number in {2, 3}:
        if round_number == 2:
            system, prompt, candidates = _round2_variant(article, variant)
        else:
            system, prompt, candidates = _round3_variant(article, variant)
        call = _call(runner, system, prompt, session_base)
        parsed = call["parsed_response"]
        return {
            "variant": variant,
            "article_key": article.key,
            "calls": [call],
            "normalized_output": parsed,
            "mechanical": _mechanical_candidates(parsed, candidates),
        }
    if variant == "A":
        system, prompt = _variant_a(article)
        call = _call(runner, system, prompt, session_base)
        parsed = call["parsed_response"]
        return {
            "variant": variant,
            "article_key": article.key,
            "calls": [call],
            "normalized_output": parsed,
            "mechanical": _mechanical(variant, article, parsed),
        }
    if variant == "B":
        system, prompt = _variant_b(article)
        call = _call(runner, system, prompt, session_base)
        parsed = call["parsed_response"]
        return {
            "variant": variant,
            "article_key": article.key,
            "calls": [call],
            "normalized_output": parsed,
            "mechanical": _mechanical(variant, article, parsed),
        }
    if variant != "C":
        raise ValueError(f"unknown variant: {variant}")
    system_1, prompt_1 = _variant_c_stage_1(article)
    call_1 = _call(runner, system_1, prompt_1, session_base + ":locate")
    stage_1 = call_1["parsed_response"] or {}
    system_2, prompt_2 = _variant_c_stage_2(article, stage_1)
    call_2 = _call(runner, system_2, prompt_2, session_base + ":normalize")
    stage_2 = call_2["parsed_response"] or {}
    normalized = {
        "events": stage_2.get("events") or [],
        "rejections": stage_1.get("rejections") or [],
        "ambiguities": stage_1.get("ambiguities") or [],
        "accepted_items": stage_1.get("accepted_items") or [],
    }
    return {
        "variant": variant,
        "article_key": article.key,
        "calls": [call_1, call_2],
        "normalized_output": normalized,
        "mechanical": {
            "json_object": bool(call_1["parsed_response"])
            and bool(call_2["parsed_response"]),
            "stage_1_error": call_1["error"],
            "stage_2_error": call_2["error"],
            "event_count": len(normalized["events"]),
        },
    }


def command_materialize(args: argparse.Namespace) -> int:
    manifest = _read_json(args.manifest)
    all_keys = [
        pair for batch in manifest["train_rounds"].values() for pair in batch
    ] + manifest["holdout"]
    articles = _load_articles(args.database, all_keys)
    keys = [article.key for article in articles]
    if len(keys) != len(set(keys)):
        raise RuntimeError("train/holdout splits overlap")
    payload = {
        "manifest": manifest,
        "articles": [
            {
                "key": article.key,
                "source_id": article.source_id,
                "source_article_id": article.source_article_id,
                "article": article.article,
                "rule_events": list(article.rule_events),
                "candidate_count": len(_candidates(article)),
                "body_sha1": sha1(article.body.encode("utf-8")).hexdigest(),
            }
            for article in articles
        ],
    }
    _write_json(args.output / "dataset.json", payload)
    print(json.dumps({"article_count": len(articles)}, ensure_ascii=False))
    return 0


def command_run(args: argparse.Namespace) -> int:
    manifest = _read_json(args.manifest)
    keys = manifest["train_rounds"][str(args.round)]
    articles = _load_articles(args.database, keys)
    runner = _runner(args.env_file, args.timeout)
    round_dir = args.output / f"round-{args.round:02d}"
    for variant in args.variants:
        target = round_dir / f"variant-{variant.lower()}.json"
        results = []
        for position, article in enumerate(articles, start=1):
            result = _run_variant(variant, article, runner, args.round)
            result["production_projection"] = _production_projection(
                article,
                result.get("normalized_output"),
            )
            results.append(result)
            _write_json(
                target,
                {
                    "round": args.round,
                    "variant": variant,
                    "model": "minimax/MiniMax-M3",
                    "results": results,
                },
            )
            print(
                json.dumps(
                    {
                        "round": args.round,
                        "variant": variant,
                        "article": article.key,
                        "position": position,
                        "calls": len(result["calls"]),
                        "errors": [
                            call["error"] for call in result["calls"] if call["error"]
                        ],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 0


def command_reproject(args: argparse.Namespace) -> int:
    manifest = _read_json(args.manifest)
    keys = manifest["train_rounds"][str(args.round)]
    articles = {article.key: article for article in _load_articles(args.database, keys)}
    round_dir = args.output / f"round-{args.round:02d}"
    for variant in args.variants:
        target = round_dir / f"variant-{variant.lower()}.json"
        payload = _read_json(target)
        projected = 0
        failed = 0
        for result in payload.get("results") or []:
            article = articles[str(result["article_key"])]
            projection = _production_projection(
                article,
                result.get("normalized_output"),
            )
            result["production_projection"] = projection
            if projection["error"]:
                failed += 1
            else:
                projected += 1
        _write_json(target, payload)
        print(
            json.dumps(
                {
                    "round": args.round,
                    "variant": variant,
                    "projected": projected,
                    "failed": failed,
                },
                ensure_ascii=False,
            )
        )
    return 0


def command_blind(args: argparse.Namespace) -> int:
    manifest = _read_json(args.manifest)
    keys = manifest["train_rounds"][str(args.round)]
    articles = {article.key: article for article in _load_articles(args.database, keys)}
    variants = ["A", "B", "C"]
    labels = ["X", "Y", "Z"]
    random.Random(20260801 + args.round).shuffle(labels)
    mapping = dict(zip(variants, labels, strict=True))
    round_dir = args.output / f"round-{args.round:02d}"
    outputs = {
        variant: _read_json(round_dir / f"variant-{variant.lower()}.json")
        for variant in variants
    }
    by_variant = {
        variant: {item["article_key"]: item for item in payload["results"]}
        for variant, payload in outputs.items()
    }
    bundle = {
        "round": args.round,
        "rubric": {
            "hard_failures": [
                "wrong company or event",
                "invented factual field",
                "missed strong current event",
                "wrong completed/started/target",
                "ungrounded evidence",
                "invalid JSON or incomplete required adjudication",
            ],
            "tie_breakers": [
                "fewer duplicate events",
                "smaller prompt",
                "lower latency",
                "fewer calls",
            ],
        },
        "articles": [],
    }
    for key in [f"{source}:{article_id}" for source, article_id in keys]:
        article = articles[key]
        variants_payload = {}
        for variant in variants:
            result = by_variant[variant][key]
            projection = result.get("production_projection")
            if not isinstance(projection, dict):
                projection = _production_projection(
                    article,
                    result.get("normalized_output"),
                )
            variants_payload[mapping[variant]] = {
                "output": _projected_output(
                    result.get("normalized_output"),
                    projection,
                ),
                "mechanical": _projection_mechanical(projection),
                "production_audit": projection.get("audit") or {},
                "raw_mechanical": result["mechanical"],
                "prompt_chars": sum(call["prompt_chars"] for call in result["calls"]),
                "elapsed_seconds": sum(
                    call["elapsed_seconds"] for call in result["calls"]
                ),
                "call_count": len(result["calls"]),
                "errors": [call["error"] for call in result["calls"] if call["error"]],
            }
        bundle["articles"].append(
            {
                "article_key": key,
                "title": article.index.get("title", ""),
                "published_at": article.index.get("published_at", ""),
                "article_text": article.body,
                "variants": variants_payload,
            }
        )
    _write_json(round_dir / "blinded-bundle.json", bundle)
    _write_json(round_dir / "blind-map.private.json", mapping)
    print(json.dumps({"round": args.round, "labels": sorted(labels)}))
    return 0


def _holdout_order(manifest: dict[str, Any]) -> list[list[str]]:
    keys = [list(pair) for pair in manifest["holdout"]]
    random.Random(int(manifest["holdout_random_seed"])).shuffle(keys)
    return keys


def command_holdout(args: argparse.Namespace) -> int:
    manifest = _read_json(args.manifest)
    order = _holdout_order(manifest)
    if args.position < 1 or args.position > len(order):
        raise ValueError(f"holdout position must be between 1 and {len(order)}")
    article = _load_articles(args.database, [order[args.position - 1]])[0]
    runner = _runner(args.env_file, args.timeout)
    system, prompt, candidates = _round3_variant(article, "C")
    call = _call(
        runner,
        system,
        prompt,
        f"minimax-input-loop:holdout:{args.position}:{article.key}",
    )
    parsed = call["parsed_response"]
    raw_mechanical = _mechanical_candidates(parsed, candidates)
    projection = _production_projection(article, parsed)
    mechanical = _projection_mechanical(projection)
    holdout_dir = args.output / "holdout"
    raw_path = holdout_dir / f"case-{args.position:02d}.raw.json"
    bundle_path = holdout_dir / f"case-{args.position:02d}.bundle.json"
    _write_json(
        raw_path,
        {
            "position": args.position,
            "article_key": article.key,
            "model": "minimax/MiniMax-M3",
            "winner_contract": "round-03-variant-c",
            "call": call,
            "raw_mechanical": raw_mechanical,
            "production_projection": projection,
            "mechanical": mechanical,
        },
    )
    _write_json(
        bundle_path,
        {
            "position": args.position,
            "article_key": article.key,
            "title": article.index.get("title", ""),
            "published_at": article.index.get("published_at", ""),
            "article_text": article.body,
            "candidate_output": _projected_output(parsed, projection),
            "mechanical": mechanical,
            "production_audit": projection.get("audit") or {},
            "prompt_chars": call["prompt_chars"],
            "elapsed_seconds": call["elapsed_seconds"],
            "errors": [call["error"]] if call["error"] else [],
            "rubric": {
                "hard_failures": [
                    "wrong company or event",
                    "invented factual field",
                    "missed strong current event",
                    "wrong completed/started/target",
                    "ungrounded evidence",
                    "invalid JSON or incomplete required adjudication",
                ]
            },
        },
    )
    _write_json(holdout_dir / "order.private.json", {"order": order})
    print(
        json.dumps(
            {
                "position": args.position,
                "article": article.key,
                "events": len(projection.get("events") or []),
                "coverage_complete": mechanical.get("coverage_complete"),
                "elapsed_seconds": call["elapsed_seconds"],
                "error": call["error"],
                "bundle": str(bundle_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    sub = parser.add_subparsers(dest="command", required=True)
    materialize = sub.add_parser("materialize")
    materialize.set_defaults(func=command_materialize)
    run = sub.add_parser("run")
    run.add_argument("--round", type=int, choices=range(1, 6), required=True)
    run.add_argument(
        "--variants", nargs="+", choices=("A", "B", "C"), default=["A", "B", "C"]
    )
    run.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    run.add_argument("--timeout", type=float, default=240.0)
    run.set_defaults(func=command_run)
    reproject = sub.add_parser("reproject")
    reproject.add_argument("--round", type=int, choices=range(1, 6), required=True)
    reproject.add_argument(
        "--variants", nargs="+", choices=("A", "B", "C"), default=["A", "B", "C"]
    )
    reproject.set_defaults(func=command_reproject)
    blind = sub.add_parser("blind")
    blind.add_argument("--round", type=int, choices=range(1, 6), required=True)
    blind.set_defaults(func=command_blind)
    holdout = sub.add_parser("holdout")
    holdout.add_argument("--position", type=int, required=True)
    holdout.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    holdout.add_argument("--timeout", type=float, default=240.0)
    holdout.set_defaults(func=command_holdout)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
