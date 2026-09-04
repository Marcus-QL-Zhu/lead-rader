from datetime import datetime, timezone
import json
from urllib.parse import parse_qs, urlparse

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.miit import MiitAdapter


NOW = datetime(2026, 7, 30, 2, 0, tzinfo=timezone.utc)
POSITIVE_TITLE = (
    "工业和信息化部办公厅关于开展2026年人工智能创新任务"
    "揭榜挂帅工作的通知"
)
NEGATIVE_TITLE = "工业和信息化部2025年创新任务工作回顾"
QUERY_DATA = (
    "{'webId':'8d828e408d90447786ddbe128d495e9e',"
    "'pageId':'7df23bf39e2d42b793ebfcc3319015b7',"
    "'parseType':'buildstatic','pageType':'column',"
    "'tagId':'当前栏目_list',"
    "'tplSetId':'209741b2109044b5b7695700b2bec37e'}"
)


def _row(article_id: str, title: str, published_at: str) -> str:
    return f"""
    <li class="cf">
      <a class="fl"
         href="/jgsj/kjs/wjfb/art/{published_at[:4]}/art_{article_id}.html"
         title="{title}"><i></i>{title[:28]}...</a>
      <span class="fr">{published_at}</span>
    </li>
    """


def _page(
    rows: list[tuple[str, str, str]],
    *,
    page_number: int = 1,
    total_count: int = 24,
) -> bytes:
    body = "".join(_row(*row) for row in rows)
    total_pages = (total_count + 23) // 24
    return f"""
    <html><body>
      <div id="当前栏目_list">
        <div class="page-content"><ul>{body}</ul></div>
        <div class="pagination"
          querydata="{QUERY_DATA}"
          rows="24"
          unitid="当前栏目_list"
          count="{total_count}"
          pageno="{page_number}"
          uniturl="/api-gateway/jpaas-publish-server/front/page/build/unit">
          <span class="layui-laypage-curr"><em></em><em>{page_number}</em></span>
          <a class="layui-laypage-last" data-page="{total_pages}">尾页</a>
        </div>
      </div>
    </body></html>
    """.encode()


def _api_payload(fragment: bytes) -> bytes:
    return json.dumps(
        {
            "success": True,
            "code": "200",
            "data": {"html": fragment.decode()},
        },
        ensure_ascii=False,
    ).encode()


def _mutate_api(
    payload: bytes,
    old: str,
    new: str,
    *,
    count: int = -1,
) -> bytes:
    parsed = json.loads(payload.decode())
    parsed["data"]["html"] = parsed["data"]["html"].replace(
        old,
        new,
        count,
    )
    return json.dumps(parsed, ensure_ascii=False).encode()


def _closed_listing() -> bytes:
    rows = [
        ("e" * 32, POSITIVE_TITLE, "2026-07-28"),
        ("d" * 32, "工业和信息化部办公厅关于印发旧标准的通知", "2026-07-15"),
    ]
    rows.extend(
        (
            f"{number:032x}",
            f"科技司历史文件{number:02d}",
            "2026-06-30",
        )
        for number in range(1, 23)
    )
    return _api_payload(_page(rows))


def _detail(
    title: str,
    *,
    published_at: str = "2026-07-28 18:47",
    historical: bool = False,
) -> bytes:
    if historical:
        body = (
            "本文回顾2025年创新任务执行情况。此前工业和信息化部曾于"
            "2025年发布入围单位名单，相关任务目前已全部结束。"
            "文章只总结历史工作，不启动新的申报、遴选、试点或项目，"
            "也不构成新的政策、标准或名单发布。"
        )
    else:
        body = (
            "为加快推动科技创新与产业创新深度融合，现启动2026年首批"
            "工业和信息化领域创新任务揭榜挂帅工作。"
            "本批任务围绕未来产业、装备制造、信息技术、通信、人工智能"
            "和消费品六个方向，系统布局二十四个专题。"
            "工业和信息化部牵头组织遴选入围单位，并发布入围单位名单。"
            "各推荐单位应公开、公平、公正做好推荐工作。"
        )
    return f"""
    <html><head>
      <meta name="SiteIDCode" content="bm07000001">
      <meta name="ColumnName" content="文件发布">
    </head><body>
      <aside>相关阅读：某公司发布机器人产品</aside>
      <h1 id="con_title">{title}</h1>
      <div class="cinfo center">
        <span id="con_time">发布时间：{published_at}</span>
        <span>来源：科技司</span>
      </div>
      <div class="ccontent center" id="con_con">
        <p>{body}</p>
        <p>附件：人工智能方向创新任务揭榜挂帅申报指南.pdf</p>
      </div>
      <footer>网站地图 主办单位 版权所有</footer>
    </body></html>
    """.encode()


