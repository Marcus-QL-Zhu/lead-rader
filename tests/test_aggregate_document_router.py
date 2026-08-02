from __future__ import annotations

from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex


def _article(
    body: str,
    *,
    title: str = "公司动态",
    structured_data: dict | None = None,
    tags: tuple[str, ...] = (),
) -> CleanArticle:
    return CleanArticle(
        index=SourceArticleIndex(
            source_id="test",
            source_article_id="1",
            channel="test",
            canonical_url="https://example.test/1",
            title=title,
            published_at="2026-08-01T08:00:00+08:00",
            discovered_at="2026-08-01T09:00:00+08:00",
            cursor_value="1",
            listing_page="https://example.test",
            listing_position=1,
            content_hash="index-hash",
            discovery_method="fixture",
        ),
        clean_body=body,
        tags=tags,
        structured_data=structured_data or {},
        content_hash="article-hash",
    )


def test_router_uses_adapter_boundaries_and_preserves_all_text() -> None:
    body = "导语\n第一家公司完成融资。\n第二家公司宣布扩产。\n尾注"
    first = body.index("第一家")
    second = body.index("第二家")
    article = _article(
        body,
        structured_data={
            "item_boundaries": [
                {"char_start": first, "char_end": second},
                {"char_start": second, "char_end": body.index("\n尾注")},
            ]
        },
    )

    route = route_document(article)

    assert route.document_type == "multi_company_bulletin"
    assert route.reason == "adapter_item_boundaries"
    assert "".join(unit.text for unit in route.units) == body
    assert all(unit.boundary_source == "adapter" for unit in route.units)


def test_router_detects_roadmap_before_long_feature() -> None:
    article = _article(
        "公司计划于2027年建设新产线。" + "技术说明。" * 2500,
        title="未来三年发展路线图",
    )

    route = route_document(article, max_unit_chars=1000)

    assert route.document_type == "roadmap"
    assert len(route.units) > 1
    assert "".join(unit.text for unit in route.units) == article.clean_body
    assert max(len(unit.text) for unit in route.units) <= 1000


def test_router_uses_deterministic_bulletin_headings() -> None:
    body = "1、甲公司\n甲公司完成融资。\n2、乙公司\n乙公司签署订单。"

    route = route_document(_article(body, title="本周融资快报"))

    assert route.document_type == "multi_company_bulletin"
    assert route.reason == "bulletin_structure"
    assert len(route.units) == 2
    assert "".join(unit.text for unit in route.units) == body


def test_router_detects_inline_chinese_list_headings_after_newline_normalization() -> None:
    body = (
        "\u57fa\u91d1\u7ba1\u7406\u4eba\u4ecb\u7ecd "
        "\u4e00\u3001\u7532\u57fa\u91d1\u7ba1\u7406\u6709\u9650\u516c\u53f8\u6210\u7acb\u3002 "
        "\u4e8c\u3001\u4e59\u57fa\u91d1\u7ba1\u7406\u6709\u9650\u516c\u53f8\u6210\u7acb\u3002"
    )

    route = route_document(
        _article(body, title="8\u6708\u65b0\u767b\u8bb05\u5bb6\u79c1\u52df\u57fa\u91d1\u7ba1\u7406\u4eba")
    )

    assert route.document_type == "multi_company_bulletin"
    assert route.reason == "bulletin_structure"
    assert len(route.units) == 3
    assert "".join(unit.text for unit in route.units) == body


def test_router_detects_roundup_title_without_numbered_headings() -> None:
    article = _article(
        "甲公司宣布融资。乙公司启动扩产。丙公司获得订单。",
        title="公告精选：甲公司完成融资；乙公司启动扩产；丙公司获得订单",
    )

    route = route_document(article)

    assert route.document_type == "multi_company_bulletin"
    assert route.reason == "bulletin_structure"


