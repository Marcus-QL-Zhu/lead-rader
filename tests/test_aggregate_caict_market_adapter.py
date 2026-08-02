from datetime import datetime, timezone
import re

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.sites.caict_market import (
    CaictMarketAnalysisAdapter,
)


NOW = datetime(2026, 8, 1, 2, 0, tzinfo=timezone.utc)
ADAPTER = CaictMarketAnalysisAdapter()
CHANNEL = ADAPTER.channels[0]
REPORTS = (
    ("july", 7, "2026-07-30"),
    ("june", 6, "2026-07-28"),
    ("may", 5, "2026-07-27"),
    ("april", 4, "2026-07-26"),
    ("march", 3, "2026-07-25"),
    ("february", 2, "2026-07-24"),
    ("january", 1, "2026-07-23"),
)


def _report_title(month: int) -> str:
    return (
        f"中国信通院发布2026年{month}月国内手机市场运行分析报告："
        "出货量3000万部，其中5G手机占比95.0%"
    )


def _row(href: str, title: str, published_at: str) -> str:
    return f"""
    <div class="row p-2">
      <div class="col-md-10"><a href="{href}">{title}</a></div>
      <div class="col-md-2"><span>{published_at}</span></div>
    </div>
    """


def _listing() -> bytes:
    rows = [
        _row(
            "/plat/news/caict-release-china-mobile-phone-market-analysis-"
            "report-july-2026",
            _report_title(7),
            "2026-07-30",
        ),
        _row(
            "/plat/news/ai-glasses-industry-convention",
            "《AI眼镜可信视界自律公约》正式发布",
            "2026-07-29",
        ),
    ]
    rows.extend(
        _row(
            "/plat/news/caict-release-china-mobile-phone-market-analysis-"
            f"report-{month_name}-2026",
            _report_title(month),
            published_at,
        )
        for month_name, month, published_at in REPORTS[1:]
    )
    rows.extend(
        _row(
            f"/plat/news/industry-news-{number}",
            f"中国信通院产业公开信息第{number}期",
            f"2026-07-{22 - number:02d}",
        )
        for number in range(12)
    )
    assert len(rows) == 20
    return f"""
    <html><body><main>
      <div class="row mt-0 mb-1">
        <div class="col-lg-3 col-md-4 col-sm-12 mb-2 h-100">栏目</div>
        <div class="col-lg-9 col-md-8 col-sm-12 mb-2">
          {''.join(rows)}
          <ul class="pagination">
            <li class="page-item"><span class="page-link">总数74, 共4页</span></li>
            <li class="page-item disabled"><span class="page-link">«</span></li>
          </ul>
        </div>
      </div>
    </main></body></html>
    """.encode()


def _detail(
    title: str,
    *,
    published_at: str = "2026-07-30",
    author: str = "CTTL-T",
    body_class: str = "post-body",
) -> bytes:
    sections = (
        "一、国内手机市场总体情况",
        "2026年7月，国内市场手机出货量3000.0万部，同比增长16.5%，"
        "其中5G手机2850.0万部，占同期手机出货量的95.0%。"
        "国内市场继续围绕高端芯片、智能终端和供应链协同推进产品迭代。",
        "二、国内手机市场国内外品牌构成",
        "国产品牌手机出货量2600.0万部，同比增长25.0%，占同期手机"
        "出货量的86.7%；国产品牌上市新机型15款，占同期数量的78.9%。"
        "市场结构显示本土软硬件生态仍在扩张。",
        "三、国内智能手机发展情况",
        "智能手机出货量2910.0万部，同比增长19.0%，占同期手机出货量"
        "的97.0%；智能手机上市新机型18款，产业创新保持活跃。"
        "人工智能终端升级推动芯片、软件和算法能力持续演进。",
        "指导单位：工业和信息化部信息通信管理局",
        "报告完成单位：中国信息通信研究院",
    )
    body = "".join(f"<p>{section}</p>" for section in sections)
    return f"""
    <html><body><main>
      <div class="row mt-0 mb-1">
        <div class="col-lg-3 col-md-4 col-sm-12 mb-2 h-100">栏目</div>
        <div class="col-lg-9 col-md-8 col-sm-12 mb-2">
          <h4 class="text-center py-2">{title}</h4>
          <p class="text-center">
            <span class="post-meta m-2">作者：{author}</span>
            <span class="post-meta m-2">发布时间：{published_at}</span>
          </p>
          <hr>
          <div class="{body_class}">{body}</div>
        </div>
      </div>
    </main></body></html>
    """.encode()


def _context(tmp_path, decisions=None):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
        record_decision=(
            (lambda key, value: decisions.append((key, value)))
            if decisions is not None
            else None
        ),
    )


