from datetime import datetime, timezone

from ht_lead_radar.aggregate_adapters.base import AdapterContext
from ht_lead_radar.aggregate_adapters.models import SourceArticleIndex
from ht_lead_radar.aggregate_adapters.sites.stcn import StcnAdapter


def test_stcn_access_control_uses_auditable_public_api_summary_fallback(
    tmp_path,
):
    adapter = StcnAdapter()
    channel = adapter.channel_for("stcn-flash")
    index = SourceArticleIndex(
        source_id=channel.source_id,
        source_article_id="4048192",
        channel="people-finance-flash",
        canonical_url="https://www.stcn.com/article/detail/4048192.html",
        title="公告精选：多公司公告",
        published_at="2026-07-29T22:00:00+08:00",
        discovered_at="2026-07-30T00:00:00+00:00",
        cursor_value="1785333600|4048192",
        listing_page="https://ewap.stcn.com/api/transform",
        listing_position=1,
        content_hash="index-hash",
        discovery_method="xhr:stcn-mobile-transform:exact",
        summary="蓝宇股份：拟设立控股子公司并投建埃及纺织项目。",
        structured_data={
            "tags": ("先进制造",),
            "company": "",
        },
    )
    context = AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    article = adapter.parse_detail(
        channel,
        index,
        "<html>访问过于频繁</html>".encode(),
        context,
    )

    assert article.fetch_status == "listing_fallback"
    assert article.failure_reason == "detail_access_control"
    assert article.extraction_method == "listing-api-fallback"
    assert index.summary in article.clean_body
    assert article.evidence_locators["body"] == "listing:wap_content"
    assert {
        (event.canonical_company, event.event_type, event.event_status)
        for event in adapter.rule_events(channel, article)
    } == {
        ("蓝宇股份", "factory_or_capacity", "started"),
        ("蓝宇股份", "new_site_or_entity", "started"),
    }