def test_router_detects_commentary_from_discourse_not_only_title() -> None:
    body = (
        "甲公司完成融资。业内人士认为产业进入整合期。"
        "值得注意的是，资金并不等于量产能力。" + "行业背景说明。" * 180
    )

    route = route_document(_article(body, title="甲公司完成新一轮融资"))

    assert route.document_type == "commentary"
    assert route.reason == "commentary_discourse"


def test_router_detects_roadmap_from_multiple_future_action_sentences() -> None:
    body = (
        "甲公司完成融资。公司计划建设新工厂；"
        "预计明年启动量产；目标到2028年形成十万台产能。" + "技术背景。" * 120
    )

    route = route_document(_article(body, title="甲公司完成融资"))

    assert route.document_type == "roadmap"
    assert route.reason == "future_action_density"


def test_router_uses_realistic_long_feature_threshold() -> None:
    route = route_document(_article("技术与产业分析。" * 400, title="产业进展"))

    assert route.document_type == "long_feature"
    assert route.reason == "feature_length_or_title"


def test_numbered_single_company_sections_are_not_a_company_bulletin() -> None:
    body = "1、技术路径\n详细说明。\n2、产品规划\n后续说明。"

    route = route_document(_article(body, title="甲公司产品分析"))

    assert route.document_type != "multi_company_bulletin"


def test_router_uses_exact_industry_research_metadata() -> None:
    route = route_document(
        _article(
            "产业供需分析。" * 120,
            title="先进封装市场年度报告",
            structured_data={"tags": ["行业研究", "半导体"]},
        )
    )

    assert route.document_type == "commentary"
    assert route.reason == "explicit_industry_research"


def test_router_uses_exact_human_authored_policy_section_tag() -> None:
    route = route_document(
        _article(
            "政策背景、影响范围和实施路径分析。" * 80,
            title="开展零碳算力设施建设",
            tags=("政策解读",),
        )
    )

    assert route.document_type == "commentary"
    assert route.reason == "explicit_industry_research"


def test_router_requires_bounded_industry_insight_title() -> None:
    insight = route_document(
        _article("产业分析。" * 120, title="行业洞察｜人形机器人商业化拐点")
    )
    company = route_document(
        _article("公司完成融资。" * 120, title="洞察科技完成新一轮融资")
    )

    assert insight.document_type == "commentary"
    assert insight.reason == "explicit_industry_research"
    assert company.document_type == "single_company_flash"


def test_financing_feature_precedes_future_action_density() -> None:
    route = route_document(
        _article(
            "甲辰科技完成融资。资金将用于建设平台。公司计划拓展海外市场。"
            "产品将在年底发布。" * 40,
            title="对话甲辰科技：完成新一轮融资后的全球化计划",
        )
    )

    assert route.document_type == "long_feature"
    assert route.reason == "transaction_or_company_feature_title"


def test_router_detects_patent_study_title() -> None:
    route = route_document(
        _article("专利申请趋势与申请人分布。" * 90, title="半导体产业专利导航系列02")
    )

    assert route.document_type == "long_feature"
    assert route.reason == "patent_study_title"


def test_router_detects_editorial_reprint_without_lowering_global_length() -> None:
    route = route_document(
        _article(
            "编者按：本文来自微信公众号" + "行业调查与供需变化。" * 180,
            title="小龙虾，迎来最冷的夏天",
        )
    )

    assert route.document_type == "long_feature"
    assert route.reason == "editorial_reprint"


def test_router_detects_three_section_long_form_but_excludes_notices() -> None:
    body = (
        "导语。" * 80
        + "\n一、技术路线\n"
        + "技术分析。" * 100
        + "\n二、产业化进程\n"
        + "产业分析。" * 100
        + "\n三、未来应用\n"
        + "应用分析。" * 100
    )

    feature = route_document(_article(body, title="聚变能产业化路径分析"))
    notice = route_document(_article(body, title="产业论坛参会通知"))

    assert feature.document_type == "long_feature"
    assert feature.reason == "sectioned_long_form"
    assert notice.reason != "sectioned_long_form"
