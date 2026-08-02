from __future__ import annotations

from scripts.audit_semantic_v27_ledgers import audit
from scripts.audit_semantic_v27_ledgers import (
    _claim_supports_evidence,
    _span_supports_evidence,
)


def test_audit_evidence_alignment_ignores_punctuation_and_whitespace() -> None:
    assert _span_supports_evidence(
        "近日，甲辰科技已完成 A+ 轮融资。",
        "甲辰科技融资\n近日，甲辰科技已完成A+轮融资。",
    )
    assert _claim_supports_evidence(
        "近日，甲辰科技已完成A+轮融资。本轮由乙资本领投。",
        "完成A+轮融资",
        "甲辰科技融资\n近日，甲辰科技已完成A+轮融资。",
    )


def test_audit_does_not_count_one_claim_for_two_gold_subjects() -> None:
    joint_span = "双方联合发布机器人产品。"
    body = "甲辰科技发布平台。乙巳智能推出系统。" + joint_span
    article = {
        "index": {
            "source_id": "source",
            "source_article_id": "1",
            "channel": "news",
            "canonical_url": "https://example.invalid/1",
            "title": "合作",
            "published_at": "2026-08-01T00:00:00+08:00",
            "discovered_at": "2026-08-01T01:00:00+08:00",
            "cursor_value": "1",
            "listing_page": "https://example.invalid",
            "listing_position": 1,
            "content_hash": "index-hash",
            "discovery_method": "exact",
        },
        "clean_body": body,
        "content_hash": "body-hash",
    }
    events = [
        {
            "canonical_company": company,
            "event_type": "technical_milestone",
            "evidence_span": {"text": joint_span},
        }
        for company in ("甲辰科技", "乙巳智能")
    ]
    bundle = {"articles": [{"key": "source:1", "article": article}]}
    gold = {
        "dataset_version": "development",
        "cases": [
            {
                "key": "source:1",
                "candidates": [],
                "annotation": {"gold_events": events},
            }
        ],
    }

    result = audit(bundle, gold)

    assert result["entity_recall"] == 1.0
    assert result["action_claim_recall"] == 0.5
    assert len(result["failures"]) == 1
