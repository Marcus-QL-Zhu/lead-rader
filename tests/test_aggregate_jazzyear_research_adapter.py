from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.sites.jazzyear_research import (
    JazzyearResearchAdapter,
)


ADAPTER = JazzyearResearchAdapter()
CHANNEL = ADAPTER.channels[0]
NOW = datetime(2026, 8, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _context(tmp_path) -> AdapterContext:
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite",
        fetch=lambda _: b"",
        now=NOW,
    )


def _listing() -> bytes:
    rows = (
        ("171", "2026年AI行业月度观察报告（6月期）", "2026-07-29", "人工智能 · 科技投资", "月度观察"),
        ("166", "AI产业最新趋势解读：组织变革新机遇", "2026-06-09", "人工智能 · 数字化", "趋势判断"),
        ("167", "2026中国具身智能行业洞察报告", "2026-05-19", "人工智能 · 商业化", "深度行研"),
        ("165", "跨 OS GUI 智能体基础设施白皮书", "2026-04-13", "数字经济 · 数字化", "定义者"),
    )
    cards = "".join(
        f"""
        <a href="./study_info.html?id={article_id}" class="article-card-cover-sm">
          <div class="cover"><div class="tag">原创</div></div>
          <div class="center">
            <div class="title font-18">{title}</div>
            <div class="tags font-14">
              <span>{tags}</span><span class="subscribe-item">{research_type}</span>
            </div>
            <div class="bottom font-12">
              <span class="btn">报告</span><span class="time">{published}</span>
            </div>
          </div>
        </a>
        """
        for article_id, title, published, tags, research_type in rows
    )
    return f"""
    <html><head><title>甲子光年|中国科技产业智库</title></head><body>
      <div class="study-list">{cards}</div>
      <div class="paging-box"><ul><li class="page">1</li><li class="page">2</li></ul></div>
    </body></html>
    """.encode("utf-8")


def _detail(title: str, *, published: str = "2026-07-29") -> bytes:
    highlights = (
        "人工智能产业正在从模型能力竞争转向系统交付能力竞争。"
        "产业链企业开始围绕真实工作流、基础设施、治理合规和结果交付重建产品边界。"
        "资本定价逻辑也由参数规模转向单位资源可交付的有效任务，组织需要同步补齐技术、"
        "产品、商业化和生态合作能力。具身智能、先进计算和工业软件的跨领域协作因此加速。"
        "这类结构性变化会持续影响企业的研发投入、产业合作与组织建设节奏。"
    )
    return f"""
    <html><head><title>{title}</title></head><body>
      <div class="study-base">
        <div class="center">
          <div class="name">{title}</div>
          <div class="tags">
            <a><span class="item">人工智能</span></a>
            <a><span class="item">科技投资</span></a>
            <span class="item subscribe-item">月度观察</span>
            <span class="time">{published}</span>
          </div>
          <div class="option"><span class="label">版权所有：</span><span class="msg">甲子光年</span></div>
          <div class="option"><span class="label">报告简介：</span><span class="msg">本报告分析人工智能产业从模型能力走向系统交付的结构性变化，并梳理相关产业机会。</span></div>
        </div>
      </div>
      <div class="study-detail"><div class="study-block-title">核心亮点</div><div><p>{highlights}</p></div></div>
      <div class="study-detail"><div class="study-block-title">投资建议</div><div><p>无</p></div></div>
      <footer>关联推荐 登录 注册 联系我们</footer>
    </body></html>
    """.encode("utf-8")


def test_indexes_complete_visible_research_window_with_explicit_routes(tmp_path):
    indexes = ADAPTER.parse_listing(CHANNEL, _listing(), _context(tmp_path))

    assert len(indexes) == 4
    assert [item.listing_position for item in indexes] == [1, 2, 3, 4]
    assert [item.source_article_id for item in indexes] == ["171", "166", "167", "165"]
    assert indexes[0].canonical_url == "https://www.jazzyear.com/study_info.html?id=171"
    assert indexes[0].published_at == "2026-07-29T00:00:00+08:00"
    assert indexes[0].structured_data["document_type"] == "commentary"
    assert indexes[2].structured_data["document_type"] == "long_feature"
    assert indexes[0].structured_data["tags"] == ("人工智能", "科技投资")


