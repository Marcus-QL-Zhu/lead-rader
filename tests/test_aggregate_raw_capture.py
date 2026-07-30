from hashlib import sha256

from ht_lead_radar.aggregate_adapters.base import AggregateAdapter
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry


CHANNEL = SourceChannel(
    source_id="raw-capture-test",
    name="raw capture",
    url="https://example.com/list",
    source_grade="B",
    event_prior=("technical_milestone",),
    allowed_hosts=("example.com",),
    allowed_path_patterns=(r"/detail/\d+",),
)
PAGE_TWO = "https://example.com/page/2"


class PagedAdapter(AggregateAdapter):
    adapter_id = "raw-capture"
    channels = (CHANNEL,)

    def parse_listing(self, channel, html, context):
        del channel, html
        page_two = context.fetch(PAGE_TWO)
        assert page_two == b'{"page":2}'
        return [
            SourceArticleIndex(
                source_id=CHANNEL.source_id,
                source_article_id="1",
                channel="news",
                canonical_url="https://example.com/detail/1",
                title="complete test article",
                published_at="2026-07-29",
                discovered_at="2026-07-30T00:00:00+00:00",
                cursor_value="1",
                listing_page=PAGE_TWO,
                listing_position=1,
                content_hash="index-1",
                discovery_method="fixture",
            )
        ]

    def parse_detail(self, channel, index, html, context):
        del channel, html, context
        return CleanArticle(
            index=index,
            clean_body="complete public article body",
            content_hash="body-1",
        )

    def rule_events(self, channel, article) -> list[SemanticEvent]:
        del channel, article
        return []


def test_internal_pagination_raw_payload_is_captured(tmp_path):
    routes = {
        CHANNEL.url: b"<html>landing</html>",
        PAGE_TWO: b'{"page":2}',
        "https://example.com/detail/1": b"<html>detail</html>",
    }
    acceptance = tmp_path / "acceptance"
    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "state.sqlite3",
        registry=DedicatedAdapterRegistry((PagedAdapter(),)),
        fetch=lambda url: routes[url],
        acceptance_dir=acceptance,
    )

    coordinator.collect_source(CHANNEL.source_id, "hardtech")

    target = acceptance / CHANNEL.source_id
    digest = sha256(PAGE_TWO.encode("utf-8")).hexdigest()[:16]
    assert (target / "listing.html").read_bytes() == routes[CHANNEL.url]
    assert (target / f"adapter-fetch-{digest}.html").read_bytes() == routes[PAGE_TWO]
    assert (target / "detail-1.html").read_bytes() == routes[
        "https://example.com/detail/1"
    ]
