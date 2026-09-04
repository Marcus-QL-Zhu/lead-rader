from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
)
from ht_lead_radar.aggregate_adapters.models import CleanArticle
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites import stcn as stcn_module
from ht_lead_radar.aggregate_adapters.sites.stcn import StcnAdapter


NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)
CHINA = ZoneInfo("Asia/Shanghai")
DAY_END = 1785340799
DAY_START = 1785254400
ADAPTER = StcnAdapter()
CHANNEL = ADAPTER.channel_for("stcn-flash")


def _landing(*, drifted: bool = False) -> bytes:
    css_class = "stream-list" if drifted else "list infinite-list"
    return f"""
    <html><body>
      <div class="login-modal">验证码登录</div>
      <ul id="flash-feed" class="{css_class}" data-role="flash-feed"
          data-url="/article/list.html?type=kx"></ul>
    </body></html>
    """.encode()


def _item(article_id: int, timestamp: int, title: str, summary: str):
    return {
        "item_id": article_id,
        "wap_title": title,
        "wap_content": summary,
        "content": f"【{title}】{summary}",
        "time": timestamp,
        "stocks": [],
        "is_red": 0,
        "style_type": "fast_info",
        "jump_type": "fast_info",
        "jump_index": str(article_id),
        "tags": [{"tag_name": "人工智能"}],
    }


def _fixture_items():
    return [
        _item(
            4100001,
            DAY_END - 10,
            "瑞萨中国任命新总裁",
            "人民财讯7月29日电，瑞萨中国宣布任命王明为新总裁，负责在华业务。",
        ),
        _item(
            4100002,
            DAY_END - 20,
            "主要指数收盘涨跌互现",
            "人民财讯7月29日电，沪指收涨0.2%，深证成指收跌0.1%。",
        ),
        _item(
            4100003,
            DAY_END - 30,
            "行业报告讨论扩产预期",
            "人民财讯7月29日电，报告仅讨论行业未来扩产预期，未宣布企业事件。",
        ),
        _item(
            4100004,
            DAY_START + 20,
            "公告精选：硬科技公司动态",
            "甲芯科技计划新建晶圆产线并扩产。乙机器人获得头部客户订单。",
        ),
        _item(
            4100005,
            DAY_START + 10,
            "星河芯片计划新建晶圆产线",
            "人民财讯7月29日电，星河芯片计划新建晶圆产线并扩大产能。",
        ),
    ]


def _api_document(page: int, items, *, first_id: int, max_id: int):
    document = {
        "status": 1,
        "msg": "操作成功",
        "page": page,
        "max_id": max_id,
        "first_id": first_id,
    }
    if items is not None:
        document["data"] = items
    return json.dumps(document, ensure_ascii=False).encode()


def _api_pages():
    items = _fixture_items()
    first_id = items[0]["time"]
    return {
        1: _api_document(
            1,
            items[:3],
            first_id=first_id,
            max_id=items[2]["time"],
        ),
        2: _api_document(
            2,
            items[3:],
            first_id=first_id,
            max_id=items[4]["time"],
        ),
        3: _api_document(
            3,
            None,
            first_id=first_id,
            max_id=items[4]["time"],
        ),
    }


def _context(tmp_path, pages=None, calls=None):
    pages = pages or _api_pages()

    def fetch(url):
        if calls is not None:
            calls.append(url)
        query = parse_qs(urlparse(url).query)
        assert query["path"] == ["news-fast_info_list"]
        params = json.loads(query["other_param"][0])
        return pages[params["page"]]

    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=fetch,
        now=NOW,
    )


def _indexes(tmp_path):
    return ADAPTER.parse_listing(
        CHANNEL,
        _landing(),
        _context(tmp_path),
    )


