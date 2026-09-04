from datetime import datetime, timezone
import re

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.zhidx import ZhidxAdapter


NOW = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)
POSITIVE_TITLE = "星河机器人发布首款工业机器人并实现规模化量产"
NEGATIVE_TITLE = "机器人融资环境研究报告：为什么项目会失败"


def _listing() -> bytes:
    rows = [
        ("7001", POSITIVE_TITLE, "2026/07/29 15:39"),
        ("7002", NEGATIVE_TITLE, "2026/07/28 10:13"),
        ("7003", "融资标签历史文章3", "2026/07/27 19:50"),
        ("7004", "融资标签历史文章4", "2026/07/27 19:18"),
        ("7005", "融资标签历史文章5", "2026/07/27 16:46"),
        ("7006", "融资标签历史文章6", "2026/07/27 13:10"),
        ("7007", "融资标签历史文章7", "2026/07/24 21:25"),
        ("7008", "融资标签历史文章8", "2026/07/24 21:16"),
        ("7009", "融资标签历史文章9", "2026/07/23 17:18"),
        ("7010", "融资标签历史文章10", "2026/07/23 13:31"),
    ]
    items = "".join(
        f"""
        <li>
          <div class="tag-info-left-title">
            <a href="/p/{article_id}.html" title="{title}">{title}</a>
          </div>
          <div class="tag-info-list-related">
            <div class="iril-related-time">{published_at}</div>
          </div>
        </li>
        """
        for article_id, title, published_at in rows
    )
    return (
        f'<html><body><ul class="info-list">{items}</ul></body></html>'
    ).encode()


def _detail(
    title: str,
    published_at: str,
    *,
    event: bool = False,
) -> bytes:
    if event:
        body = (
            "星河机器人发布首款工业机器人并实现规模化量产。"
            "该公司已与海岳集团签署战略合作协议，双方将联合研发"
            "面向先进制造客户的运动控制和机器视觉方案。"
            "产品已经在多家客户的真实产线完成验证，团队将继续"
            "完善安全能力和规模化交付体系。"
        )
    else:
        body = (
            "本文讨论机器人融资环境、产业周期、技术路线和商业模式，"
            "并比较不同类型项目可能面临的成本、效率与人才挑战。"
            "文章只是行业研究和评论，只总结公开观点与趋势，并未披露"
            "任何一家具体公司的新动作、执行结果或其他新增产业事实。"
        )
    return f"""
    <html><body>
      <aside class="recommend">相关推荐：北辰公司发布首款机器人</aside>
      <div id="info-left">
        <div class="post-title">{title}</div>
        <div class="post-related"><span class="time">{published_at}</span></div>
        <div class="post-content">
          <p><strong>智东西 作者 | 测试作者</strong></p>
          <p>{body}</p>
          <p>▲无关图片说明（图源：推荐页面）</p>
        </div>
        <div class="post-tag"><a>融资</a><a>机器人</a></div>
      </div>
      <div class="author-info">
        <div class="author-name"><a>测试作者</a></div>
      </div>
      <footer>网站导航和版权信息</footer>
    </body></html>
    """.encode()


def _context(tmp_path):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
    )


def test_listing_enumerates_complete_closed_window_without_keyword_prefilter(
    tmp_path,
):
    adapter = ZhidxAdapter()
    indexes = adapter.parse_listing(
        adapter.channels[0],
        _listing(),
        _context(tmp_path),
    )

    assert [item.source_article_id for item in indexes] == ["7001", "7002"]
    assert [item.listing_position for item in indexes] == [1, 2]
    assert indexes[0].published_at == "2026-07-29T15:39:00+08:00"
    assert indexes[1].title == NEGATIVE_TITLE
    assert {item.discovery_method for item in indexes} == {"exact"}


def test_detail_is_dedicated_clean_body_with_title_and_date_invariants(tmp_path):
    adapter = ZhidxAdapter()
    context = _context(tmp_path)
    index = adapter.parse_listing(
        adapter.channels[0], _listing(), context
    )[0]
    article = adapter.parse_detail(
        adapter.channels[0],
        index,
        _detail(index.title, "2026/07/29", event=True),
        context,
    )

    assert "真实产线完成验证" in article.clean_body
    assert "相关推荐" not in article.clean_body
    assert "无关图片说明" not in article.clean_body
    assert "网站导航" not in article.clean_body
    assert article.author == "测试作者"
    assert article.tags == ("融资", "机器人")
    assert article.structured_data["company"] == "星河机器人"
    assert article.extraction_method == "exact"


