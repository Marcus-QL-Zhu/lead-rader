from datetime import datetime, timezone

from ht_lead_radar.aggregate_adapters.base import AdapterContext
from ht_lead_radar.aggregate_adapters.sites.lieyun import LieyunAdapter


def _item(article_id: int, published: str) -> str:
    return f"""
    <div class="article-bar">
      <a class="lyw-article-title" href="/archives/{article_id}">
        Company {article_id} funding update
      </a>
      <p class="article-digest">Funding and technology update.</p>
      <a class="author">Reporter</a>
      <span class="timestamp">{published}</span>
    </div>
    """


def _page(start: int, count: int, published: str) -> bytes:
    items = "".join(
        _item(article_id, published)
        for article_id in range(start, start + count)
    )
    return (
        f"<html><body><div class='article-container'>{items}</div></body></html>"
    ).encode()


def test_archive_pagination_closes_overlap_after_following_page(tmp_path):
    adapter = LieyunAdapter()
    channel = adapter.channels[0]
    page_one = _page(1000, 20, "2026-07-29")
    page_two = _page(1020, 5, "2026-07-28")
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        assert url == "https://lieyunpro.com/archives/p2.html"
        return page_two

    context = AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=fetch,
        now=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )

    articles = adapter.parse_listing(channel, page_one, context)

    assert len(articles) == 25
    assert calls == ["https://lieyunpro.com/archives/p2.html"]
    assert [item.listing_position for item in articles] == list(range(1, 26))
    assert articles[20].listing_page.endswith("/p2.html")
    assert {item.source_article_id for item in articles} == {
        str(article_id) for article_id in range(1000, 1025)
    }


def test_relative_archive_time_uses_site_timezone(tmp_path):
    adapter = LieyunAdapter()
    context = AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda url: b"",
        now=datetime(2026, 7, 28, 21, 30, tzinfo=timezone.utc),
    )

    published = adapter._parse_listing_time("1小时前", context)

    assert published == "2026-07-29"
