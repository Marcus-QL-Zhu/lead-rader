from __future__ import annotations

from scripts.audit_semantic_v27_entity_precision import audit


def _bundle(body: str) -> dict:
    return {
        "articles": [
            {
                "key": "source:1",
                "article": {
                    "index": {
                        "source_id": "source",
                        "source_article_id": "1",
                        "channel": "news",
                        "canonical_url": "https://example.invalid/1",
                        "title": "",
                        "published_at": "2026-08-01T00:00:00+08:00",
                        "discovered_at": "2026-08-01T01:00:00+08:00",
                        "cursor_value": "1",
                        "listing_page": "https://example.invalid",
                        "listing_position": 1,
                        "content_hash": "index",
                        "discovery_method": "exact",
                    },
                    "clean_body": body,
                },
            }
        ]
    }


def test_entity_precision_gate_requires_reviewed_row_count() -> None:
    result = audit(
        _bundle("\u7532\u8fb0\u79d1\u6280\u5b8c\u6210\u878d\u8d44\u3002"),
        {
            "dataset_version": "test",
            "reviewer": "test",
            "expected_candidate_count": 1,
            "default_label": "tp",
        },
    )

    assert result["status"] == "PASS"
    assert result["precision"] == 1.0


def test_entity_precision_gate_fails_when_expected_count_drifts() -> None:
    result = audit(
        _bundle("\u7532\u8fb0\u79d1\u6280\u5b8c\u6210\u878d\u8d44\u3002"),
        {
            "dataset_version": "test",
            "reviewer": "test",
            "expected_candidate_count": 2,
            "default_label": "tp",
        },
    )

    assert result["status"] == "FAIL"
