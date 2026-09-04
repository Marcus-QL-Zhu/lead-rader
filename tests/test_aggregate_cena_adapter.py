from datetime import datetime, timezone

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.models import SourceArticleIndex
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.cena import CenaAdapter


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)
ADAPTER = CenaAdapter()
CHANNEL = ADAPTER.channels[0]
ISSUE = "2026年07月31日"
POLICY_URL = "https://epaper.cena.com.cn/pc/layout/202607/31/node_02.html"
CHIP_URL = "https://epaper.cena.com.cn/pc/layout/202607/31/node_03.html"


def _issue_home(*, link_class="issue-page"):
    return f"""<html><body>
      <ul id="list" style="display:none">
        <li class="{link_class}"><a href="202607/31/node_01.html">第01版 要闻</a></li>
        <li class="{link_class}"><a href="202607/31/node_02.html">第02版 政策解读</a></li>
        <li class="{link_class}"><a href="202607/31/node_03.html">第03版 集成电路</a></li>
      </ul>
    </body></html>""".encode()


def _section(page, name, articles, *, article_list_id="articlelist"):
    links = "".join(
        f"""<li class="clearfix"><img src="bullet.png" />
        <a href="../../../content/202607/31/content_{article_id}.html">{title}</a>
        </li>"""
        for article_id, title in articles
    )
    return f"""<html><body>
      <div class="header-time pull-right" id="paperdate">{ISSUE}</div>
      <span id="layout">第{page:02d}版：<span class="font-weight">{name}</span></span>
      <ul class="newsList" id="{article_list_id}">{links}</ul>
    </body></html>""".encode()


def _pages():
    return {
        POLICY_URL: _section(
            2,
            "政策解读",
            [
                (17940, "人工智能赋能制造业政策释放新动能"),
                (17943, "编辑：齐旭"),
            ],
        ),
        CHIP_URL: _section(
            3,
            "集成电路",
            [
                (17941, "先进封装成为人工智能芯片竞争关键"),
                (17942, "半导体设备国产化进入系统协同阶段"),
            ],
        ),
    }


def _context(tmp_path, pages=None, decisions=None):
    page_map = pages or _pages()

    def fetch(url):
        return page_map[url]

    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=fetch,
        now=NOW,
        record_decision=(
            (lambda key, payload: decisions.append((key, payload)))
            if decisions is not None
            else None
        ),
    )


def _detail(
    title,
    *,
    date=ISSUE,
    title_id="Title",
    body_id="ozoom",
    date_id="paperdate",
):
    body = (
        "芯舟半导体宣布新建先进封装生产基地正式投产，项目形成晶圆级封装、"
        "系统级封装和可靠性验证能力。公司表示，新基地将面向人工智能芯片与"
        "汽车电子客户提供批量交付服务，并继续扩充工艺、设备和质量团队。"
        "产业人士认为，先进封装正从单点设备突破转向材料、工艺、设备与软件的"
        "系统协同，客户验证周期和量产爬坡能力成为下一阶段竞争关键。"
        "随着订单结构趋于复杂，企业需要建立覆盖研发导入、制造交付和供应链"
        "协同的组织能力，以支持多产品并行和跨区域客户服务。"
    )
    return f"""<html><body>
      <form><input name="siteID" value="1" /></form>
      <div class="header-time pull-right" id="{date_id}">{date}</div>
      <div class="detail-art">
        <h2 id="{title_id}" class="art-title text-center">{title}</h2>
        <div class="author" id="Author">本报记者 张芯</div>
        <div id="{body_id}" class="content"><founder-content>
          <p>{body}</p><p>产业观察</p>
        </founder-content></div>
      </div>
      <footer>版面导航 推荐阅读 上一篇 下一篇</footer>
    </body></html>""".encode()


def test_indexes_every_article_in_analysis_sections_and_skips_news(tmp_path):
    decisions = []
    indexes = ADAPTER.parse_listing(
        CHANNEL,
        _issue_home(),
        _context(tmp_path, decisions=decisions),
    )

    assert len(indexes) == 4
    assert [item.listing_position for item in indexes] == [1, 2, 3, 4]
    assert [item.channel for item in indexes] == [
        "政策解读",
        "政策解读",
        "集成电路",
        "集成电路",
    ]
    assert indexes[0].canonical_url.endswith("/content_17940.html")
    assert indexes[0].published_at == "2026-07-31T00:00:00+08:00"
    assert indexes[0].structured_data["document_type_target"] == (
        "commentary",
        "long_feature",
    )
    assert decisions[0][1]["analysis_sections"] == ["政策解读", "集成电路"]
    assert ADAPTER.should_fetch_detail(CHANNEL, indexes[0]) is True
    assert ADAPTER.should_fetch_detail(CHANNEL, indexes[1]) is False


