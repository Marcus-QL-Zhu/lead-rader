from datetime import datetime, timezone

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.jazzyear import JazzyearAdapter


NOW = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)
POSITIVE_TITLE = "星河机器人发布首款工业机器人并实现规模化量产"
NEGATIVE_TITLE = "机器人融资环境研究报告：为什么项目会失败"


def _homepage() -> bytes:
    links = "".join(
        f'<a class="article-card-cover" href="./article_info.html?id={item}">'
        f"<span>文章{item}</span></a>"
        for item in range(9001, 9006)
    )
    return (
        f'<html><body><div class="article-card-cover-box">{links}</div></body></html>'
    ).encode()


def _card(
    article_id: str,
    title: str,
    published_at: str,
    *,
    tags: str = "人工智能 · 商业化",
) -> str:
    return f"""
    <a href="./article_info.html?id={article_id}" class="article-card-cover ani-frame">
      <div class="cover"><div class="tag">原创</div></div>
      <div class="center">
        <div class="title font-18">{title}</div>
        <div class="tags font-14">{tags}</div>
        <div class="bottom font-12">
          <div class="author-box"><span class="author">作者：甲子光年</span></div>
          <span class="time">{published_at}</span>
        </div>
      </div>
    </a>
    """


def _listing(cards: list[tuple[str, str, str]]) -> bytes:
    return (
        '<html><body><div class="page-article-list">'
        + "".join(_card(*card) for card in cards)
        + "</div></body></html>"
    ).encode()


def _routes(adapter: JazzyearAdapter) -> dict[str, bytes]:
    routes: dict[str, bytes] = {
        adapter._listing_url(1, 1): _listing(
            [("1001", "甲小姐对话旧文章", "2025-08-06")]
        ),
        adapter._listing_url(2, 1): _listing(
            [
                (str(2000 + item), f"洞见窗口文章{item}", "2026-07-29")
                for item in range(1, 10)
            ]
        ),
        adapter._listing_url(2, 2): _listing(
            [
                ("2010", "洞见窗口文章10", "2026-07-29"),
                ("2011", "洞见窗口文章11", "2026-07-29"),
                ("2012", "洞见窗口以前的边界文章", "2026-07-27"),
            ]
        ),
        adapter._listing_url(3, 1): _listing(
            [
                ("3001", POSITIVE_TITLE, "2026-07-29"),
                ("3002", NEGATIVE_TITLE, "2026-07-29"),
                ("3003", "破局者旧文章", "2026-07-20"),
            ]
        ),
        adapter._listing_url(4, 1): _listing(
            [("4001", "7x24h旧文章", "2026-07-22")]
        ),
        adapter._listing_url(5, 1): _listing(
            [("5001", "甲子视频旧文章", "2026-07-27")]
        ),
    }
    return routes


def _detail(title: str, *, event: bool = False) -> bytes:
    if event:
        body = (
            "星河机器人发布首款工业机器人并实现规模化量产。"
            "该公司介绍，产品已经在先进制造客户的真实产线完成验证，"
            "当前正在扩大交付团队，并持续完善运动控制、机器视觉和安全能力。"
        )
    else:
        body = (
            "本文讨论人工智能产业的发展路径、产品体验、客户需求和商业模式，"
            "并比较多种技术路线的成本、效率以及长期影响。"
            "全文没有宣布公司融资、产品发布、订单交付、合作签约或其他新增企业事件。"
        )
    return f"""
    <html><body>
      <aside class="recommend">
        <a>推荐文章中的某公司发布首款机器人</a>
        <a>推荐报告</a>
      </aside>
      <div class="article-header">
        <div class="title">{title}</div>
        <div class="author-header">
          <span class="author name">作者：甲子光年</span>
          <span class="time">2026-07-29</span>
        </div>
      </div>
      <div class="article-detail">
        <div class="article-message" data-role="article-body"><p>{body}</p></div>
        <ul class="article-data-box"><li>9999阅读</li></ul>
      </div>
      <footer>网站导航和版权信息</footer>
    </body></html>
    """.encode()


def _context(tmp_path, routes: dict[str, bytes]):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda url: routes[url],
        now=NOW,
    )


def test_closed_window_enumerates_overflow_page_without_keyword_prefilter(tmp_path):
    adapter = JazzyearAdapter()
    routes = _routes(adapter)
    indexes = adapter.parse_listing(
        adapter.channels[0],
        _homepage(),
        _context(tmp_path, routes),
    )

    assert len(indexes) == 13
    assert {item.source_article_id for item in indexes} == {
        *(str(item) for item in range(2001, 2012)),
        "3001",
        "3002",
    }
    assert [item.listing_position for item in indexes] == list(range(1, 14))
    assert indexes[9].listing_page == adapter._listing_url(2, 2)
    assert indexes[9].structured_data["page_position"] == 1
    assert any(item.title == NEGATIVE_TITLE for item in indexes)