def test_shared_industry_rules_cover_positive_negative_and_multiple_events(
    tmp_path,
):
    adapter = ZhidxAdapter()
    context = _context(tmp_path)
    indexes = adapter.parse_listing(adapter.channels[0], _listing(), context)
    positive = adapter.parse_detail(
        adapter.channels[0],
        indexes[0],
        _detail(indexes[0].title, "2026/07/29", event=True),
        context,
    )
    negative = adapter.parse_detail(
        adapter.channels[0],
        indexes[1],
        _detail(indexes[1].title, "2026/07/28"),
        context,
    )

    events = adapter.rule_events(adapter.channels[0], positive)
    assert {event.event_type for event in events} == {
        "partnership",
        "technical_milestone",
    }
    assert {event.canonical_company for event in events} == {"星河机器人"}
    assert all(event.evidence_quotes for event in events)
    assert all(event.processor == "rules:zhidx-v1" for event in events)
    assert adapter.rule_events(adapter.channels[0], negative) == []


def test_exact_selector_drift_adapts_but_invalid_count_fails_closed(tmp_path):
    adapter = ZhidxAdapter()
    context = _context(tmp_path)
    adapter.parse_listing(adapter.channels[0], _listing(), context)
    drifted = _listing().replace(b'class="info-list"', b'class="latest-list"')

    relocated = adapter.parse_listing(adapter.channels[0], drifted, context)
    assert len(relocated) == 2
    assert {item.discovery_method for item in relocated} == {"adaptive"}

    missing_item = _listing().replace(
        b'<li>\n          <div class="tag-info-left-title">',
        b'<div>\n          <div class="tag-info-left-title">',
        1,
    ).replace(b"</li>", b"</div>", 1)
    with pytest.raises(ListingInvariantError, match="failed closed"):
        adapter.parse_listing(
            adapter.channels[0],
            missing_item,
            _context(tmp_path / "fresh"),
        )


def test_access_interstitial_and_detail_mismatch_fail_closed(tmp_path):
    adapter = ZhidxAdapter()
    context = _context(tmp_path)
    interstitial = (
        b"<html><title>Just a moment</title>"
        b"<script src='/cdn-cgi/challenge-platform/x.js'></script></html>"
    )
    with pytest.raises(ListingInvariantError, match="no bypass"):
        adapter.parse_listing(adapter.channels[0], interstitial, context)

    index = adapter.parse_listing(
        adapter.channels[0], _listing(), context
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
            _detail("完全不同的详情页标题", "2026/07/29", event=True),
            context,
        )
    with pytest.raises(DetailFetchError, match="date mismatch"):
        adapter.parse_detail(
            adapter.channels[0],
            index,
            _detail(index.title, "2026/07/28", event=True),
            context,
        )


def test_second_coordinator_run_does_not_refetch_unchanged_details(tmp_path):
    adapter = ZhidxAdapter()
    channel = adapter.channels[0]
    listing = _listing()
    context = _context(tmp_path)
    indexes = adapter.parse_listing(channel, listing, context)
    network = {
        channel.url: listing,
        indexes[0].canonical_url: _detail(
            indexes[0].title,
            "2026/07/29",
            event=True,
        ),
        indexes[1].canonical_url: _detail(
            indexes[1].title,
            "2026/07/28",
        ),
    }
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
    new_title = "北辰机器人发布新一代控制器"
    new_item = f"""
        <li>
          <div class="tag-info-left-title">
            <a href="/p/7999.html" title="{new_title}">{new_title}</a>
          </div>
          <div class="tag-info-list-related">
            <div class="iril-related-time">2026/07/29 16:00</div>
          </div>
        </li>
    """.encode()
    drifted = listing.replace(
        b'<ul class="info-list">',
        b'<ul class="info-list">' + new_item,
    )
    drifted = re.sub(
        rb"<li>\s*<div class=\"tag-info-left-title\">\s*"
        rb"<a href=\"/p/7010\.html\".*?</li>",
        b"",
        drifted,
        count=1,
        flags=re.DOTALL,
    )
    network[channel.url] = drifted
    new_url = "https://zhidx.com/p/7999.html"
    network[new_url] = _detail(new_title, "2026/07/29")
    calls.clear()
    second = coordinator.collect_source(channel.source_id, "硬科技")

    assert first.run.listing_count == 2
    assert first.run.detail_success_count == 2
    assert second.run.incremental_count == 1
    assert calls == [channel.url, new_url]