def _detail(
    index,
    *,
    drifted=False,
    title=None,
    timestamp=None,
    article_id=None,
    body=None,
):
    title_class = "article-title" if drifted else "detail-title"
    info_class = "article-meta" if drifted else "detail-info"
    body_class = "article-body" if drifted else "detail-content"
    id_class = "article-like" if drifted else "like-btn"
    actual_timestamp = (
        timestamp
        if timestamp is not None
        else int(index.cursor_value.split("|")[0])
    )
    published = datetime.fromtimestamp(actual_timestamp, CHINA)
    return f"""
    <html><body>
      <nav>导航噪声</nav>
      <div id="story-head" class="{title_class}" data-role="headline">{title or index.title}</div>
      <div id="story-meta" class="{info_class}" data-role="metadata">
        来源：人民财讯 作者：钟恬 {published:%Y-%m-%d %H:%M}
      </div>
      <div id="story-copy" class="{body_class}" data-role="article-body">
        <p>{body or index.summary}</p>
        <script>推荐文章中的企业宣布扩产</script>
      </div>
      <div id="story-identity" class="{id_class}" data-role="article-identity"
           data-id="{article_id or index.source_article_id}"></div>
      <aside>相关推荐中的企业获得订单</aside>
      <footer>页脚噪声</footer>
    </body></html>
    """.encode()


def test_stcn_closed_day_indexes_every_api_item_and_terminates(tmp_path):
    calls = []
    indexes = ADAPTER.parse_listing(
        CHANNEL,
        _landing(),
        _context(tmp_path, calls=calls),
    )

    assert [item.source_article_id for item in indexes] == [
        "4100001",
        "4100002",
        "4100003",
        "4100004",
        "4100005",
    ]
    assert [item.listing_position for item in indexes] == [1, 2, 3, 4, 5]
    assert len(calls) == 3
    assert indexes[3].structured_data["api_page"] == 2
    assert all(item.published_at.startswith("2026-07-29") for item in indexes)
    assert any(item.title == "主要指数收盘涨跌互现" for item in indexes)
    for request_url in calls:
        other = json.loads(parse_qs(urlparse(request_url).query)["other_param"][0])
        assert other["start_time"] == other["end_time"] == "2026-07-29"
        assert "tab_names" not in other
        assert "is_red" not in other


def test_stcn_pagination_fails_closed_without_terminal_page(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(stcn_module, "_MAX_PAGES", 2)
    with pytest.raises(ListingInvariantError, match="terminal page"):
        ADAPTER.parse_listing(
            CHANNEL,
            _landing(),
            _context(tmp_path),
        )


def test_stcn_detail_parser_excludes_navigation_recommendations_and_scripts(
    tmp_path,
):
    index = _indexes(tmp_path)[0]
    article = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(index),
        _context(tmp_path),
    )

    assert article.clean_body == index.summary
    assert article.author == "钟恬"
    assert article.extraction_method == "exact"
    assert "导航噪声" not in article.clean_body
    assert "相关推荐" not in article.clean_body
    assert "页脚噪声" not in article.clean_body
    assert "推荐文章" not in article.clean_body


def test_stcn_uses_shared_industry_rules_for_positive_negative_and_multi_event(
    tmp_path,
):
    indexes = _indexes(tmp_path)
    executive = CleanArticle(
        index=indexes[0],
        clean_body=indexes[0].summary,
        content_hash="executive",
    )
    market_noise = CleanArticle(
        index=indexes[1],
        clean_body=indexes[1].summary,
        content_hash="market",
    )
    commentary = CleanArticle(
        index=indexes[2],
        clean_body=indexes[2].summary,
        content_hash="commentary",
    )
    multi_index = replace(
        indexes[3],
        summary="",
        structured_data={
            **indexes[3].structured_data,
            "company": "",
        },
    )
    multi = CleanArticle(
        index=multi_index,
        clean_body=(
            "甲芯科技计划新建晶圆产线并扩产。"
            "乙机器人获得头部客户订单。"
        ),
        content_hash="multi",
    )

    events = ADAPTER.rule_events(CHANNEL, executive)
    assert {
        (event.canonical_company, event.event_type, event.event_status)
        for event in events
    } == {("瑞萨中国", "executive_change", "completed")}
    assert events[0].processor == "rules:stcn-v1"
    assert ADAPTER.rule_events(CHANNEL, market_noise) == []
    assert ADAPTER.rule_events(CHANNEL, commentary) == []
    assert {
        (event.canonical_company, event.event_type, event.event_status)
        for event in ADAPTER.rule_events(CHANNEL, multi)
    } == {
        ("甲芯科技", "factory_or_capacity", "started"),
        ("乙机器人", "major_order", "completed"),
    }