def _context(tmp_path, fetch=lambda _url: b""):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=fetch,
        now=NOW,
    )


def test_listing_enumerates_complete_closed_window_without_keyword_prefilter(
    tmp_path,
):
    adapter = MiitAdapter()
    indexes = adapter.parse_listing(
        adapter.channels[0],
        _closed_listing(),
        _context(tmp_path),
    )

    assert [item.source_article_id for item in indexes] == ["e" * 32]
    assert indexes[0].title == POSITIVE_TITLE
    assert indexes[0].published_at == "2026-07-28"
    assert indexes[0].listing_position == 1
    assert indexes[0].structured_data["closed_window_start"] == "2026-07-28"
    assert indexes[0].structured_data["closed_window_end"] == "2026-07-29"
    assert indexes[0].structured_data["archive_total_count"] == 24
    assert indexes[0].discovery_method == "exact"


def test_listing_uses_official_api_until_overflow_window_is_closed(tmp_path):
    adapter = MiitAdapter()
    current = [
        (f"{900 + number:032x}", f"当日未关闭文件{number}", "2026-07-30")
        for number in range(5)
    ]
    target = [
        (
            f"{1000 + number:032x}",
            f"完整窗口文件{number}",
            "2026-07-29" if number < 10 else "2026-07-28",
        )
        for number in range(19)
    ]
    first_page = _api_payload(_page(current + target, total_count=25))
    second_fragment = _page(
        [("f" * 32, "窗口以前的边界文件", "2026-07-27")],
        page_number=2,
        total_count=25,
    ).decode()
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return _api_payload(second_fragment.encode())

    indexes = adapter.parse_listing(
        adapter.channels[0],
        first_page,
        _context(tmp_path, fetch),
    )

    assert len(indexes) == 19
    assert not ({item.source_article_id for item in indexes} & {
        item[0] for item in current
    })
    assert len(calls) == 1
    query = parse_qs(urlparse(calls[0]).query)
    assert json.loads(query["paramJson"][0]) == {
        "pageNo": 2,
        "pageSize": "24",
    }
    assert calls[0].startswith(
        "https://www.miit.gov.cn/api-gateway/"
        "jpaas-publish-server/front/page/build/unit?"
    )


def test_detail_cleaning_and_industry_rules_keep_only_current_official_action(
    tmp_path,
):
    adapter = MiitAdapter()
    context = _context(tmp_path)
    index = adapter.parse_listing(
        adapter.channels[0],
        _closed_listing(),
        context,
    )[0]
    article = adapter.parse_detail(
        adapter.channels[0],
        index,
        _detail(index.title),
        context,
    )

    assert "现启动2026年首批" in article.clean_body
    assert "人工智能方向创新任务揭榜挂帅申报指南" in article.clean_body
    assert "相关阅读" not in article.clean_body
    assert "网站地图" not in article.clean_body
    assert article.author == "科技司"
    assert article.structured_data["company"] == "工业和信息化部"
    assert article.extraction_method == "exact"

    events = adapter.rule_events(adapter.channels[0], article)
    assert len(events) == 1
    event = events[0]
    assert event.canonical_company == "工业和信息化部"
    assert event.event_type == "policy_or_standard"
    assert event.event_status == "started"
    assert event.event_date == "2026-07-28"
    assert event.processor == "rules:miit-v1"
    assert event.evidence_quotes == (POSITIVE_TITLE,)
    assert "artificial_intelligence" in event.industry_tags


def test_historical_review_is_not_a_new_policy_increment(tmp_path):
    adapter = MiitAdapter()
    context = _context(tmp_path)
    index = adapter.parse_listing(
        adapter.channels[0],
        _closed_listing(),
        context,
    )[0]
    historical_index = type(index)(
        **{
            **index.to_dict(),
            "title": NEGATIVE_TITLE,
            "content_hash": adapter.stable_hash(NEGATIVE_TITLE),
        }
    )
    article = adapter.parse_detail(
        adapter.channels[0],
        historical_index,
        _detail(NEGATIVE_TITLE, historical=True),
        context,
    )

    assert adapter.rule_events(adapter.channels[0], article) == []


