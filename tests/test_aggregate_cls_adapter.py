from datetime import datetime, timezone
import json
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.models import CleanArticle
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.cls import ClsAdapter


NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)
ADAPTER = ClsAdapter()
CHANNEL = ADAPTER.channel_for("cls-telegraph")
DAY_END = 1785340799
DAY_START = 1785254400


def _item(article_id, ctime, title, content):
    return {
        "id": article_id,
        "ctime": ctime,
        "title": title,
        "content": content,
        "level": "C",
        "subjects": [{"subject_name": "半导体"}],
    }


def _api(items):
    return json.dumps({"errno": 0, "data": {"roll_data": items}}).encode()


def _listing_routes():
    first = [
        _item(
            2440001,
            DAY_END - 10,
            "星河芯片计划新建晶圆产线",
            "【星河芯片计划新建晶圆产线】财联社7月29日电，星河芯片计划新建晶圆产线并扩产。",
        ),
        _item(
            2440002,
            DAY_END - 20,
            "",
            "【行业报告完成订单金额统计】财联社7月29日电，报告仅讨论市场规模。",
        ),
    ]
    second = [
        _item(
            2440003,
            DAY_START + 5,
            "乙机器人获得头部客户订单",
            "【乙机器人获得头部客户订单】财联社7月29日电，乙机器人获得头部客户订单。",
        ),
        _item(
            2439999,
            DAY_START - 1,
            "边界之前的电报",
            "财联社7月28日电，此条用于证明分页已经越过目标自然日边界。",
        ),
    ]
    return {
        DAY_END: _api(first),
        DAY_END - 20: _api(second),
    }


def _context(tmp_path, routes, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        cursor = int(parse_qs(urlparse(url).query)["last_time"][0])
        return routes[cursor]

    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=fetch,
        now=NOW,
    )


def _detail(index, *, body_class="telegraph-detail-body", title=None):
    actual_title = title or index.title
    return f"""
    <html><body>
      <nav>导航噪声与推荐内容</nav>
      <h1>{actual_title}</h1>
      <time data-ctime="{index.cursor_value.split("|")[0]}"></time>
      <div class="{body_class}">{index.summary}</div>
      <footer>页脚噪声</footer>
    </body></html>
    """.encode()


def _next_detail(index):
    detail = {
        "id": int(index.source_article_id),
        "title": index.title,
        "content": index.summary,
        "ctime": int(index.cursor_value.split("|")[0]),
        "author": {},
    }
    payload = {"props": {"pageProps": {"articleDetail": detail}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script>"
    ).encode()


def test_cls_closed_day_paginates_until_crossing_boundary(tmp_path):
    calls = []
    articles = ADAPTER.parse_listing(
        CHANNEL,
        b"<html><title>CLS telegraph</title></html>",
        _context(tmp_path, _listing_routes(), calls),
    )

    assert [item.source_article_id for item in articles] == [
        "2440001",
        "2440002",
        "2440003",
    ]
    assert [item.listing_position for item in articles] == [1, 2, 3]
    assert len(calls) == 2
    assert articles[-1].structured_data["api_page"] == 2
    assert articles[-1].listing_page == calls[-1]
    assert all(item.published_at.startswith("2026-07-29") for item in articles)


def test_cls_short_api_title_falls_back_to_bracketed_content():
    item = _item(
        2440999,
        DAY_END - 30,
        "快讯",
        "【星河芯片完成新产线投产】财联社7月29日电，星河芯片完成新产线投产。",
    )

    assert ADAPTER._item_title(item, item["content"]) == "星河芯片完成新产线投产"


def test_cls_listing_fails_closed_without_boundary_proof(tmp_path):
    routes = {
        DAY_END: _api(
            [
                _item(
                    1, DAY_END - 1, "有效标题文本", "足够长的公开电报正文内容用于测试。"
                )
            ]
        )
    }
    with pytest.raises((ListingInvariantError, KeyError)):
        ADAPTER.parse_listing(
            CHANNEL,
            b"<html></html>",
            _context(tmp_path, routes),
        )


def test_cls_embedded_detail_is_clean_and_date_bound(tmp_path):
    index = ADAPTER.parse_listing(
        CHANNEL,
        b"<html></html>",
        _context(tmp_path, _listing_routes()),
    )[0]
    article = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _next_detail(index),
        _context(tmp_path, {}),
    )

    assert article.extraction_method == "embedded-json"
    assert article.clean_body == index.summary
    assert "导航噪声" not in article.clean_body


