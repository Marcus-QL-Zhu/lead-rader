from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from scripts.prepare_semantic_v25_gold_packets import build_packets


def test_builds_two_blinded_hash_locked_packets(tmp_path: Path) -> None:
    body = "Company Alpha completed Series A funding and plans a new factory."
    article = {
        "index": {
            "source_id": "source-a",
            "source_article_id": "1",
            "channel": "test",
            "canonical_url": "https://example.test/1",
            "title": "Company Alpha raises funding",
            "published_at": "2026-08-01",
            "discovered_at": "2026-08-01T09:00:00+08:00",
            "cursor_value": "1",
            "listing_page": "https://example.test",
            "listing_position": 1,
            "content_hash": "index-hash",
            "discovery_method": "fixture",
        },
        "clean_body": body,
        "fetch_status": "ok",
        "content_hash": "body-hash",
    }
    bundle = {
        "schema_version": 1,
        "dataset_version": "semantic-v25-final-v1",
        "articles": [{"key": "source-a:1", "split": "formal", "article": article}],
    }
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "dataset_version": "semantic-v25-final-v1",
        "status": "frozen_unlabelled",
        "bundle_sha256": sha256(bundle_path.read_bytes()).hexdigest(),
        "cases": [{"key": "source-a:1", "split": "formal"}],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    result = build_packets(
        manifest_path,
        bundle_path,
        tmp_path / "packets",
        seed=7,
    )

    assert result["case_count"] == 1
    first = json.loads((tmp_path / "packets" / "annotator-a.json").read_text())
    second = json.loads((tmp_path / "packets" / "annotator-b.json").read_text())
    assert first["annotator_id"] != second["annotator_id"]
    assert first["cases"][0]["article_sha256"] == sha256(
        body.encode("utf-8")
    ).hexdigest()
    assert first["cases"][0]["annotation"]["annotation_status"] == "unlabelled"
