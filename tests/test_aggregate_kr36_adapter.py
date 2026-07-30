from datetime import datetime, timezone
import sqlite3

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
            f"https://36kr.com/newsflashes/{article_id}": _detail(title, company)
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
            f"https://36kr.com/newsflashes/{article_id}": _detail(title, company)
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
