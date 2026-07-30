from datetime import datetime, timezone
import json
import sqlite3

import pytest

from ht_lead_radar.aggregate_adapters.adaptive import AdaptiveSelector
from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
)
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.kr36 import Kr36Adapter


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _item(
    article_id: str,
    title: str,
    company: str,
    *,
    kind: str = "newsflashes",
) -> str:
    return f"""
    <div class="css-xle9x">
      <div class="item-title">
        <span class="type">快讯</span>
        <a class="title" href="//36kr.com/{kind}/{article_id}">{title}</a>
      </div>
      <div class="item-desc"><span>{title}，资金用于研发和产能建设。</span></div>
      <div class="project-card-wrp">
        <div class="right">
          <div class="right-top">
            <div class="title">{company}</div>
            <div class="tag fin-tag">A轮</div>
          </div>
          <div class="right-bottom">硬科技研发商</div>
        </div>
      </div>
      <div class="item-other"><span class="time">2小时前</span></div>
    </div>
    """


def _listing() -> bytes:
    items = [
        _item("1001", "星河芯片完成1亿元A轮融资", "星河芯片"),
        _item("1002", "灵巧机器人完成数千万元A轮融资", "灵巧机器人"),
        _item("1003", "神经接口科技获数亿元新一轮融资", "神经接口科技"),
        _item("1004", "超导能源完成亿元级战略融资", "超导能源"),
        _item("1005", "个贷融资成本明示规定即将实施", "合规观察"),
    ]
    return f"<html><body>{''.join(items)}</body></html>".encode()


def _detail(title: str, company: str) -> bytes:
    return f"""
    <html>
      <head><meta name="description" content="{title}，本轮融资由远山资本领投，资金用于技术研发和产能建设。"></head>
      <body>
        <h1>{title}</h1>
        <div class="newsflash-item">{company}发布消息：{title}，本轮融资由远山资本领投，资金用于技术研发和产能建设。</div>
      </body>
    </html>
    """.encode()


def test_kr36_indexes_every_list_item_but_emits_only_real_events(tmp_path):
    listing = _listing()
    titles = {
        "1001": ("星河芯片完成1亿元A轮融资", "星河芯片"),
        "1002": ("灵巧机器人完成数千万元A轮融资", "灵巧机器人"),
        "1003": ("神经接口科技获数亿元新一轮融资", "神经接口科技"),
        "1004": ("超导能源完成亿元级战略融资", "超导能源"),
        "1005": ("个贷融资成本明示规定即将实施", "合规观察"),
    }
    routes = {"https://pitchhub.36kr.com/financing-flash": listing}
    routes.update(
        {
            f"https://www.36kr.com/newsflashes/{article_id}": _detail(title, company)
            for article_id, (title, company) in titles.items()
        }
    )
    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "state.sqlite3",
        registry=DedicatedAdapterRegistry((Kr36Adapter(),)),
        fetch=lambda url: routes[url],
        now=NOW,
    )

    result = coordinator.collect_source(
        "36kr-financing-flash",
        "半导体|具身智能|脑机接口|核聚变",
    )

    assert result.run.status == "ok"
    assert result.run.listing_count == 5
    assert result.run.incremental_count == 5
    assert result.run.detail_success_count == 5
    assert result.run.detail_failure_count == 0
    assert result.run.rule_event_count == 4
    assert {item.company for item in result.evidence} == {
        "星河芯片",
        "灵巧机器人",
        "神经接口科技",
        "超导能源",
    }
    assert {item.event_type for item in result.evidence} == {"funding"}

    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    assert connection.execute(
        "SELECT COUNT(*) FROM aggregate_article_index"
    ).fetchone()[0] == 5
    assert connection.execute(
        "SELECT COUNT(*) FROM aggregate_clean_articles"
    ).fetchone()[0] == 5
    connection.close()


