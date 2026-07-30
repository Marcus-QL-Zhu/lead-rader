from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from ht_lead_radar.aggregate_adapters.sites.cls import ClsAdapter

ADAPTER = ClsAdapter()
CHANNEL = ADAPTER.channel_for("cls-telegraph")
CASES = json.loads((Path(__file__).parent / "fixtures/cls_acceptance_fixtures.json").read_text(encoding="utf8"))

@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_cls_closed_day_acceptance_fixtures(case):
    article_id = case["id"]
    index = SourceArticleIndex(source_id=CHANNEL.source_id, source_article_id=article_id, channel=CHANNEL.name, canonical_url=f"https://www.cls.cn/detail/{article_id}", title=case["title"], published_at="2026-07-29T12:00:00+08:00", discovered_at=datetime.now(timezone.utc).isoformat(), cursor_value=f"0|{article_id}", listing_page=CHANNEL.url, listing_position=1, content_hash=article_id, discovery_method="fixture", summary=case["body"], structured_data={})
    article = CleanArticle(index=index, clean_body=case["body"], content_hash=article_id)
    assert ADAPTER.should_fetch_detail(CHANNEL, index) is True
    events = MiniMaxSemanticProcessor._normalize_rule_events(article, ADAPTER.rule_events(CHANNEL, article))
    actual = {(event.canonical_company, event.event_type, event.event_status, event.funding_round, event.funding_amount) for event in events}
    assert actual == {tuple(item) for item in case["expected"]}