def test_stcn_adaptive_recovery_and_business_invariants_fail_closed(tmp_path):
    index = _indexes(tmp_path)[0]
    context = _context(tmp_path)
    exact = ADAPTER.parse_detail(CHANNEL, index, _detail(index), context)
    moved = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(index, drifted=True),
        context,
    )

    assert exact.extraction_method == "exact"
    assert moved.extraction_method == "adaptive"
    assert moved.adaptive_similarity == 72

    with pytest.raises(DetailFetchError, match="title mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, title="完全不相关的详情标题"),
            context,
        )
    with pytest.raises(DetailFetchError, match="date mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, timestamp=DAY_START + 60),
            context,
        )
    with pytest.raises(DetailFetchError, match="id mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, article_id="9999999"),
            context,
        )
    with pytest.raises(DetailFetchError, match="body mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, body="完全不相关的正文内容，长度足够但不对应列表摘要。"),
            context,
        )


def test_stcn_landing_selector_adapts_but_endpoint_invariant_remains(tmp_path):
    context = _context(tmp_path)
    ADAPTER.parse_listing(CHANNEL, _landing(), context)
    moved = ADAPTER.parse_listing(CHANNEL, _landing(drifted=True), context)

    assert {
        item.structured_data["landing_selector_method"] for item in moved
    } == {"adaptive"}
    bad = _landing().replace(
        b"/article/list.html?type=kx",
        b"/article/search.html?keyword=AI",
    )
    with pytest.raises(ListingInvariantError, match="unexpected public"):
        ADAPTER.parse_listing(CHANNEL, bad, context)


def test_stcn_prefilter_keeps_executive_change_and_skips_market_noise(tmp_path):
    indexes = _indexes(tmp_path)
    assert ADAPTER.should_fetch_detail(CHANNEL, indexes[0])
    assert not ADAPTER.should_fetch_detail(CHANNEL, indexes[1])
    assert ADAPTER.should_fetch_detail(CHANNEL, indexes[2])


def test_stcn_compound_digest_does_not_join_headline_subject_to_first_item(
    tmp_path,
):
    index = replace(
        _indexes(tmp_path)[3],
        title=(
            "公告精选：豪能股份拟10亿元投建机器人关节减速器生产基地"
        ),
        summary="",
        structured_data={},
    )
    article = CleanArticle(
        index=index,
        clean_body=(
            "人民财讯7月29日电，【热点】*ST华闻：7月31日起撤销退市风险"
            "警示，继续实施其他风险警示。"
            "【中标合同】行云科技：全资子公司签订算力服务补充协议，"
            "合同额增至30.53亿元。"
            "【其他】豪能股份：拟10亿元投建机器人关节减速器生产基地项目。"
        ),
        content_hash="compound",
    )

    events = ADAPTER.rule_events(CHANNEL, article)

    assert ("豪能股份", "factory_or_capacity") in {
        (event.canonical_company, event.event_type)
        for event in events
    }
    assert all("*ST华闻" not in event.evidence_quotes[0] for event in events)
    assert all(
        event.canonical_company != "豪能股份拟10亿元投建机器人关节减速器"
        for event in events
    )