def test_kr36_second_run_uses_persisted_events_without_refetching_details(tmp_path):
    listing = _listing()
    titles = {
        "1001": ("星河芯片完成1亿元A轮融资", "星河芯片"),
        "1002": ("灵巧机器人完成数千万元A轮融资", "灵巧机器人"),
        "1003": ("神经接口科技获数亿元新一轮融资", "神经接口科技"),
        "1004": ("超导能源完成亿元级战略融资", "超导能源"),
        "1005": ("个贷融资成本明示规定即将实施", "合规观察"),
    }
    routes = {"https://pitchhub.36kr.com/financing-flash": listing}
    routes.update(
        {
            f"https://www.36kr.com/newsflashes/{article_id}": _detail(title, company)
            for article_id, (title, company) in titles.items()
        }
    )
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return routes[url]

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "state.sqlite3",
        fetch=fetch,
        now=NOW,
    )
    first = coordinator.collect_source("36kr-financing-flash", "硬科技")
    calls.clear()
    second = coordinator.collect_source("36kr-financing-flash", "硬科技")

    assert len(first.evidence) == len(second.evidence) == 4
    assert second.run.incremental_count == 0
    assert calls == ["https://pitchhub.36kr.com/financing-flash"]


def test_scrapling_adaptive_selector_relocates_saved_element(tmp_path):
    original = """
    <html><body>
      <main>
        <div class="item-title" data-kind="article"><a class="title">融资一</a></div>
        <div class="item-title" data-kind="article"><a class="title">融资二</a></div>
      </main>
    </body></html>
    """
    changed = """
    <html><body>
      <main>
        <section class="feed">
          <div class="story-heading" data-kind="article"><a class="title">融资一</a></div>
          <div class="story-heading" data-kind="article"><a class="title">融资二</a></div>
        </section>
      </main>
    </body></html>
    """
    storage = tmp_path / "adaptive.sqlite3"
    seeded = AdaptiveSelector(
        original,
        url="https://example.com/feed",
        storage_path=storage,
        minimum_similarity=55,
    )
    first = seeded.css(
        "div.item-title",
        identifier="feed:title",
        minimum_count=2,
    )
    relocated = AdaptiveSelector(
        changed,
        url="https://example.com/feed",
        storage_path=storage,
        minimum_similarity=55,
    ).css(
        "div.item-title",
        identifier="feed:title",
        minimum_count=2,
    )

    assert first.method == "exact"
    assert relocated.method == "adaptive"
    assert [item.get_all_text(strip=True) for item in relocated.elements] == [
        "融资一",
        "融资二",
    ]


def test_kr36_captcha_with_structured_listing_is_complete_not_dead_letter(tmp_path):
    listing = _listing()
    routes = {"https://pitchhub.36kr.com/financing-flash": listing}
    for article_id in ("1001", "1002", "1003", "1004", "1005"):
        routes[f"https://www.36kr.com/newsflashes/{article_id}"] = b"<html>TTGCaptcha verify_center</html>"
        routes[f"https://36kr.com/newsflashes/{article_id}"] = b"<html>TTGCaptcha verify_center</html>"
    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "captcha.sqlite3",
        registry=DedicatedAdapterRegistry((Kr36Adapter(),)),
        fetch=lambda url: routes[url],
        now=NOW,
    )

    result = coordinator.collect_source("36kr-financing-flash", "hardtech")

    assert result.run.status == "ok"
    assert result.run.detail_success_count == 5
    assert result.run.detail_failure_count == 0
    connection = sqlite3.connect(tmp_path / "captcha.sqlite3")
    assert connection.execute(
        "SELECT COUNT(*) FROM aggregate_dead_letters WHERE resolved_at = ''"
    ).fetchone()[0] == 0
    statuses = {
        json.loads(row[0])["fetch_status"]
        for row in connection.execute(
            "SELECT article_json FROM aggregate_clean_articles"
        ).fetchall()
    }
    connection.close()
    assert statuses == {"structured_complete", "listing_complete"}


