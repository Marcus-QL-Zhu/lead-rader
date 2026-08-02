from datetime import datetime, timezone

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.sites.shanghai_fgw_annual_plan import (
    ShanghaiFgwAnnualPlanAdapter,
)


NOW = datetime(2026, 8, 1, 2, tzinfo=timezone.utc)


def _context(tmp_path):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
        now=NOW,
    )


def _title(planned_year: int) -> str:
    return (
        f"关于上海市{planned_year - 1}年国民经济和社会发展计划执行情况与"
        f"{planned_year}年国民经济和社会发展计划草案的报告"
    )


def _path(planned_year: int, digit: str) -> str:
    return f"/fgw_ndjh/{planned_year}0123/{digit * 32}.html"


def _listing(*, moved: bool = False, marker: str = "3100000087") -> bytes:
    items = []
    for offset, planned_year in enumerate((2026, 2025, 2024, 2023)):
        digit = "abcdef"[offset]
        title = _title(planned_year)
        items.append(
            f'<li><a href="{_path(planned_year, digit)}" title="{title}">'
            f"{title}</a><span class=\"time\">{planned_year}-01-23</span></li>"
        )
    items.append(
        '<li><a href="/fgw_ndjh/20230820/ffffffffffffffffffffffffffffffff.html" '
        'title="关于上海市2023年上半年国民经济和社会发展计划执行情况的报告">'
        "关于上海市2023年上半年国民经济和社会发展计划执行情况的报告"
        '</a><span class="time">2023-08-20</span></li>'
    )
    list_class = "moved-list" if moved else "news-list"
    return (
        "<html><head>"
        '<meta name="SiteIDCode" content="'
        + marker
        + '"><meta name="ColumnName" content="年度计划">'
        "</head><body><ul class=\""
        + list_class
        + '\">'
        + "".join(items)
        + "</ul></body></html>"
    ).encode("utf-8")


def _detail(
    planned_year: int = 2026,
    *,
    title: str | None = None,
    date: str | None = None,
    future_markers: bool = True,
    moved: bool = False,
    site_id: str = "3100000087",
) -> bytes:
    title = title or _title(planned_year)
    date = date or f"{planned_year}-01-23 10:01:19"
    intro = (
        f"关于上海市{planned_year - 1}年国民经济和社会发展计划执行情况与"
        f"{planned_year}年国民经济和社会发展计划草案，请予审议。"
    )
    future = (
        f"二、{planned_year}年国民经济和社会发展主要任务。"
        "主要预期目标和全年目标已经明确，计划安排一批集成电路、人工智能、"
        "商业航天、机器人和先进制造重大项目，加快建设研发平台和产业基地。"
        if future_markers
        else "二、上一年度工作回顾。有关部门总结了已经完成的工作。"
    )
    paragraphs = "".join(
        f"<p>{intro if number == 0 else future}"
        f"第{number + 1}项任务由各责任部门推进项目建设、技术攻关、产业升级和人才引育，"
        "围绕目标形成年度实施路径和量化任务。</p>"
        for number in range(28)
    )
    body_attributes = (
        'id="moved-content" class="moved-body"'
        if moved
        else 'id="ivs_content" class="Article_content trout-region-content"'
    )
    return (
        "<html><head>"
        f'<meta name="SiteIDCode" content="{site_id}">'
        '<meta name="SiteName" content="上海市发展和改革委员会">'
        '<meta name="ColumnName" content="年度计划">'
        f'<meta name="ArticleTitle" content="{title}">'
        f'<meta name="PubDate" content="{date}">'
        '<meta name="ContentSource" content="上海市发展和改革委员会">'
        f"</head><body><div {body_attributes}>{paragraphs}</div></body></html>"
    ).encode("utf-8")


