from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.materialize_adapter_semantic_archive import materialize
from scripts.run_adapter_semantic_acceptance import load_archive


def _index(source_id: str, article_id: str) -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_article_id": article_id,
        "channel": "test",
        "canonical_url": f"https://example.invalid/{article_id}",
        "title": f"Title {article_id}",
        "published_at": "2026-08-01T00:00:00+08:00",
        "discovered_at": "2026-08-01T01:00:00+08:00",
        "cursor_value": article_id,
        "listing_page": "https://example.invalid",
        "listing_position": 1,
        "content_hash": f"hash-{article_id}",
        "discovery_method": "fixture",
    }


def _article(source_id: str, article_id: str) -> dict[str, object]:
    return {
        "index": _index(source_id, article_id),
        "clean_body": f"Body {article_id}",
        "content_hash": f"hash-{article_id}",
    }


def _event(source_id: str, article_id: str, event_type: str) -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_article_id": article_id,
        "canonical_url": f"https://example.invalid/{article_id}",
        "company_mentions": ["Example Co"],
        "canonical_company": "Example Co",
        "event_type": event_type,
        "event_date": "2026-08-01",
    }


def test_materialize_nested_items_and_load_archive(tmp_path: Path) -> None:
    payload = {
        "items": [
            {
                "article": _article("fixture-source", "1"),
                "rule_events": [_event("fixture-source", "1", "funding")],
            },
            {"article": _article("fixture-source", "2"), "rule_events": []},
        ]
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    output_root = tmp_path / "archive"

    result = materialize(
        payload,
        source_id="fixture-source",
        articles_path="items",
        events_path="",
        indexes_path="",
        output_root=output_root,
        input_path=input_path,
    )

    assert result["article_count"] == 2
    records = load_archive(output_root, ["fixture-source"])
    assert sorted(records) == ["fixture-source:1", "fixture-source:2"]
    assert len(records["fixture-source:1"]["rule_events"]) == 1


def test_materialize_groups_by_article_events(tmp_path: Path) -> None:
    payload = {
        "articles": [_article("fixture-source", "1")],
        "rule_events_by_article": [
            {
                "source_article_id": "1",
                "events": [_event("fixture-source", "1", "partnership")],
            }
        ],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    materialize(
        payload,
        source_id="fixture-source",
        articles_path="articles",
        events_path="rule_events_by_article",
        indexes_path="",
        output_root=tmp_path / "archive",
        input_path=input_path,
    )
    records = load_archive(tmp_path / "archive", ["fixture-source"])
    assert records["fixture-source:1"]["rule_events"][0].event_type == "partnership"


def test_materialize_groups_nested_event_lists(tmp_path: Path) -> None:
    payload = {
        "articles": [_article("fixture-source", "1")],
        "rule_events_by_article": [[_event("fixture-source", "1", "funding")]],
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    materialize(
        payload,
        source_id="fixture-source",
        articles_path="articles",
        events_path="rule_events_by_article",
        indexes_path="",
        output_root=tmp_path / "archive",
        input_path=input_path,
    )
    records = load_archive(tmp_path / "archive", ["fixture-source"])
    assert records["fixture-source:1"]["rule_events"][0].event_type == "funding"


def test_materialize_can_filter_a_mixed_source_snapshot(tmp_path: Path) -> None:
    payload = {
        "articles": [
            _article("fixture-source", "1"),
            _article("other-source", "2"),
        ]
    }
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    result = materialize(
        payload,
        source_id="fixture-source",
        articles_path="articles",
        events_path="",
        indexes_path="",
        output_root=tmp_path / "archive",
        input_path=input_path,
        skip_other_sources=True,
    )
    assert result["article_count"] == 1
    assert result["skipped_source_count"] == 1


def test_materialize_accepts_events_from_a_separate_snapshot(tmp_path: Path) -> None:
    payload = {"articles": [_article("fixture-source", "1")]}
    events_payload = {"events": [_event("fixture-source", "1", "technical_milestone")]}
    input_path = tmp_path / "articles.json"
    events_path = tmp_path / "events.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    events_path.write_text(json.dumps(events_payload), encoding="utf-8")

    materialize(
        payload,
        source_id="fixture-source",
        articles_path="articles",
        events_path="events",
        indexes_path="",
        output_root=tmp_path / "archive",
        input_path=input_path,
        events_payload=events_payload,
        events_input_path=events_path,
    )
    records = load_archive(tmp_path / "archive", ["fixture-source"])
    assert records["fixture-source:1"]["rule_events"][0].event_type == "technical_milestone"


def test_materialize_rejects_source_mismatch(tmp_path: Path) -> None:
    payload = {"articles": [_article("other-source", "1")]}
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="source mismatch"):
        materialize(
            payload,
            source_id="fixture-source",
            articles_path="articles",
            events_path="",
            indexes_path="",
            output_root=tmp_path / "archive",
            input_path=input_path,
        )