def test_stcn_compound_digest_supplements_eight_explicit_events(tmp_path):
    index = replace(
        _indexes(tmp_path)[3],
        title="公告精选：多公司公告",
        summary="",
        structured_data={},
    )
    article = CleanArticle(
        index=index,
        clean_body=(
            "天智航：拟购买上海骨科62%股权，30日复牌。"
            "亿田智能：全资子公司签署11.06亿元算力资源服务合同。"
            "蓝宇股份：拟设立控股子公司，投建埃及纺织项目。"
            "天和磁材：拟设立控股子公司，补齐市场服务短板。"
            "浙江正特：拟与关联方设立合资公司，拓展宠物用品业务。"
            "中远海发：全资子公司新建15艘21万吨级散货船。"
            "*ST长投：拟公开挂牌转让参股公司全部股权。"
        ),
        content_hash="eight-supplements",
    )

    events = ADAPTER.rule_events(CHANNEL, article)

    assert {
        (event.canonical_company, event.event_type, event.event_status)
        for event in events
    } == {
        ("天智航", "merger_acquisition", "started"),
        ("亿田智能", "major_order", "completed"),
        ("蓝宇股份", "factory_or_capacity", "started"),
        ("蓝宇股份", "new_site_or_entity", "started"),
        ("天和磁材", "new_site_or_entity", "started"),
        ("浙江正特", "new_site_or_entity", "started"),
        ("中远海发", "factory_or_capacity", "started"),
        ("*ST长投", "merger_acquisition", "started"),
    }
    assert all(
        event.canonical_company in event.evidence_quotes[0]
        for event in events
    )

def test_stcn_second_run_does_not_refetch_unchanged_details(tmp_path):
    pages = _api_pages()
    context = _context(tmp_path, pages)
    indexes = ADAPTER.parse_listing(CHANNEL, _landing(), context)
    calls = []

    def fetch(url):
        calls.append(url)
        if url == CHANNEL.url:
            return _landing()
        if "/article/detail/" in url:
            index = next(item for item in indexes if item.canonical_url == url)
            return _detail(index)
        params = json.loads(parse_qs(urlparse(url).query)["other_param"][0])
        return pages[params["page"]]

    state_db = tmp_path / "coordinator.sqlite3"
    coordinator = DedicatedAggregateCoordinator(
        state_db=state_db,
        registry=DedicatedAdapterRegistry((StcnAdapter(),)),
        fetch=fetch,
        now=NOW,
    )
    first = coordinator.collect_source(CHANNEL.source_id, "硬科技")
    items = _fixture_items()
    first_id = items[0]["time"]
    pages.clear()
    pages.update(
        {
            1: _api_document(
                1,
                items[:2],
                first_id=first_id,
                max_id=items[1]["time"],
            ),
            2: _api_document(
                2,
                items[2:],
                first_id=first_id,
                max_id=items[4]["time"],
            ),
            3: _api_document(
                3,
                None,
                first_id=first_id,
                max_id=items[4]["time"],
            ),
        }
    )
    calls.clear()
    second = coordinator.collect_source(CHANNEL.source_id, "硬科技")

    assert first.run.listing_count == 5
    assert first.run.incremental_count == 5
    assert first.run.prefiltered_count == 1
    assert first.run.detail_success_count == 4
    assert second.run.incremental_count == 0
    assert second.run.prefiltered_count == 0
    assert not any("/article/detail/" in url for url in calls)
    assert calls[0] == CHANNEL.url
    assert len(calls) == 4

    connection = sqlite3.connect(state_db)
    assert connection.execute(
        "SELECT COUNT(*) FROM aggregate_article_index"
    ).fetchone()[0] == 5
    assert connection.execute(
        """
        SELECT COUNT(*) FROM aggregate_clean_articles
        WHERE json_extract(article_json, '$.fetch_status') = 'prefiltered'
        """
    ).fetchone()[0] == 1
    connection.close()
