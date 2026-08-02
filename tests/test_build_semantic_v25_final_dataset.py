from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import sqlite3

from scripts.build_semantic_v25_final_dataset import DOC_TYPES, build


def _create_db(path: Path, *, populate: bool) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE aggregate_clean_articles (
            source_id TEXT,
            source_article_id TEXT,
            article_json TEXT
        );
        CREATE TABLE aggregate_semantic_events (
            source_id TEXT,
            source_article_id TEXT,
            event_json TEXT
        );
        """
    )
    if populate:
        position = 0
        actions = (
            "完成A轮融资",
            "任命新任首席执行官",
            "宣布新工厂投产",
            "获得重大订单",
            "签署战略合作协议",
            "发布新产品",
            "获得临床试验许可",
            "完成收购",
        )
        for document_type in DOC_TYPES:
            for within_type in range(12):
                company = f"company-{document_type}-{within_type}"
                article_id = str(position)
                source = f"source-{position % 12}"
                body = (
                    f"{company}{actions[within_type % len(actions)]}。"
                    + f"Auditable evidence specific to {company}. " * 6
                )
                payload = {
                    "index": {
                        "source_id": source,
                        "source_article_id": article_id,
                        "channel": "test",
                        "canonical_url": f"https://example.test/{article_id}",
                        "title": f"{company} update",
                        "published_at": "2026-08-01",
                        "discovered_at": "2026-08-01T12:00:00+08:00",
                        "cursor_value": article_id,
                        "listing_page": "https://example.test",
                        "listing_position": within_type,
                        "content_hash": f"index-{article_id}",
                        "discovery_method": "fixture",
                    },
                    "clean_body": body,
                    "structured_data": {"document_type": document_type},
                    "fetch_status": "ok",
                    "content_hash": f"body-{article_id}",
                }
                connection.execute(
                    "INSERT INTO aggregate_clean_articles VALUES (?, ?, ?)",
                    (source, article_id, json.dumps(payload)),
                )
                connection.execute(
                    "INSERT INTO aggregate_semantic_events VALUES (?, ?, ?)",
                    (
                        source,
                        article_id,
                        json.dumps({"canonical_company": company}),
                    ),
                )
                position += 1
    connection.commit()
    connection.close()


def test_final_builder_freezes_exact_40_plus_20_company_disjoint_cohort(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh.sqlite"
    excluded = tmp_path / "excluded.sqlite"
    output = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.json"
    _create_db(fresh, populate=True)
    _create_db(excluded, populate=False)

    manifest = build(
        Namespace(
            fresh_db=fresh,
            exclude_db=[excluded],
            output=output,
            bundle=bundle,
            seed=7,
        )
    )

    assert manifest["status"] == "frozen_unlabelled"
    assert manifest["selected"]["formal"] == 40
    assert manifest["selected"]["reserve"] == 20
    assert set(manifest["selected"]["formal_by_document_type"].values()) == {8}
    assert set(manifest["selected"]["reserve_by_document_type"].values()) == {4}
    assert manifest["selected"]["missing_event_groups"] == []
    formal_companies = {
        company
        for case in manifest["cases"]
        if case["split"] == "formal"
        for company in case["company_keys"]
    }
    reserve_companies = {
        company
        for case in manifest["cases"]
        if case["split"] == "reserve"
        for company in case["company_keys"]
    }
    assert formal_companies.isdisjoint(reserve_companies)
    assert bundle.exists()


def test_final_builder_rejects_near_duplicate_leakage_across_splits(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh.sqlite"
    excluded = tmp_path / "excluded.sqlite"
    output = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.json"
    _create_db(fresh, populate=True)
    _create_db(excluded, populate=False)

    connection = sqlite3.connect(fresh)
    rows = connection.execute(
        "SELECT rowid, article_json FROM aggregate_clean_articles "
        "WHERE source_article_id IN ('0', '1') ORDER BY source_article_id"
    ).fetchall()
    first = json.loads(rows[0][1])
    duplicate = json.loads(rows[1][1])
    duplicate["clean_body"] = first["clean_body"]
    duplicate["content_hash"] = first["content_hash"]
    connection.execute(
        "UPDATE aggregate_clean_articles SET article_json = ? WHERE rowid = ?",
        (json.dumps(duplicate), rows[1][0]),
    )
    connection.commit()
    connection.close()

    manifest = build(
        Namespace(
            fresh_db=fresh,
            exclude_db=[excluded],
            output=output,
            bundle=bundle,
            seed=7,
        )
    )

    assert manifest["status"] == "insufficient_fresh_unseen"
    assert manifest["selected"]["formal"] + manifest["selected"]["reserve"] < 60


def test_final_builder_rejects_same_substantive_title_across_splits(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh.sqlite"
    excluded = tmp_path / "excluded.sqlite"
    output = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.json"
    _create_db(fresh, populate=True)
    _create_db(excluded, populate=False)

    connection = sqlite3.connect(fresh)
    rows = connection.execute(
        "SELECT rowid, article_json FROM aggregate_clean_articles "
        "WHERE source_article_id IN ('0', '1') ORDER BY source_article_id"
    ).fetchall()
    first = json.loads(rows[0][1])
    duplicate_title = json.loads(rows[1][1])
    duplicate_title["index"]["title"] = first["index"]["title"]
    connection.execute(
        "UPDATE aggregate_clean_articles SET article_json = ? WHERE rowid = ?",
        (json.dumps(duplicate_title), rows[1][0]),
    )
    connection.commit()
    connection.close()

    manifest = build(
        Namespace(
            fresh_db=fresh,
            exclude_db=[excluded],
            output=output,
            bundle=bundle,
            seed=7,
        )
    )

    assert manifest["status"] == "insufficient_fresh_unseen"
    assert manifest["selected"]["formal"] + manifest["selected"]["reserve"] < 60


def test_final_builder_excludes_frozen_bundle_and_sets_dataset_version(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh.sqlite"
    excluded = tmp_path / "excluded.sqlite"
    output = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.json"
    prior_bundle = tmp_path / "prior-bundle.json"
    _create_db(fresh, populate=True)
    _create_db(excluded, populate=False)

    connection = sqlite3.connect(fresh)
    raw = connection.execute(
        "SELECT article_json FROM aggregate_clean_articles "
        "WHERE source_article_id = '0'"
    ).fetchone()[0]
    connection.close()
    article = json.loads(raw)
    prior_bundle.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_version": "semantic-v25-final-v1",
                "articles": [
                    {
                        "key": "source-0:0",
                        "split": "formal",
                        "article": article,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = build(
        Namespace(
            fresh_db=fresh,
            exclude_db=[excluded],
            exclude_bundle=[prior_bundle],
            dataset_version="semantic-v25-final-v2",
            output=output,
            bundle=bundle,
            seed=7,
        )
    )

    assert manifest["dataset_version"] == "semantic-v25-final-v2"
    assert manifest["availability"]["rejected"]["exact_prior_overlap"] >= 1
    assert str(prior_bundle) in manifest["selection_policy"]["excluded_frozen_bundles"]


def test_final_builder_accepts_multiple_fresh_databases(tmp_path: Path) -> None:
    fresh_a = tmp_path / "fresh-a.sqlite"
    fresh_b = tmp_path / "fresh-b.sqlite"
    excluded = tmp_path / "excluded.sqlite"
    output = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.json"
    _create_db(fresh_a, populate=True)
    _create_db(fresh_b, populate=False)
    _create_db(excluded, populate=False)

    connection = sqlite3.connect(fresh_a)
    article = connection.execute(
        "SELECT source_id, source_article_id, article_json "
        "FROM aggregate_clean_articles WHERE source_article_id = '0'"
    ).fetchone()
    connection.close()
    payload = json.loads(article[2])
    payload["index"]["discovered_at"] = "2026-08-02T00:00:00+08:00"
    connection = sqlite3.connect(fresh_b)
    connection.execute(
        "INSERT INTO aggregate_clean_articles VALUES (?, ?, ?)",
        (article[0], article[1], json.dumps(payload)),
    )
    connection.commit()
    connection.close()

    manifest = build(
        Namespace(
            fresh_db=[fresh_a, fresh_b],
            exclude_db=[excluded],
            output=output,
            bundle=bundle,
            seed=7,
        )
    )

    assert manifest["availability"]["eligible_cases"] == 60


def test_final_builder_supports_development_cohort_without_reserve(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "fresh.sqlite"
    excluded = tmp_path / "excluded.sqlite"
    output = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.json"
    _create_db(fresh, populate=True)
    _create_db(excluded, populate=False)

    manifest = build(
        Namespace(
            fresh_db=[fresh],
            exclude_db=[excluded],
            dataset_version="semantic-v27-development-v2",
            formal_per_type=6,
            reserve_per_type=0,
            output=output,
            bundle=bundle,
            seed=7,
        )
    )

    assert manifest["status"] == "frozen_unlabelled"
    assert manifest["selected"]["formal"] == 30
    assert manifest["selected"]["reserve"] == 0
    assert bundle.exists()


def test_final_builder_prioritizes_rare_required_event_groups(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh.sqlite"
    excluded = tmp_path / "excluded.sqlite"
    output = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.json"
    _create_db(fresh, populate=True)
    _create_db(excluded, populate=False)

    # The fixture exposes executive_change only once.  A formal-only selection
    # must deliberately retain that rare required group instead of depending on
    # a lucky random restart.
    connection = sqlite3.connect(fresh)
    rows = connection.execute(
        "SELECT rowid, article_json FROM aggregate_clean_articles"
    ).fetchall()
    for rowid, raw in rows:
        payload = json.loads(raw)
        body = payload["clean_body"]
        if "任命新任首席执行官" in body and payload["index"]["source_article_id"] != "1":
            payload["clean_body"] = body.replace("任命新任首席执行官", "完成A轮融资")
            connection.execute(
                "UPDATE aggregate_clean_articles SET article_json = ? WHERE rowid = ?",
                (json.dumps(payload), rowid),
            )
    connection.commit()
    connection.close()

    manifest = build(
        Namespace(
            fresh_db=[fresh],
            exclude_db=[excluded],
            dataset_version="semantic-v27-final-v2",
            formal_per_type=4,
            reserve_per_type=0,
            output=output,
            bundle=bundle,
            seed=7,
        )
    )

    assert manifest["status"] == "frozen_unlabelled"
    assert manifest["selected"]["missing_event_groups"] == []
    assert any(
        "executive_change" in case["candidate_event_types"]
        for case in manifest["cases"]
        if case["split"] == "formal"
    )