def test_listing_returns_six_newest_verified_market_analyses(tmp_path):
    decisions = []
    indexes = ADAPTER.parse_listing(
        CHANNEL,
        _listing(),
        _context(tmp_path, decisions),
    )

    assert len(indexes) == 6
    assert [item.listing_position for item in indexes] == list(range(1, 7))
    assert [item.structured_data["report_month"] for item in indexes] == [
        7,
        6,
        5,
        4,
        3,
        2,
    ]
    assert all(item.channel == "业内新闻—产业运行分析" for item in indexes)
    assert all(
        item.structured_data["document_type"] == "commentary"
        for item in indexes
    )
    assert all(item.discovery_method == "exact" for item in indexes)
    assert decisions == [
        (
            "listing_window",
            {
                "page_row_count": 20,
                "validated_report_count": 7,
                "selected_report_count": 6,
                "adaptive_used": False,
            },
        )
    ]


def test_detail_is_explicit_commentary_and_never_invents_company_event(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]
    article = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(index.title),
        context,
    )

    assert article.author == "CTTL-T"
    assert article.fetch_status == "ok"
    assert article.extraction_method == "exact"
    assert article.structured_data["section_count"] == 3
    assert article.structured_data["document_type"] == "commentary"
    assert "国内市场手机出货量3000.0万部" in article.clean_body
    assert route_document(article).document_type == "commentary"
    assert route_document(article).reason == "adapter_document_type"
    assert ADAPTER.rule_events(CHANNEL, article) == []


def test_scrapling_only_relocates_dom_and_business_rules_remain_strict(tmp_path):
    context = _context(tmp_path)
    listing = _listing()
    ADAPTER.parse_listing(CHANNEL, listing, context)
    relocated = listing.replace(b'row p-2', b'row news-entry')

    indexes = ADAPTER.parse_listing(CHANNEL, relocated, context)

    assert len(indexes) == 6
    assert all(item.discovery_method == "adaptive" for item in indexes)

    invalid = relocated.replace(b"report-july-2026", b"report-june-2026", 1)
    with pytest.raises(ListingInvariantError, match="month/year identity mismatch"):
        ADAPTER.parse_listing(CHANNEL, invalid, context)


def test_listing_fails_closed_on_pagination_row_loss_and_ordering(tmp_path):
    listing = _listing()
    missing_row = listing.replace(
        _row(
            "/plat/news/industry-news-11",
            "中国信通院产业公开信息第11期",
            "2026-07-11",
        ).encode(),
        b"",
    )
    with pytest.raises(ListingInvariantError, match="row count"):
        ADAPTER.parse_listing(CHANNEL, missing_row, _context(tmp_path / "lost"))

    out_of_order = listing.replace(
        b"<span>2026-07-29</span>",
        b"<span>2026-07-31</span>",
    )
    with pytest.raises(ListingInvariantError, match="not newest-first"):
        ADAPTER.parse_listing(CHANNEL, out_of_order, _context(tmp_path / "order"))

    too_few_reports = listing.replace(
        "中国信通院发布2026年2月国内手机市场运行分析报告".encode(),
        "中国信通院发布2026年2月手机市场简讯".encode(),
    )
    with pytest.raises(ListingInvariantError, match="title/URL identity mismatch"):
        ADAPTER.parse_listing(
            CHANNEL,
            too_few_reports,
            _context(tmp_path / "reports"),
        )


def test_detail_fails_closed_on_identity_metadata_and_body_errors(tmp_path):
    context = _context(tmp_path)
    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]

    with pytest.raises(DetailFetchError, match="title mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail("完全不同的市场报告"),
            context,
        )
    with pytest.raises(DetailFetchError, match="date mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index.title, published_at="2026-07-29"),
            context,
        )
    with pytest.raises(DetailFetchError, match="unexpected detail author"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index.title, author="匿名转载"),
            context,
        )
    short_body = re.sub(
        rb'<div class="post-body">.*?</div>',
        '<div class="post-body"><p>简讯</p></div>'.encode(),
        _detail(index.title),
        count=1,
        flags=re.S,
    )
    with pytest.raises(DetailFetchError, match="body length"):
        ADAPTER.parse_detail(CHANNEL, index, short_body, context)


def test_access_interstitial_is_rejected_without_bypass(tmp_path):
    context = _context(tmp_path)
    with pytest.raises(ListingInvariantError, match="no bypass"):
        ADAPTER.parse_listing(
            CHANNEL,
            b"<title>Just a moment</title>",
            context,
        )

    index = ADAPTER.parse_listing(CHANNEL, _listing(), context)[0]
    with pytest.raises(DetailFetchError, match="no bypass"):
        ADAPTER.parse_detail(CHANNEL, index, b"403 Forbidden", context)