def test_kr36_prefers_www_detail_and_does_not_hit_bare_captcha_route(tmp_path):
    listing = _listing()
    calls: list[str] = []
    routes = {"https://pitchhub.36kr.com/financing-flash": listing}
    titles = {
        "1001": ("星河芯片完成1亿元A轮融资", "星河芯片"),
        "1002": ("灵巧机器人完成数千万元A轮融资", "灵巧机器人"),
        "1003": ("神经接口科技获数亿元新一轮融资", "神经接口科技"),
        "1004": ("超导能源完成亿元级战略融资", "超导能源"),
        "1005": ("个贷融资成本明示规定即将实施", "合规观察"),
    }
    routes.update(
        {
            f"https://www.36kr.com/newsflashes/{article_id}": _detail(title, company)
            for article_id, (title, company) in titles.items()
        }
    )

    def fetch(url: str) -> bytes:
        calls.append(url)
        return routes[url]

    result = DedicatedAggregateCoordinator(
        state_db=tmp_path / "www.sqlite3",
        registry=DedicatedAdapterRegistry((Kr36Adapter(),)),
        fetch=fetch,
        now=NOW,
    ).collect_source("36kr-financing-flash", "hardtech")

    assert result.run.detail_failure_count == 0
    assert not any(
        url.startswith("https://36kr.com/newsflashes/") for url in calls
    )


def test_kr36_falls_back_to_bare_domain_when_www_transport_fails(tmp_path):
    listing = _listing()
    routes = {"https://pitchhub.36kr.com/financing-flash": listing}
    titles = {
        "1001": ("星河芯片完成1亿元A轮融资", "星河芯片"),
        "1002": ("灵巧机器人完成数千万元A轮融资", "灵巧机器人"),
        "1003": ("神经接口科技获数亿元新一轮融资", "神经接口科技"),
        "1004": ("超导能源完成亿元级战略融资", "超导能源"),
        "1005": ("个贷融资成本明示规定即将实施", "合规观察"),
    }
    routes.update(
        {
            f"https://36kr.com/newsflashes/{article_id}": _detail(title, company)
            for article_id, (title, company) in titles.items()
        }
    )

    def fetch(url: str) -> bytes:
        if url.startswith("https://www.36kr.com/"):
            raise OSError("simulated www outage")
        return routes[url]

    result = DedicatedAggregateCoordinator(
        state_db=tmp_path / "www-error.sqlite3",
        registry=DedicatedAdapterRegistry((Kr36Adapter(),)),
        fetch=fetch,
        now=NOW,
    ).collect_source("36kr-financing-flash", "hardtech")

    assert result.run.status == "ok"
    assert result.run.detail_failure_count == 0


def test_kr36_records_both_route_failures_before_reraising(tmp_path):
    from ht_lead_radar.aggregate_adapters.base import AdapterContext

    adapter = Kr36Adapter()
    channel = adapter.channel_for("36kr-financing-flash")
    index = adapter.parse_listing(channel, _listing(), AdapterContext.create(
        state_db=tmp_path / "listing.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
    ))[0]
    decisions = []

    def fail(url: str) -> bytes:
        raise OSError(f"unavailable: {url}")

    context = AdapterContext.create(
        state_db=tmp_path / "failure.sqlite3",
        fetch=fail,
        record_decision=lambda _article_id, decision: decisions.append(decision),
        now=NOW,
    )

    with pytest.raises(OSError, match="unavailable"):
        adapter.fetch_detail(channel, index, context)

    assert decisions[-1]["outcome"] == "both_routes_failed"
    assert "primary_failure" in decisions[-1]
    assert "fallback_failure" in decisions[-1]