def test_cls_uses_shared_industry_rules_positive_negative_and_multi_event(tmp_path):
    indexes = ADAPTER.parse_listing(
        CHANNEL,
        b"<html></html>",
        _context(tmp_path, _listing_routes()),
    )
    positive = CleanArticle(
        index=indexes[0],
        clean_body=indexes[0].summary,
        content_hash="positive",
    )
    negative = CleanArticle(
        index=indexes[1],
        clean_body=indexes[1].summary,
        content_hash="negative",
    )
    multi = CleanArticle(
        index=indexes[0],
        clean_body="甲芯科技计划新建晶圆产线并扩产。乙机器人获得头部客户订单。",
        content_hash="multi",
    )

    events = ADAPTER.rule_events(CHANNEL, positive)
    assert {(event.event_type, event.event_status) for event in events} == {
        ("factory_or_capacity", "started")
    }
    assert events[0].processor == "rules:cls-v1"
    assert ADAPTER.rule_events(CHANNEL, negative) == []
    assert {
        (event.canonical_company, event.event_type)
        for event in ADAPTER.rule_events(CHANNEL, multi)
    } == {
        ("星河芯片", "factory_or_capacity"),
        ("甲芯科技", "factory_or_capacity"),
        ("乙机器人", "major_order"),
    }


def test_cls_scrapling_adaptive_recovery_and_invariant_failure(tmp_path):
    index = ADAPTER.parse_listing(
        CHANNEL,
        b"<html></html>",
        _context(tmp_path, _listing_routes()),
    )[0]
    context = _context(tmp_path, {})
    exact = ADAPTER.parse_detail(CHANNEL, index, _detail(index), context)
    moved = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(index, body_class="telegraph-detail-content"),
        context,
    )

    assert exact.extraction_method == "exact"
    assert moved.extraction_method == "adaptive"
    assert moved.adaptive_similarity == 72
    with pytest.raises(DetailFetchError):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, title="完全不相关的详情标题"),
            context,
        )


def test_cls_second_run_does_not_refetch_unchanged_details(tmp_path):
    routes = _listing_routes()
    indexes = ADAPTER.parse_listing(
        CHANNEL,
        b"<html></html>",
        _context(tmp_path, routes),
    )
    calls = []

    def fetch(url):
        calls.append(url)
        if url == CHANNEL.url:
            return b"<html><title>CLS telegraph</title></html>"
        if "/detail/" in url:
            index = next(item for item in indexes if item.canonical_url == url)
            return _next_detail(index)
        cursor = int(parse_qs(urlparse(url).query)["last_time"][0])
        return routes[cursor]

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "coordinator.sqlite3",
        registry=DedicatedAdapterRegistry((ClsAdapter(),)),
        fetch=fetch,
        now=NOW,
    )
    first = coordinator.collect_source(CHANNEL.source_id, "硬科技")
    first_page = json.loads(routes[DAY_END])["data"]["roll_data"]
    second_page = json.loads(routes[DAY_END - 20])["data"]["roll_data"]
    routes.clear()
    routes.update(
        {
            DAY_END: _api(first_page[:1]),
            DAY_END - 10: _api([first_page[1], *second_page]),
        }
    )
    calls.clear()
    second = coordinator.collect_source(CHANNEL.source_id, "硬科技")

    assert first.run.incremental_count == 3
    assert second.run.incremental_count == 0
    assert not any("/detail/" in url for url in calls)
    connection = sqlite3.connect(tmp_path / "coordinator.sqlite3")
    assert (
        connection.execute("SELECT COUNT(*) FROM aggregate_clean_articles").fetchone()[
            0
        ]
        == 3
    )
    connection.close()
