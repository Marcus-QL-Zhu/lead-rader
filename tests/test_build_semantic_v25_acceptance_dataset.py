from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import sqlite3

from scripts import build_semantic_v25_acceptance_dataset as dataset


def _article(source: str, article_id: str, body: str, company: str = "") -> dict:
    return {
        "index": {
            "source_id": source,
            "source_article_id": article_id,
            "channel": "test",
            "canonical_url": f"https://example.com/{article_id}",
            "title": f"{company or article_id}动态",
            "published_at": "2026-07-01",
            "discovered_at": "2026-07-01T00:00:00+00:00",
            "cursor_value": article_id,
            "listing_page": "https://example.com",
            "listing_position": 1,
            "content_hash": f"index-{article_id}",
            "discovery_method": "fixture",
            "summary": "",
            "structured_data": {},
        },
        "clean_body": body,
        "fetch_status": "ok",
        "content_hash": f"body-{article_id}",
    }


def _database(path: Path) -> None:
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
    for position in range(70):
        source = f"source-{position % 8}"
        article_id = str(position)
        company = f"测试公司{position}"
        body = (
            f"{company}完成A轮融资，资金将用于产品研发和市场拓展。"
            "该公司同时介绍了团队背景、产品方向、客户范围和长期技术路线。"
            "报道还说明了行业环境、产品应用场景、核心能力和后续研究重点。"
            if position < 55
            else (
                f"{company}介绍了团队背景和既有业务能力，但没有宣布新事件。"
                "文章还回顾了行业发展、产品定位、客户需求和团队长期研究方向。"
                "其余内容讨论一般市场趋势、应用场景、技术特点和行业长期前景。"
            )
        )
        article = _article(source, article_id, body, company)
        connection.execute(
            "INSERT INTO aggregate_clean_articles VALUES (?, ?, ?)",
            (source, article_id, json.dumps(article, ensure_ascii=False)),
        )
        connection.execute(
            "INSERT INTO aggregate_semantic_events VALUES (?, ?, ?)",
            (
                source,
                article_id,
                json.dumps(
                    {"canonical_company": company},
                    ensure_ascii=False,
                ),
            ),
        )
    connection.commit()
    connection.close()


def test_builder_freezes_disjoint_calibration_and_test_cohorts(tmp_path: Path) -> None:
    database_path = tmp_path / "source.sqlite"
    experiment_path = tmp_path / "prior.json"
    output_path = tmp_path / "manifest.json"
    bundle_path = tmp_path / "bundle.json"
    _database(database_path)
    experiment_path.write_text(
        json.dumps(
            {
                "train_rounds": {"1": [["source-0", "0"]]},
                "holdout": [["source-1", "1"]],
            }
        ),
        encoding="utf-8",
    )

    manifest = dataset.build(
        Namespace(
            database=database_path,
            experiment_manifest=experiment_path,
            output=output_path,
            bundle=bundle_path,
            seed=7,
        )
    )

    assert manifest["status"] == "diagnostic_not_final"
    assert manifest["leakage_audit"]["may_support_final_acceptance"] is False
    assert manifest["summary"]["split_counts"] == {
        "calibration": 12,
        "test": 36,
    }
    assert manifest["summary"]["control_count"] == 12
    assert len({case["body_sha256"] for case in manifest["cases"]}) == 48
    calibration_companies = {
        company
        for case in manifest["cases"]
        if case["split"] == "calibration"
        for company in case["known_company_keys"]
    }
    test_companies = {
        company
        for case in manifest["cases"]
        if case["split"] == "test"
        for company in case["known_company_keys"]
    }
    assert calibration_companies.isdisjoint(test_companies)
    assert output_path.exists()
    assert bundle_path.exists()
