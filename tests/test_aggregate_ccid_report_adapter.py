from datetime import datetime, timezone

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.sites.ccid_report import (
    CcidReportCommentaryAdapter,
)


NOW = datetime(2026, 8, 1, 2, tzinfo=timezone.utc)
ADAPTER = CcidReportCommentaryAdapter()
CHANNEL = ADAPTER.channels[0]


def _context(tmp_path):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
    )


def _card(
    *,
    category,
    section,
    article_id,
    day,
    position,
    bad_url=False,
    bad_timestamp=False,
):
    full_title = f"{category}：先进制造产业趋势与政策影响分析第{position}篇"
    display_title = full_title if position % 2 else f"{full_title[:15]}…"
    href = (
        f"https://evil.example/{section}/{article_id}.jhtml"
        if bad_url
        else f"/{section}/{article_id}.jhtml"
    )
    image = (
        "/u/cms/qbzx/not-a-date/image.jpeg"
        if bad_timestamp
        else f"/u/cms/qbzx/202607/{day:02d}12345{position}image.jpeg"
    )
    return f"""
    <div class="case-item" data-cat="{category}">
      <div class="case-header">
        <div class="case-thumb"><img src="{image}" alt="{full_title}"></div>
        <div class="case-info">
          <h3><a href="{href}">{display_title}</a></h3>
          <p class="desc">这是一段足够长且可核验的公开摘要，用于说明产业趋势、政策背景以及潜在组织影响。</p>
          <div class="case-meta"><span class="tag">先进制造</span></div>
        </div>
      </div>
    </div>
    """


def _listing(*, drifted=False, bad_url=False, bad_timestamp=False):
    panel_class = "relocated-panel" if drifted else "tab-panel"
    expert = "".join(
        _card(
            category="专家观点",
            section="zjgd2",
            article_id=1121900 + position,
            day=31 - position,
            position=position,
            bad_url=bad_url and position == 1,
            bad_timestamp=bad_timestamp and position == 1,
        )
        for position in range(1, 5)
    )
    policy = "".join(
        _card(
            category="政策解读",
            section="zcjd",
            article_id=1121800 + position,
            day=31 - position,
            position=position,
        )
        for position in range(1, 5)
    )
    return f"""
    <main>
      <div class="{panel_class}" data-channelid="3112">
        <div class="news-list">{expert}</div>
      </div>
      <div class="{panel_class}" data-channelid="3114">
        <div class="news-list">{policy}</div>
      </div>
    </main>
    """.encode()


def _detail(index, *, drifted=False, title=None, date=None, short=False):
    area_class = "relocated-area" if drifted else "content-area"
    body = (
        "过短"
        if short
        else (
            "本文结合宏观数据与产业调研分析先进制造业的结构变化，"
            "并讨论技术创新、产业升级和政策落地之间的关系。"
            "研究认为，企业需要持续增加研发投入、优化供应链协同机制，"
            "同时围绕关键技术建设专业团队。"
            "政策实施将带动示范项目、设备更新和数字化改造需求，"
            "地方产业集群也需要完善公共服务平台与人才支撑体系。"
            "从中长期看，产业竞争将从单点产品能力转向系统工程能力，"
            "组织建设和跨部门协作将成为实现规模化交付的重要基础。"
            "因此，应当建立连续监测机制，对政策、投资与技术路线进行综合研判。"
        )
    )
    return f"""
    <div class="{area_class}">
      <div class="article-header">
        <h1 class="article-title">{title or index.title}</h1>
        <span class="article-source">来源：赛迪&nbsp;&nbsp;{date or index.published_at[:10]}</span>
      </div>
      <div class="tab-content active">
        <div class="article-content"><p>{body}</p></div>
      </div>
      <div class="recommendations">推荐阅读噪声</div>
    </div>
    """.encode()


def test_listing_captures_complete_commentary_window(tmp_path):
    indexes = ADAPTER.parse_listing(CHANNEL, _listing(), _context(tmp_path))

    assert len(indexes) == 8
    assert [item.listing_position for item in indexes] == list(range(1, 9))
    assert sum(item.channel == "专家观点" for item in indexes) == 4
    assert sum(item.channel == "政策解读" for item in indexes) == 4
    assert indexes[0].title.endswith("第1篇")
    assert indexes[1].title.endswith("第2篇")
    assert indexes[0].published_at.startswith("2026-07-30")
    assert indexes[4].canonical_url == (
        "https://www.ccidreport.com/zcjd/1121801.jhtml"
    )
    assert indexes[0].structured_data["document_type"] == "commentary"


def test_detail_cross_checks_title_date_and_routes_commentary(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]

    article = ADAPTER.parse_detail(CHANNEL, index, _detail(index), context)

    assert article.extraction_method == "exact"
    assert article.fetch_status == "ok"
    assert article.structured_data["detail_source"] == "赛迪"
    assert "推荐阅读噪声" not in article.clean_body
    assert route_document(article).document_type == "commentary"
    assert "专家观点" in article.tags


def test_adapter_fails_closed_on_listing_and_detail_invariant_drift(tmp_path):
    context = _context(tmp_path)
    with pytest.raises(ListingInvariantError, match="rejected.*URL"):
        ADAPTER.parse_listing(CHANNEL, _listing(bad_url=True), context)
    with pytest.raises(ListingInvariantError, match="CMS timestamp"):
        ADAPTER.parse_listing(
            CHANNEL,
            _listing(bad_timestamp=True),
            _context(tmp_path / "timestamp"),
        )

    index = ADAPTER.parse_listing(
        CHANNEL,
        _listing(),
        _context(tmp_path / "detail"),
    )[0]
    detail_context = _context(tmp_path / "detail-checks")
    with pytest.raises(DetailFetchError, match="title mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, title="无关标题"),
            detail_context,
        )
    with pytest.raises(DetailFetchError, match="date mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, date="2026-07-29"),
            detail_context,
        )
    with pytest.raises(DetailFetchError, match="body too short"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, short=True),
            detail_context,
        )


def test_scrapling_only_relocates_dom_and_interstitials_fail_closed(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]
    ADAPTER.parse_detail(CHANNEL, index, _detail(index), context)

    relocated = ADAPTER.parse_listing(CHANNEL, _listing(drifted=True), context)
    assert all(item.discovery_method == "adaptive" for item in relocated)
    relocated_detail = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(index, drifted=True),
        context,
    )
    assert relocated_detail.extraction_method == "adaptive"

    with pytest.raises(ListingInvariantError, match="no bypass"):
        ADAPTER.parse_listing(CHANNEL, b"Just a moment", context)
    with pytest.raises(DetailFetchError, match="no bypass"):
        ADAPTER.parse_detail(CHANNEL, index, b"Access Denied", context)