def test_extracts_only_verified_report_summary_and_highlights(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]
    article = ADAPTER.parse_detail(CHANNEL, index, _detail(index.title), context)

    assert article.author == "甲子光年智库"
    assert article.fetch_status == "ok"
    assert article.extraction_method == "exact"
    assert article.structured_data["detail_published_at"] == "2026-07-29"
    assert article.structured_data["section_headings"] == ("核心亮点", "投资建议")
    assert article.clean_body.startswith("报告简介：")
    assert "核心亮点：" in article.clean_body
    assert "关联推荐" not in article.clean_body
    assert "登录" not in article.clean_body

    route = route_document(article)
    assert route.document_type == "commentary"
    assert route.reason == "adapter_document_type"
    assert isinstance(ADAPTER.rule_events(CHANNEL, article), list)


def test_scrapling_only_relocates_dom_and_business_invariants_still_hold(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]
    exact_html = _detail(index.title)
    exact = ADAPTER.parse_detail(CHANNEL, index, exact_html, context)
    relocated_html = (
        exact_html.replace(b'class="name"', b'class="report-name"')
    )

    relocated = ADAPTER.parse_detail(CHANNEL, index, relocated_html, context)

    assert exact.extraction_method == "exact"
    assert relocated.extraction_method == "adaptive"
    assert relocated.adaptive_similarity == 72
    assert relocated.content_hash == exact.content_hash


def test_listing_fails_closed_on_incomplete_unknown_or_unordered_window(tmp_path):
    context = _context(tmp_path)
    incomplete = _listing().replace(
        b'<a href="./study_info.html?id=165" class="article-card-cover-sm">',
        b'<div data-removed="165">',
    ).replace(b"</a>\n        \n    </body>", b"</div>\n        \n    </body>")
    with pytest.raises(ListingInvariantError, match="selector failed closed"):
        ADAPTER.parse_listing(CHANNEL, incomplete, context)

    unknown = _listing().replace(
        '<span class="subscribe-item">月度观察</span>'.encode(),
        '<span class="subscribe-item">未定义栏目</span>'.encode(),
        1,
    )
    with pytest.raises(ListingInvariantError, match="unknown research type"):
        ADAPTER.parse_listing(CHANNEL, unknown, context)

    unordered = _listing().replace(b"2026-06-09", b"2026-07-30", 1)
    with pytest.raises(ListingInvariantError, match="not newest-first"):
        ADAPTER.parse_listing(CHANNEL, unordered, context)


def test_listing_and_detail_reject_source_contract_violations(tmp_path):
    context = _context(tmp_path)
    external = _listing().replace(
        b'./study_info.html?id=171',
        b'https://example.invalid/study_info.html?id=171',
        1,
    )
    with pytest.raises(ListingInvariantError, match="invalid or duplicate"):
        ADAPTER.parse_listing(CHANNEL, external, context)

    extra_query = _listing().replace(
        b"./study_info.html?id=171",
        b"./study_info.html?id=171&tracking=1",
        1,
    )
    with pytest.raises(ListingInvariantError, match="invalid or duplicate"):
        ADAPTER.parse_listing(CHANNEL, extra_query, context)

    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]
    with pytest.raises(DetailFetchError, match="title mismatch"):
        ADAPTER.parse_detail(CHANNEL, index, _detail("另一份报告"), context)
    with pytest.raises(DetailFetchError, match="date mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index.title, published="2026-07-28"),
            context,
        )
    wrong_owner = _detail(index.title).replace(
        "甲子光年</span>".encode(),
        "未知机构</span>".encode(),
        1,
    )
    with pytest.raises(DetailFetchError, match="copyright marker mismatch"):
        ADAPTER.parse_detail(CHANNEL, index, wrong_owner, context)


def test_rejects_interstitial_without_bypass(tmp_path):
    context = _context(tmp_path)
    with pytest.raises(ListingInvariantError, match="no bypass"):
        ADAPTER.parse_listing(CHANNEL, b"<title>Just a moment</title>", context)

    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]
    with pytest.raises(DetailFetchError, match="no bypass"):
        ADAPTER.parse_detail(CHANNEL, index, b"403 Forbidden", context)
