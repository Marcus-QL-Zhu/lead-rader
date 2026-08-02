from __future__ import annotations

from argparse import Namespace
import json

from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex
import scripts.run_semantic_v27_frozen_split as frozen
from tests.test_aggregate_claim_adjudication import AcceptingRunner


def _article(article_id: str) -> CleanArticle:
    return CleanArticle(
        index=SourceArticleIndex(
            source_id="source",
            source_article_id=article_id,
            channel="news",
            canonical_url=f"https://example.invalid/{article_id}",
            title="测试",
            published_at="2026-08-01T00:00:00+08:00",
            discovered_at="2026-08-01T01:00:00+08:00",
            cursor_value=article_id,
            listing_page="https://example.invalid",
            listing_position=1,
            content_hash=f"index-{article_id}",
            discovery_method="exact",
        ),
        clean_body="甲辰科技完成A轮融资。",
        content_hash=f"body-{article_id}",
    )


def _args(tmp_path) -> Namespace:
    bundle = tmp_path / "bundle.json"
    bundle.write_text(
        json.dumps(
            {
                "articles": [
                    {
                        "key": f"source:{article_id}",
                        "split": "reserve",
                        "article": _article(article_id).to_dict(),
                    }
                    for article_id in ("1", "2")
                ]
            }
        ),
        encoding="utf-8",
    )
    return Namespace(
        bundle=bundle,
        split="reserve",
        expected_count=2,
        dataset_version="semantic-v25-reserve-v1",
        purpose="reserve-v1-one-time-prevalidation",
        output=tmp_path / "prediction.json",
        env_file=tmp_path / "unused.env",
        openclaw_config=None,
        openclaw_models=None,
        timeout=10.0,
    )


def test_frozen_runner_processes_split_without_gold(monkeypatch, tmp_path) -> None:
    args = _args(tmp_path)
    runner = AcceptingRunner()
    monkeypatch.setattr(frozen, "_runner", lambda *args, **kwargs: runner)

    result = frozen.run(args)

    assert result["status"] == "complete"
    assert result["dataset_version"] == "semantic-v25-reserve-v1"
    assert result["summary"]["article_count"] == 2
    assert result["summary"]["failed_claim_count"] == 0
    assert runner.calls == 2


def test_complete_frozen_output_is_reused_without_model_call(
    monkeypatch, tmp_path
) -> None:
    args = _args(tmp_path)
    runner = AcceptingRunner()
    monkeypatch.setattr(frozen, "_runner", lambda *args, **kwargs: runner)
    first = frozen.run(args)

    class BoomRunner:
        def run(self, *args, **kwargs):
            raise AssertionError("completed frozen split must not rerun")

    monkeypatch.setattr(frozen, "_runner", lambda *args, **kwargs: BoomRunner())
    second = frozen.run(args)

    assert second == first