def test_kr36_records_bare_failure_after_www_payload_rejection(tmp_path):
    from ht_lead_radar.aggregate_adapters.base import AdapterContext

    adapter = Kr36Adapter()
    channel = adapter.channel_for("36kr-financing-flash")
    index = adapter.parse_listing(
        channel,
        _listing(),
        AdapterContext.create(
            state_db=tmp_path / "listing-rejection.sqlite3",
            fetch=lambda _url: b"",
            now=NOW,
        ),
    )[0]
    decisions = []

    def fetch(url: str) -> bytes:
        if url.startswith("https://www.36kr.com/"):
            return b"<html>TTGCaptcha</html>"
        raise OSError("bare route unavailable")

    context = AdapterContext.create(
        state_db=tmp_path / "rejection-failure.sqlite3",
        fetch=fetch,
        record_decision=lambda _article_id, decision: decisions.append(decision),
        now=NOW,
    )

    with pytest.raises(OSError, match="bare route unavailable"):
        adapter.fetch_detail(channel, index, context)

    assert decisions[-1]["primary_rejection"] == "captcha"
    assert decisions[-1]["outcome"] == "bare_route_failed_after_www_rejection"
    assert "fallback_failure" in decisions[-1]


def test_kr36_unknown_listing_is_not_treated_as_complete_negative():
    from ht_lead_radar.aggregate_adapters.models import SourceArticleIndex

    index = SourceArticleIndex(
        source_id="36kr-financing-flash",
        source_article_id="2001",
        channel="financing-flash",
        canonical_url="https://36kr.com/newsflashes/2001",
        title="\u661f\u6cb3\u82af\u7247\u8fce\u6765\u65b0\u8fdb\u5c55",
        published_at="2026-07-31",
        discovered_at=NOW.isoformat(),
        cursor_value="2001",
        listing_page="https://pitchhub.36kr.com/financing-flash",
        listing_position=1,
        content_hash="unknown",
        discovery_method="fixture",
        summary=(
            "\u661f\u6cb3\u82af\u7247\u8fce\u6765\u65b0\u8fdb\u5c55\uff0c"
            "\u66f4\u591a\u878d\u8d44\u8be6\u60c5\u8bf7\u67e5\u770b\u6b63\u6587\uff0c"
            "\u672c\u6761\u6458\u8981\u672a\u62ab\u9732\u4ea4\u6613\u72b6\u6001\u3002"
        ),
        structured_data={"company": "\u661f\u6cb3\u82af\u7247"},
    )

    assert Kr36Adapter._listing_event_complete(index) is False
    assert Kr36Adapter._listing_negative_complete(index) is False


def test_kr36_policy_words_do_not_hide_pending_funding_detail():
    from ht_lead_radar.aggregate_adapters.models import SourceArticleIndex

    index = SourceArticleIndex(
        source_id="36kr-financing-flash",
        source_article_id="2002",
        channel="financing-flash",
        canonical_url="https://36kr.com/newsflashes/2002",
        title="\u661f\u6cb3\u82af\u7247\u8d44\u672c\u52a8\u6001",
        published_at="2026-07-31",
        discovered_at=NOW.isoformat(),
        cursor_value="2002",
        listing_page="https://pitchhub.36kr.com/financing-flash",
        listing_position=1,
        content_hash="pending",
        discovery_method="fixture",
        summary=(
            "\u672c\u8f6e\u4ea4\u6613\u7531\u661f\u6cb3\u82af\u7247\u63a8\u8fdb\uff0c"
            "\u67d0\u94f6\u884c\u53c2\u4e0e\uff0c\u76f8\u5173\u76d1\u7ba1\u653f\u7b56\u4e0e"
            "\u878d\u8d44\u7ec6\u8282\u5c06\u5728\u6b63\u6587\u62ab\u9732\u3002"
        ),
        structured_data={"company": "\u661f\u6cb3\u82af\u7247"},
    )

    assert Kr36Adapter._listing_event_complete(index) is False
    assert Kr36Adapter._listing_negative_complete(index) is False