def test_new_section_item_shifts_page_rank_without_refetching_old_details(tmp_path):
    pages = _pages()
    initial = ADAPTER.parse_listing(CHANNEL, _issue_home(), _context(tmp_path, pages))
    new_id = 17999
    new_title = "智能制造基础设施建设进入新阶段"
    new_item = f"""<li class="clearfix"><img src="bullet.png" />
      <a href="../../../content/202607/31/content_{new_id}.html">{new_title}</a>
      </li>""".encode()
    pages[POLICY_URL] = pages[POLICY_URL].replace(
        b'<ul class="newsList" id="articlelist">',
        b'<ul class="newsList" id="articlelist">' + new_item,
    )
    moved = ADAPTER.parse_listing(CHANNEL, _issue_home(), _context(tmp_path, pages))
    initial_hashes = {item.source_article_id: item.content_hash for item in initial}
    moved_hashes = {item.source_article_id: item.content_hash for item in moved}
    assert all(moved_hashes[key] == value for key, value in initial_hashes.items())

    original_pages = _pages()
    network = {CHANNEL.url: _issue_home(), **original_pages}
    network.update(
        {
            item.canonical_url: _detail(item.title)
            for item in initial
            if ADAPTER.should_fetch_detail(CHANNEL, item)
        }
    )
    new_index = next(item for item in moved if item.source_article_id == str(new_id))
    network[new_index.canonical_url] = _detail(new_title)
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return network[url]

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "coordinator.sqlite3",
        registry=DedicatedAdapterRegistry((ADAPTER,)),
        fetch=fetch,
        now=NOW,
    )
    first = coordinator.collect_source(CHANNEL.source_id, "硬科技")
    network.update(pages)
    calls.clear()
    second = coordinator.collect_source(CHANNEL.source_id, "硬科技")

    assert first.run.incremental_count == len(initial)
    assert second.run.incremental_count == 1
    assert new_index.canonical_url in calls
    assert not any(
        item.canonical_url in calls
        for item in initial
        if ADAPTER.should_fetch_detail(CHANNEL, item)
    )


def test_short_decorative_anchor_is_skipped_but_invalid_article_id_still_fails_closed(
    tmp_path,
):
    pages = _pages()
    pages[POLICY_URL] = pages[POLICY_URL].replace(
        b'<a href="../../../content/202607/31/content_17940.html">',
        b'<a class="decorative" aria-hidden="true" '
        b'href="../../../content/202607/31/content_17940.html">',
    ).replace(
        "人工智能赋能制造业政策释放新动能".encode(), "导读".encode()
    )
    decisions = []
    indexes = ADAPTER.parse_listing(
        CHANNEL,
        _issue_home(),
        _context(tmp_path, pages=pages, decisions=decisions),
    )

    assert len(indexes) == 3
    assert any(
        item[1].get("reason") == "structural_decoration"
        for item in decisions
    )

    pages[POLICY_URL] = pages[POLICY_URL].replace(
        b"content_17940.html", b"content_invalid.html"
    )
    with pytest.raises(ListingInvariantError, match="rejected section article"):
        ADAPTER.parse_listing(CHANNEL, _issue_home(), _context(tmp_path, pages=pages))


def test_legitimate_short_editorial_title_is_not_dropped(tmp_path):
    pages = _pages()
    pages[POLICY_URL] = pages[POLICY_URL].replace(
        "人工智能赋能制造业政策释放新动能".encode(), "AI".encode()
    )

    indexes = ADAPTER.parse_listing(
        CHANNEL,
        _issue_home(),
        _context(tmp_path, pages=pages),
    )

    assert any(item.title == "AI" for item in indexes)


