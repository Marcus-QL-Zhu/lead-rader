from dataclasses import replace
from datetime import datetime, timezone
import json

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.sites.jiqizhixin import JiqizhixinAdapter


NOW = datetime(2026, 8, 1, 21, 0, tzinfo=timezone.utc)
ADAPTER = JiqizhixinAdapter()
CHANNEL = ADAPTER.channel_for("jiqizhixin-industry-analysis")


def _context(tmp_path, *, fetch=None, full_visible_window=False):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=fetch or (lambda url: b""),
        now=NOW,
        decision_state={
            "capture_full_visible_window": {"enabled": full_visible_window}
        },
    )


def _listing(*, drifted=False, duplicate=False, close_window=True):
    wrapper = "industry-stream" if drifted else "u-block__body js-u-item is-active"
    item_class = "article-item__container"
    rows = []
    times = [
        "2026-08-02 04:30:00 +0800",
        "2026-08-02 03:20:00 +0800",
        "2026-08-02 01:10:00 +0800",
        "2026-08-01 22:00:00 +0800",
        "2026-08-01 19:00:00 +0800",
        "2026-08-01 16:00:00 +0800",
        "2026-08-01 13:00:00 +0800",
        "2026-08-01 10:00:00 +0800",
        "2026-08-01 06:00:00 +0800",
        *(
            [
                "2026-08-01 04:00:00 +0800",
                "2026-07-31 22:00:00 +0800",
                "2026-07-31 12:00:00 +0800",
            ]
            if close_window
            else [
                "2026-08-01 05:50:00 +0800",
                "2026-08-01 05:40:00 +0800",
                "2026-08-01 05:30:00 +0800",
            ]
        ),
    ]
    for number, published in enumerate(times, start=1):
        slug_number = 1 if duplicate and number == 2 else number
        slug_day = published[:10]
        rows.append(
            f"""
            <article id="industry-{number}" class="{item_class}"
                     data-role="industry-card">
              <main class="article-item__right">
                <section>
                  <a class="article-item__title"
                     href="/articles/{slug_day}-{slug_number}">
                    产业技术深度第{number}篇
                  </a>
                  <p class="article-item__summary">第{number}篇摘要</p>
                </section>
                <footer>
                  <div class="article-item__author">
                    <a class="article-item__name">机器之心</a>
                    <time class="js-time-ago" datetime="{published}">时间</time>
                  </div>
                </footer>
              </main>
            </article>
            """
        )
    return (
        f"<html><body><div id='industry-feed' class='{wrapper}' "
        "data-role='industry-feed'>"
        + "".join(rows)
        + "</div></body></html>"
    ).encode()


def _detail(index, *, title=None, published=None, body=None, author=True):
    long_paragraph = (
        "具身智能产业正在从实验室验证走向规模化落地，机器人企业开始建设新的数据、"
        "仿真和交付能力，并围绕制造客户形成明确的产品路线。"
    )
    content = body or "".join(
        f"<p>{long_paragraph}{number}。这意味着行业组织能力正在发生变化。</p>"
        for number in range(1, 26)
    )
    content += (
        "<script>推荐文章：噪声公司宣布扩产</script>"
        "<aside><p>相关推荐噪声</p></aside>"
    )
    payload = {
        "title": title or index.title,
        "published_at": published or index.published_at[:19].replace("T", " "),
        "content": content,
        "seo": {"keywords": ["具身智能", "机器人", "产业观察"]},
    }
    if author:
        payload["author"] = {"id": "author-1", "name": "机器之心"}
    return json.dumps(payload, ensure_ascii=False).encode()


def _indexes(tmp_path):
    return ADAPTER.parse_listing(CHANNEL, _listing(), _context(tmp_path))


def test_jiqizhixin_listing_captures_closed_one_day_window(tmp_path):
    indexes = _indexes(tmp_path)

    assert len(indexes) == 9
    assert indexes[0].source_article_id == "2026-08-02-1"
    assert indexes[-1].source_article_id == "2026-08-01-9"
    assert [item.listing_position for item in indexes] == list(range(1, 10))
    assert all(item.discovery_method == "exact" for item in indexes)
    assert all(item.structured_data["author"] == "机器之心" for item in indexes)


def test_jiqizhixin_audit_mode_captures_full_finite_first_page(tmp_path):
    indexes = ADAPTER.parse_listing(
        CHANNEL,
        _listing(close_window=False),
        _context(tmp_path, full_visible_window=True),
    )

    assert len(indexes) == 12
    assert indexes[-1].source_article_id == "2026-08-01-12"


def test_jiqizhixin_listing_adapts_only_dom_location_then_revalidates(tmp_path):
    context = _context(tmp_path)
    ADAPTER.parse_listing(CHANNEL, _listing(), context)
    moved = ADAPTER.parse_listing(CHANNEL, _listing(drifted=True), context)

    assert moved
    assert all(item.discovery_method == "adaptive" for item in moved)

    with pytest.raises(ListingInvariantError, match="duplicate"):
        ADAPTER.parse_listing(CHANNEL, _listing(duplicate=True), context)
    with pytest.raises(ListingInvariantError, match="does not close"):
        ADAPTER.parse_listing(CHANNEL, _listing(close_window=False), context)


def test_jiqizhixin_fetches_public_detail_api_and_parses_scoped_body(tmp_path):
    index = _indexes(tmp_path)[0]
    calls = []

    def fetch(url):
        calls.append(url)
        return _detail(index)

    context = _context(tmp_path, fetch=fetch)
    payload = ADAPTER.fetch_detail(CHANNEL, index, context)
    article = ADAPTER.parse_detail(CHANNEL, index, payload, context)

    assert calls == [
        "https://www.jiqizhixin.com/api/article_library/articles/2026-08-02-1"
    ]
    assert article.extraction_method == "api-exact"
    assert article.author == "机器之心"
    assert article.tags == ("具身智能", "机器人", "产业观察")
    assert article.structured_data["paragraph_count"] == 25
    assert "推荐文章" not in article.clean_body
    assert "相关推荐噪声" not in article.clean_body
    assert route_document(article).document_type == "long_feature"


def test_jiqizhixin_detail_fails_closed_on_identity_and_structure(tmp_path):
    index = _indexes(tmp_path)[0]
    context = _context(tmp_path)

    with pytest.raises(DetailFetchError, match="title mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, title="完全不相关的文章标题"),
            context,
        )
    with pytest.raises(DetailFetchError, match="date mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, published="2026-08-01 04:30:00"),
            context,
        )
    with pytest.raises(DetailFetchError, match="author missing"):
        ADAPTER.parse_detail(CHANNEL, index, _detail(index, author=False), context)
    with pytest.raises(DetailFetchError, match="length/structure"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, body="<p>过短正文</p>"),
            context,
        )
    with pytest.raises(DetailFetchError, match="access control"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            "请完成人机验证".encode(),
            context,
        )


def test_jiqizhixin_rules_keep_explicit_operational_signal(tmp_path):
    index = _indexes(tmp_path)[0]
    context = _context(tmp_path)
    article = ADAPTER.parse_detail(CHANNEL, index, _detail(index), context)
    explicit = replace(
        article,
        clean_body="星河机器人宣布建设新生产基地并启动扩产。",
        content_hash="explicit",
    )

    events = ADAPTER.rule_events(CHANNEL, explicit)

    assert {
        (event.canonical_company, event.event_type) for event in events
    } == {("星河机器人", "factory_or_capacity")}
    assert events[0].processor == "rules:jiqizhixin-v1"