def test_detail_is_dedicated_body_and_excludes_navigation_recommendations(tmp_path):
    adapter = JazzyearAdapter()
    routes = _routes(adapter)
    context = _context(tmp_path, routes)
    index = next(
        item
        for item in adapter.parse_listing(adapter.channels[0], _homepage(), context)
        if item.source_article_id == "3001"
    )
    article = adapter.parse_detail(
        adapter.channels[0],
        index,
        _detail(index.title, event=True),
        context,
    )

    assert "真实产线完成验证" in article.clean_body
    assert "推荐文章" not in article.clean_body
    assert "推荐报告" not in article.clean_body
    assert "网站导航" not in article.clean_body
    assert "9999阅读" not in article.clean_body
    assert article.author == "甲子光年"
    assert article.extraction_method == "exact"


def test_shared_industry_rules_emit_positive_but_not_commentary_negative(tmp_path):
    adapter = JazzyearAdapter()
    routes = _routes(adapter)
    context = _context(tmp_path, routes)
    indexes = adapter.parse_listing(adapter.channels[0], _homepage(), context)
    positive_index = next(
        item for item in indexes if item.source_article_id == "3001"
    )
    negative_index = next(
        item for item in indexes if item.source_article_id == "3002"
    )
    positive = adapter.parse_detail(
        adapter.channels[0],
        positive_index,
        _detail(positive_index.title, event=True),
        context,
    )
    negative = adapter.parse_detail(
        adapter.channels[0],
        negative_index,
        _detail(negative_index.title),
        context,
    )

    events = adapter.rule_events(adapter.channels[0], positive)
    assert len(events) == 1
    assert events[0].canonical_company == "星河机器人"
    assert events[0].event_type == "technical_milestone"
    assert events[0].evidence_quotes
    assert adapter.rule_events(adapter.channels[0], negative) == []


def test_exact_selector_drift_uses_adaptive_relocation(tmp_path):
    adapter = JazzyearAdapter()
    routes = _routes(adapter)
    context = _context(tmp_path, routes)
    adapter.parse_listing(adapter.channels[0], _homepage(), context)

    drifted = dict(routes)
    drifted[adapter._listing_url(2, 1)] = routes[
        adapter._listing_url(2, 1)
    ].replace(b"page-article-list", b"latest-article-list")
    relocated = adapter.parse_listing(
        adapter.channels[0],
        _homepage(),
        _context(tmp_path, drifted),
    )

    type_two = [
        item
        for item in relocated
        if item.structured_data["article_type"] == 2
        and item.structured_data["page"] == 1
    ]
    assert len(type_two) == 9
    assert {item.discovery_method for item in type_two} == {"adaptive"}


def test_second_coordinator_run_does_not_refetch_unchanged_details(tmp_path):
    adapter = JazzyearAdapter()
    channel = adapter.channels[0]
    routes = _routes(adapter)
    context = _context(tmp_path, routes)
    indexes = adapter.parse_listing(channel, _homepage(), context)
    network = {channel.url: _homepage(), **routes}
    network.update(
        {
            item.canonical_url: _detail(
                item.title,
                event=item.source_article_id == "3001",
            )
            for item in indexes
        }
    )
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return network[url]

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "coordinator.sqlite3",
        registry=DedicatedAdapterRegistry((adapter,)),
        fetch=fetch,
        now=NOW,
    )
    first = coordinator.collect_source(channel.source_id, "硬科技")
    calls.clear()
    second = coordinator.collect_source(channel.source_id, "硬科技")

    assert first.run.listing_count == 13
    assert first.run.detail_success_count == 13
    assert second.run.incremental_count == 0
    assert not any("article_info.html" in url for url in calls)
    assert calls == [
        channel.url,
        adapter._listing_url(1, 1),
        adapter._listing_url(2, 1),
        adapter._listing_url(2, 2),
        adapter._listing_url(3, 1),
        adapter._listing_url(4, 1),
        adapter._listing_url(5, 1),
    ]


def test_access_interstitial_and_business_mismatch_fail_closed(tmp_path):
    adapter = JazzyearAdapter()
    routes = _routes(adapter)
    context = _context(tmp_path, routes)
    interstitial = (
        b"<html><title>Just a moment</title>"
        b"<script src='/cdn-cgi/challenge-platform/x.js'></script></html>"
    )
    with pytest.raises(ListingInvariantError, match="no bypass"):
        adapter.parse_listing(adapter.channels[0], interstitial, context)

    index = adapter.parse_listing(
        adapter.channels[0], _homepage(), context
    )[0]
    with pytest.raises(DetailFetchError, match="no bypass"):
        adapter.parse_detail(
            adapter.channels[0],
            index,
            interstitial,
            context,
        )
    with pytest.raises(DetailFetchError, match="title mismatch"):
        adapter.parse_detail(
            adapter.channels[0],
            index,
            _detail("完全不同的详情页标题"),
            context,
        )
