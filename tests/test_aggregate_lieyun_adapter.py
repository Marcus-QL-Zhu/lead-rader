from datetime import datetime, timedelta, timezone

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.lieyun import LieyunAdapter


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _item(article_id: str, title: str, summary: str, time_label: str) -> str:
    return f"""
    <div class="article-bar">
      <a class="cover" href="/archives/{article_id}">封面</a>
      <a class="lyw-article-title" href="/archives/{article_id}">{title}</a>
      <p class="article-digest">{summary}</p>
      <a class="author">王非</a>
      <span class="timestamp">{time_label}</span>
      <span class="article-tag"><a>融资汇</a><a>硬科技</a></span>
    </div>
    """


def _listing() -> bytes:
    items = [
        _item(
            "502050",
            "FPGA芯片研发商高云半导体完成数亿元Pre-IPO轮融资",
            "资金用于先进制程产品研发和产业化。",
            "9小时前",
        ),
        _item(
            "502047",
            "柔性触觉传感器研发商尧乐科技完成Pre-A+轮融资",
            "鼎和高达领投，产业资本联合跟投。",
            "10小时前",
        ),
        _item(
            "502045",
            "商汤医疗获超1亿美元B轮融资",
            "医疗世界模型公司继续扩大研发。",
            "13小时前",
        ),
        _item(
            "502038",
            "3个月完成3轮融资，瓦博科技完成天使轮融资",
            "祥峰投资、洪泰基金等参与。",
            "1天前",
        ),
        _item(
            "502046",
            "AI游戏陪玩，是个伪需求吗？",
            "本文讨论产品需求和商业模式，不是融资事件。",
            "1天前",
        ),
    ]
    return (
        "<html><body><div class='article-container'>"
        + "".join(items)
        + "</div></body></html>"
    ).encode()


def _detail(title: str, body: str | None = None) -> bytes:
    article_body = body or (
        f"来源：猎云网。近日，{title}交割落地，本轮资金将用于核心技术研发、"
        "先进产品迭代和规模化产能建设。公司团队表示将继续服务产业客户，"
        "并推进产品在真实场景中的商业化部署。"
    )
    return f"""
    <html><body>
      <div class="article-main">
        <h1 class="lyw-article-title-inner"><span class="time">9小时前</span>{title}</h1>
        <div class="main-text" id="main-text-id"><p>{article_body}</p></div>
        <ul class="article-tags"><li><a>高云半导体</a></li></ul>
        <div class="article-copyright">版权及联系方式不应进入正文</div>
        <div class="module-box">相关推荐不应进入正文</div>
      </div>
      <a class="author-name">王非</a>
    </body></html>
    """.encode()


def _context(tmp_path, *, now=NOW):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _: b"",
        now=now,
    )


def test_listing_enumerates_every_visible_item_without_keyword_prefilter(tmp_path):
    adapter = LieyunAdapter()
    channel = adapter.channels[0]
    articles = adapter.parse_listing(channel, _listing(), _context(tmp_path))

    assert len(articles) == 5
    assert articles[-1].title == "AI游戏陪玩，是个伪需求吗？"
    assert [item.listing_position for item in articles] == [1, 2, 3, 4, 5]
    assert articles[0].structured_data["company"] == "高云半导体"
    assert articles[0].canonical_url == "https://lieyunpro.com/archives/502050"


def test_listing_hash_ignores_relative_time_and_page_position_drift(tmp_path):
    adapter = LieyunAdapter()
    channel = adapter.channels[0]
    first = adapter._parse_listing_page(
        channel,
        _listing(),
        _context(tmp_path),
        page_url=channel.url,
        position_offset=0,
        first_page=True,
    )
    moved = adapter._parse_listing_page(
        channel,
        _listing(),
        _context(tmp_path, now=NOW + timedelta(days=1)),
        page_url="https://lieyunpro.com/archives/p2.html",
        position_offset=20,
        first_page=False,
    )

    assert first[0].published_at != moved[0].published_at
    assert first[0].listing_position != moved[0].listing_position
    assert first[0].content_hash == moved[0].content_hash


def test_detail_extracts_dedicated_body_and_excludes_page_chrome(tmp_path):
    adapter = LieyunAdapter()
    channel = adapter.channels[0]
    index = adapter.parse_listing(channel, _listing(), _context(tmp_path))[0]
    article = adapter.parse_detail(
        channel, index, _detail(index.title), _context(tmp_path)
    )

    assert "核心技术研发" in article.clean_body
    assert "版权及联系方式" not in article.clean_body
    assert "相关推荐" not in article.clean_body
    assert article.author == "王非"
    assert article.extraction_method == "exact"


def test_rule_events_extracts_funding_but_not_non_event_discussion(tmp_path):
    adapter = LieyunAdapter()
    channel = adapter.channels[0]
    indexes = adapter.parse_listing(channel, _listing(), _context(tmp_path))

    positive = adapter.parse_detail(
        channel, indexes[0], _detail(indexes[0].title), _context(tmp_path)
    )
    negative = adapter.parse_detail(
        channel,
        indexes[-1],
        _detail(
            indexes[-1].title,
            "这是一篇讨论AI游戏产品需求与用户留存的评论文章，"
            "文章比较不同产品形态、内容成本、用户体验和商业模式，并分析玩家是否愿意长期使用此类产品。全文只讨论产品需求、运营策略与留存表现，没有任何资本事件事实。",
        ),
        _context(tmp_path),
    )

    events = adapter.rule_events(channel, positive)
    assert len(events) == 1
    assert events[0].canonical_company == "高云半导体"
    assert events[0].funding_round.startswith("Pre-")
    assert adapter.rule_events(channel, negative) == []


def test_access_interstitial_and_detail_mismatch_fail_closed(tmp_path):
    adapter = LieyunAdapter()
    channel = adapter.channels[0]
    context = _context(tmp_path)
    with pytest.raises(ListingInvariantError, match="no bypass"):
        adapter.parse_listing(
            channel,
            b"<html><title>Just a moment</title><script src='/cdn-cgi/challenge'></script>",
            context,
        )
    index = adapter.parse_listing(channel, _listing(), context)[0]
    with pytest.raises(DetailFetchError, match="title mismatch"):
        adapter.parse_detail(
            channel,
            index,
            _detail("完全不同的文章标题"),
            context,
        )


def test_scrapling_relocates_listing_and_second_run_skips_details(tmp_path):
    adapter = LieyunAdapter()
    channel = adapter.channels[0]
    context = _context(tmp_path)
    first = adapter.parse_listing(channel, _listing(), context)
    drifted = _listing().replace(b"article-container", b"archive-feed")
    relocated = adapter.parse_listing(channel, drifted, context)
    assert len(first) == len(relocated) == 5
    assert {item.discovery_method for item in relocated} == {"adaptive"}

    routes = {channel.url: _listing()}
    routes.update(
        {item.canonical_url: _detail(item.title) for item in first}
    )
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return routes[url]

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "coordinator.sqlite3",
        registry=DedicatedAdapterRegistry((adapter,)),
        fetch=fetch,
        now=NOW,
    )
    first_run = coordinator.collect_source(channel.source_id, "硬科技")
    calls.clear()
    second_run = coordinator.collect_source(channel.source_id, "硬科技")

    assert first_run.run.listing_count == 5
    assert first_run.run.detail_success_count == 5
    assert second_run.run.incremental_count == 0
    assert calls == [channel.url]