def test_listing_enumerates_four_roadmaps_and_excludes_execution_only(tmp_path):
    adapter = ShanghaiFgwAnnualPlanAdapter()
    indexes = adapter.parse_listing(
        adapter.channels[0],
        _listing(),
        _context(tmp_path),
    )

    assert len(indexes) == 4
    assert [item.structured_data["planned_year"] for item in indexes] == [
        2026,
        2025,
        2024,
        2023,
    ]
    assert all(
        item.structured_data["document_type_target"] == "roadmap"
        for item in indexes
    )
    assert all("上半年" not in item.title for item in indexes)
    assert all(item.discovery_method == "exact" for item in indexes)
    assert all(adapter.should_fetch_detail(adapter.channels[0], item) for item in indexes)


def test_detail_labels_roadmap_only_after_explicit_future_markers(tmp_path):
    adapter = ShanghaiFgwAnnualPlanAdapter()
    context = _context(tmp_path)
    index = adapter.parse_listing(adapter.channels[0], _listing(), context)[0]

    article = adapter.parse_detail(
        adapter.channels[0],
        index,
        _detail(),
        context,
    )

    assert article.fetch_status == "ok"
    assert article.extraction_method == "exact"
    assert article.structured_data["document_type"] == "roadmap"
    assert article.structured_data["roadmap_plan_year"] == 2026
    assert all(article.structured_data["roadmap_markers"].values())
    assert article.author == "上海市发展和改革委员会"
    assert len(article.clean_body) >= 2_000

    with pytest.raises(DetailFetchError, match="future roadmap markers missing"):
        adapter.parse_detail(
            adapter.channels[0],
            index,
            _detail(future_markers=False),
            context,
        )


def test_scrapling_relocates_dom_but_cannot_relax_source_invariants(tmp_path):
    adapter = ShanghaiFgwAnnualPlanAdapter()
    context = _context(tmp_path)
    indexes = adapter.parse_listing(adapter.channels[0], _listing(), context)
    moved_indexes = adapter.parse_listing(
        adapter.channels[0],
        _listing(moved=True),
        context,
    )
    assert len(moved_indexes) == 4
    assert all(item.discovery_method == "adaptive" for item in moved_indexes)

    adapter.parse_detail(adapter.channels[0], indexes[0], _detail(), context)
    moved_article = adapter.parse_detail(
        adapter.channels[0],
        indexes[0],
        _detail(moved=True),
        context,
    )
    assert moved_article.extraction_method == "adaptive"
    assert moved_article.adaptive_similarity == 72

    with pytest.raises(DetailFetchError, match="official detail marker mismatch"):
        adapter.parse_detail(
            adapter.channels[0],
            indexes[0],
            _detail(site_id="wrong"),
            context,
        )


def test_listing_and_detail_fail_closed_on_identity_date_and_access(tmp_path):
    adapter = ShanghaiFgwAnnualPlanAdapter()
    context = _context(tmp_path)
    index = adapter.parse_listing(adapter.channels[0], _listing(), context)[0]

    with pytest.raises(ListingInvariantError, match="official listing marker"):
        adapter.parse_listing(
            adapter.channels[0],
            _listing(marker="wrong"),
            context,
        )
    rejected_host = _listing().replace(
        _path(2026, "a").encode(),
        b"https://example.com/fgw_ndjh/20260123/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.html",
        1,
    )
    with pytest.raises(ListingInvariantError, match="malformed annual-plan"):
        adapter.parse_listing(adapter.channels[0], rejected_host, context)
    with pytest.raises(DetailFetchError, match="title mismatch"):
        adapter.parse_detail(
            adapter.channels[0],
            index,
            _detail(title=_title(2025)),
            context,
        )
    with pytest.raises(DetailFetchError, match="date mismatch"):
        adapter.parse_detail(
            adapter.channels[0],
            index,
            _detail(date="2026-01-22 10:01:19"),
            context,
        )
    with pytest.raises(ListingInvariantError, match="no bypass"):
        adapter.parse_listing(adapter.channels[0], b"403 Forbidden", context)
    with pytest.raises(DetailFetchError, match="no bypass"):
        adapter.parse_detail(adapter.channels[0], index, b"Access Denied", context)