def test_exact_selector_drift_adapts_and_row_loss_fails_closed(tmp_path):
    adapter = MiitAdapter()
    context = _context(tmp_path)
    listing = _closed_listing()
    adapter.parse_listing(adapter.channels[0], listing, context)

    drifted = _mutate_api(
        listing,
        'id="当前栏目_list"',
        'id="科技司文件列表"',
    )
    relocated = adapter.parse_listing(
        adapter.channels[0],
        drifted,
        context,
    )
    assert len(relocated) == 1
    assert relocated[0].discovery_method == "adaptive"

    missing = _mutate_api(
        listing,
        '<li class="cf">',
        '<li>',
        count=1,
    )
    with pytest.raises(ListingInvariantError, match="row count"):
        adapter.parse_listing(
            adapter.channels[0],
            missing,
            _context(tmp_path / "fresh"),
        )


def test_access_metadata_and_detail_mismatches_fail_closed(tmp_path):
    adapter = MiitAdapter()
    context = _context(tmp_path)
    interstitial = (
        b"<html><title>Just a moment</title>"
        b"<script src='/cdn-cgi/challenge-platform/x.js'></script></html>"
    )
    with pytest.raises(ListingInvariantError, match="no bypass"):
        adapter.parse_listing(adapter.channels[0], interstitial, context)

    failed_api = json.dumps(
        {
            "success": False,
            "code": "500",
            "data": {"html": _page([]).decode()},
        },
        ensure_ascii=False,
    ).encode()
    with pytest.raises(ListingInvariantError, match="API status invalid"):
        adapter.parse_listing(
            adapter.channels[0],
            failed_api,
            _context(tmp_path / "failed-api"),
        )

    invalid_pagination = _mutate_api(
        _closed_listing(),
        'uniturl="/api-gateway/jpaas-publish-server/front/page/build/unit"',
        'uniturl="https://example.com/page"',
    )
    with pytest.raises(ListingInvariantError, match="endpoint rejected"):
        adapter.parse_listing(
            adapter.channels[0],
            invalid_pagination,
            _context(tmp_path / "invalid"),
        )

    index = adapter.parse_listing(
        adapter.channels[0],
        _closed_listing(),
        context,
    )[0]
    with pytest.raises(DetailFetchError, match="title mismatch"):
        adapter.parse_detail(
            adapter.channels[0],
            index,
            _detail("完全不同的文件标题"),
            context,
        )
    with pytest.raises(DetailFetchError, match="date mismatch"):
        adapter.parse_detail(
            adapter.channels[0],
            index,
            _detail(index.title, published_at="2026-07-27 18:47"),
            context,
        )


def test_second_coordinator_run_does_not_refetch_unchanged_detail(tmp_path):
    adapter = MiitAdapter()
    channel = adapter.channels[0]
    current_rows = [
        ("e" * 32, POSITIVE_TITLE, "2026-07-28"),
        ("c" * 32, "工业和信息化部关于启动先进制造试点的通知", "2026-07-28"),
    ]
    current_rows.extend(
        (f"{number:032x}", f"科技司历史文件{number:02d}", "2026-06-30")
        for number in range(1, 23)
    )
    listing = _api_payload(_page(current_rows))
    indexes = adapter.parse_listing(
        channel,
        listing,
        _context(tmp_path),
    )
    network = {channel.url: listing}
    network.update({item.canonical_url: _detail(item.title) for item in indexes})
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
    first = coordinator.collect_source(channel.source_id, "人工智能")
    network[channel.url] = _api_payload(
        _page([current_rows[1], current_rows[0], *current_rows[2:]])
    )
    calls.clear()
    second = coordinator.collect_source(channel.source_id, "人工智能")

    assert first.run.listing_count == 2
    assert first.run.detail_success_count == 2
    assert second.run.incremental_count == 0
    assert first.run.listing_count == second.run.listing_count == 2
    assert calls == [channel.url]


def test_default_registry_routes_miit_science_files():
    registry = DedicatedAdapterRegistry.defaults()

    adapter = registry.for_source("miit-science-files")
    assert isinstance(adapter, MiitAdapter)