def test_empty_non_decorative_article_title_recovers_from_detail(tmp_path):
    pages = _pages()
    pages[POLICY_URL] = pages[POLICY_URL].replace(
        "人工智能赋能制造业政策释放新动能".encode(), b""
    )
    pages["https://epaper.cena.com.cn/pc/content/202607/31/content_17940.html"] = _detail(
        "人工智能赋能制造业政策释放新动能"
    )

    indexes = ADAPTER.parse_listing(CHANNEL, _issue_home(), _context(tmp_path, pages=pages))
    assert any(item.title == "人工智能赋能制造业政策释放新动能" for item in indexes)


def test_listing_router_prefilters_exact_public_service_advertisement() -> None:
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id="17963",
        channel="新型显示",
        canonical_url=(
            "https://epaper.cena.com.cn/pc/content/202607/31/content_17963.html"
        ),
        title="公益广告",
        published_at="2026-07-31T00:00:00+08:00",
        discovered_at="2026-08-01T09:00:00+08:00",
        cursor_value="17963",
        listing_page="https://epaper.cena.com.cn/pc/layout/202607/31/node_04.html",
        listing_position=1,
        content_hash="hash",
        discovery_method="exact",
    )

    assert ADAPTER.should_fetch_detail(CHANNEL, index) is False


def test_detail_extracts_only_article_body_and_reuses_industry_rules(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _issue_home(), context)[2]
    article = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(index.title),
        context,
    )

    assert "新建先进封装生产基地正式投产" in article.clean_body
    assert "版面导航" not in article.clean_body
    assert article.author == "本报记者 张芯"
    assert article.extraction_method == "exact"
    assert article.structured_data["detail_published_at"] == "2026-07-31"
    events = ADAPTER.rule_events(CHANNEL, article)
    assert "factory_or_capacity" in {event.event_type for event in events}
    assert all(event.canonical_url == index.canonical_url for event in events)


def test_policy_interpretation_section_has_explicit_commentary_route(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _issue_home(), context)[0]
    article = ADAPTER.parse_detail(CHANNEL, index, _detail(index.title), context)

    route = route_document(article)

    assert route.document_type == "commentary"
    assert route.reason == "adapter_document_type"


def test_scrapling_relocates_detail_dom_but_business_invariants_still_apply(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _issue_home(), context)[2]
    exact = ADAPTER.parse_detail(CHANNEL, index, _detail(index.title), context)
    relocated = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(
            index.title,
            title_id="Headline",
            body_id="articleBody",
            date_id="issueDate",
        ),
        context,
    )

    assert exact.extraction_method == "exact"
    assert relocated.extraction_method == "adaptive"
    assert relocated.adaptive_similarity == 72
    assert relocated.content_hash == exact.content_hash


def test_rejects_stale_or_incomplete_issue_window(tmp_path):
    stale = _issue_home().replace(b"202607/31", b"202607/20")
    with pytest.raises(ListingInvariantError, match="stale"):
        ADAPTER.parse_listing(CHANNEL, stale, _context(tmp_path))

    incomplete = _issue_home().replace(
        b'<a href="202607/31/node_02.html">',
        b'<a href="202607/31/node_04.html">',
    )
    with pytest.raises(ListingInvariantError, match="section link"):
        ADAPTER.parse_listing(CHANNEL, incomplete, _context(tmp_path))


def test_rejects_section_and_detail_contract_violations(tmp_path):
    pages = _pages()
    pages[POLICY_URL] = pages[POLICY_URL].replace(
        b"content_17940.html",
        b"https://example.invalid/content_17940.html",
    )
    with pytest.raises(ListingInvariantError, match="rejected section article"):
        ADAPTER.parse_listing(CHANNEL, _issue_home(), _context(tmp_path, pages))

    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _issue_home(), context)[2]
    with pytest.raises(DetailFetchError, match="title mismatch"):
        ADAPTER.parse_detail(CHANNEL, index, _detail("另一篇文章"), context)
    with pytest.raises(DetailFetchError, match="date mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index.title, date="2026年07月30日"),
            context,
        )


def test_rejects_interstitial_without_bypass(tmp_path):
    context = _context(tmp_path)
    with pytest.raises(ListingInvariantError, match="no bypass"):
        ADAPTER.parse_listing(CHANNEL, b"<title>Just a moment</title>", context)

    index = ADAPTER.parse_listing(CHANNEL, _issue_home(), context)[0]
    with pytest.raises(DetailFetchError, match="no bypass"):
        ADAPTER.parse_detail(CHANNEL, index, b"403 Forbidden", context)
