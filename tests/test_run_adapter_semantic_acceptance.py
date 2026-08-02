from __future__ import annotations

import json

import pytest

from scripts.run_adapter_semantic_acceptance import load_archive
from scripts.run_semantic_v27_development import _summary


def _payload(content_hash: str) -> dict:
    return {
        "article": {
            "index": {
                "source_id": "nbd-vcpe-weekly",
                "source_article_id": "1",
                "channel": "vcpe-weekly",
                "canonical_url": "https://example.invalid/1",
                "title": "Title",
                "published_at": "2026-08-01T00:00:00+08:00",
                "discovered_at": "2026-08-01T01:00:00+08:00",
                "cursor_value": "1",
                "listing_page": "https://example.invalid",
                "listing_position": 1,
                "content_hash": "index-hash",
                "discovery_method": "exact",
            },
            "clean_body": "Body",
            "content_hash": content_hash,
        },
        "events": [],
        "minimax_audit": {"model_identity": "rules-only"},
    }


def test_load_archive_deduplicates_identical_semantic_snapshots(tmp_path) -> None:
    source_dir = tmp_path / "nbd-vcpe-weekly"
    source_dir.mkdir()
    for name in ("semantic-1.json", "semantic-1-hash.json"):
        (source_dir / name).write_text(
            json.dumps(_payload("body-hash")),
            encoding="utf-8",
        )

    records = load_archive(tmp_path, ["nbd-vcpe-weekly"])

    assert list(records) == ["nbd-vcpe-weekly:1"]
    assert len(records["nbd-vcpe-weekly:1"]["archive_files"]) == 2


def test_load_archive_rejects_duplicate_content_mismatch(tmp_path) -> None:
    source_dir = tmp_path / "nbd-vcpe-weekly"
    source_dir.mkdir()
    (source_dir / "semantic-1.json").write_text(
        json.dumps(_payload("first")),
        encoding="utf-8",
    )
    (source_dir / "semantic-1-other.json").write_text(
        json.dumps(_payload("second")),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate content mismatch"):
        load_archive(tmp_path, ["nbd-vcpe-weekly"])


def test_v27_adapter_acceptance_summary_exposes_failed_claims() -> None:
    summary = _summary(
        [
            {
                "events": [],
                "audit": {
                    "candidate_count": 2,
                    "accepted_claim_ids": [],
                    "rejected_claim_ids": ["ac_1"],
                    "failed_claim_ids": ["ac_2"],
                    "strict_claim_contract_ready": False,
                    "status": "partial",
                },
            }
        ]
    )

    assert summary["claim_count"] == 2
    assert summary["failed_claim_count"] == 1
    assert summary["strict_ready_article_count"] == 0
