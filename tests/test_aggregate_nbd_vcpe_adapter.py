from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.sites.nbd_vcpe import NbdVcpeWeeklyAdapter


NOW = datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc)
ADAPTER = NbdVcpeWeeklyAdapter()
CHANNEL = ADAPTER.channels[0]


def _search_page(*, drifted=False):
    wrapper = "search-shell" if drifted else "search-header"
    form = "query-box" if drifted else "search-box1"
    return f"""<html><body>
      <div class="{wrapper}"><div class="{form}">
        <input class="input-search" />
      </div></div>
      <script>url: "//www.nbd.com.cn/news-search/queryByMatch"</script>
    </body></html>""".encode()


def _result(number, published, *, title=None, url_date=None):
    title = title or (
        "<em class='highlight'>VC</em>/<em class='highlight'>PE</em>"
        "<em class='highlight'>周报</em>｜硬科技公司融资进展"
    )
    path_date = url_date or published
    return {
        "title": title,
        "url": f"http://www.nbd.com.cn/articles/{path_date}/{4500000 + number}.html",
        "editor": "叶峰",
        "author": "姚亚楠",
        "digest": "<p>多家硬科技企业完成融资，产业资本继续加码。</p>",
        "publishTime": published,
    }


def _search_payload(*, results=None, total_hits=40):
    if results is None:
        start = datetime(2026, 7, 27)
        results = [
            _result(number, (start - timedelta(days=7 * number)).date().isoformat())
            for number in range(16)
        ]
    return json.dumps(
        {
            "code": 200,
            "msg": "Request success",
            "data": {
                "totalHits": total_hits,
                "searchResults": results,
            },
        },
        ensure_ascii=False,
    ).encode()


def _context(
    tmp_path,
    *,
    payload=None,
    full_visible_window=False,
    decisions=None,
):
    calls = []

    def post_json(url, request):
        calls.append((url, request))
        return payload or _search_payload()

    context = AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        post_json=post_json,
        now=NOW,
        decision_state={
            "capture_full_visible_window": {"enabled": full_visible_window}
        },
        record_decision=(
            (lambda key, value: decisions.append((key, value)))
            if decisions is not None
            else None
        ),
    )
    return context, calls


def _indexes(tmp_path):
    context, _calls = _context(tmp_path)
    return ADAPTER.parse_listing(CHANNEL, _search_page(), context)


def _detail(index, *, title=None, published=None, heading_count=5, drifted=False):
    top_class = "g-article-top"
    body_class = "article-copy" if drifted else "g-articl-text"
    paragraphs = [
        "<p style='color:#6d6d6d'>每经记者｜姚亚楠 每经编辑｜叶峰</p>",
        "<p>上周创投市场募投两端均现大额交易，硬科技项目融资持续活跃。</p>",
        "<p>投资端覆盖半导体、具身智能、先进制造与未来能源等赛道。</p>",
    ]
    if heading_count < 4:
        paragraphs.append(
            "<p>"
            + "本期还梳理了产业资本结构、技术路线、产能建设与客户验证进展。"
            * 12
            + "</p>"
        )
    for number in range(1, heading_count + 1):
        heading = f"第{number}家硬科技企业完成融资"
        if number == 2:
            paragraphs.append(f"<h2>{heading}</h2>")
        elif number == 3:
            paragraphs.append(
                f"<p><span style='font-size:20px'><strong>{heading}</strong></span></p>"
            )
        else:
            paragraphs.append(f"<p><strong>{heading}</strong></p>")
        paragraphs.append(
            "<p>近日，该企业宣布完成数亿元新一轮融资，由产业基金领投，"
            "多家市场化机构跟投，资金将用于核心技术研发、产线建设和市场拓展。</p>"
        )
        paragraphs.append(
            "<p><strong>点评：</strong>本轮融资说明资本继续关注拥有明确技术壁垒、"
            "量产路径与客户验证能力的团队，后续交付和组织建设值得观察。</p>"
        )
        paragraphs.append(
            "<p>公司成立以来持续投入研发，已经形成产品、供应链、制造与商业化"
            "协同能力，并计划扩大研发和交付团队以支撑下一阶段增长。</p>"
        )
    return f"""<html><body>
      <div class="{top_class}">
        <h1>{title or index.title}</h1>
        <p class="u-time"><span class="time">{published or index.published_at[:10] + ' 19:44:07'}</span></p>
      </div>
      <div id="article-body" class="{body_class}">{''.join(paragraphs)}</div>
      <footer><p>上一篇 下一篇 热文精选</p></footer>
    </body></html>""".encode()


def test_listing_uses_public_search_api_and_closes_35_day_window(tmp_path):
    decisions = []
    context, calls = _context(tmp_path, decisions=decisions)

    indexes = ADAPTER.parse_listing(CHANNEL, _search_page(), context)

    assert len(indexes) == 5
    assert [item.published_at[:10] for item in indexes] == [
        "2026-07-27",
        "2026-07-20",
        "2026-07-13",
        "2026-07-06",
        "2026-06-29",
    ]
    assert all(item.canonical_url.startswith("https://www.nbd.com.cn/") for item in indexes)
    assert all(item.discovery_method == "api-exact" for item in indexes)
    assert all(
        item.structured_data["document_type"] == "multi_company_bulletin"
        for item in indexes
    )
    assert calls == [
        (
            "https://www.nbd.com.cn/news-search/queryByMatch",
            {
                "keyword": "VC PE 周报",
                "from": 0,
                "size": 16,
                "includeAd": True,
                "platform": [0, 1],
            },
        )
    ]
    assert decisions[0][1]["visible_result_count"] == 16
    assert decisions[0][1]["accepted_roundup_count"] == 5


def test_full_visible_window_keeps_all_exact_roundup_results(tmp_path):
    context, _calls = _context(tmp_path, full_visible_window=True)

    indexes = ADAPTER.parse_listing(CHANNEL, _search_page(), context)

    assert len(indexes) == 16
    assert indexes[-1].published_at[:10] == "2026-04-13"


def test_scrapling_relocates_search_dom_but_business_rules_still_apply(tmp_path):
    context, _calls = _context(tmp_path)
    exact = ADAPTER.parse_listing(CHANNEL, _search_page(), context)
    relocated = ADAPTER.parse_listing(CHANNEL, _search_page(drifted=True), context)

    assert exact
    assert relocated
    assert all(item.discovery_method == "api+adaptive" for item in relocated)

    bad = _search_payload(
        results=[
            _result(1, "2026-07-27"),
            _result(2, "2026-07-20", url_date="2026-07-19"),
            *[
                _result(number, (datetime(2026, 7, 13) - timedelta(days=7 * number)).date().isoformat())
                for number in range(3, 17)
            ],
        ]
    )
    bad_context, _ = _context(tmp_path, payload=bad)
    with pytest.raises(ListingInvariantError, match="URL/date mismatch"):
        ADAPTER.parse_listing(CHANNEL, _search_page(), bad_context)


def test_listing_fails_closed_on_missing_transport_count_or_access_control(tmp_path):
    no_post = AdapterContext.create(
        state_db=tmp_path / "none.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
    )
    with pytest.raises(ListingInvariantError, match="POST transport"):
        ADAPTER.parse_listing(CHANNEL, _search_page(), no_post)

    short = _search_payload(results=[_result(1, "2026-07-27")], total_hits=40)
    short_context, _ = _context(tmp_path, payload=short)
    with pytest.raises(ListingInvariantError, match="returned 1 of expected 16"):
        ADAPTER.parse_listing(CHANNEL, _search_page(), short_context)

    context, _ = _context(tmp_path)
    with pytest.raises(ListingInvariantError, match="access control"):
        ADAPTER.parse_listing(CHANNEL, b"<title>Just a moment</title>", context)


def test_detail_preserves_explicit_item_boundaries_and_routes_as_bulletin(tmp_path):
    index = _indexes(tmp_path)[0]
    context, _calls = _context(tmp_path)

    article = ADAPTER.parse_detail(CHANNEL, index, _detail(index), context)
    boundaries = article.structured_data["item_boundaries"]
    route = route_document(article)

    assert len(boundaries) == 5
    assert article.structured_data["item_headings"] == [
        f"第{number}家硬科技企业完成融资" for number in range(1, 6)
    ]
    assert article.structured_data["article_block_count"] == 22
    assert "上一篇" not in article.clean_body
    assert "每经记者" not in article.clean_body
    assert route.document_type == "multi_company_bulletin"
    assert route.reason == "adapter_document_type"
    assert "".join(unit.text for unit in route.units) == article.clean_body
    assert len(route.units) == 6  # intro plus five explicit items
    for number, boundary in enumerate(boundaries, start=1):
        item = article.clean_body[boundary["char_start"] : boundary["char_end"]]
        assert item.startswith(f"第{number}家硬科技企业完成融资")
        assert "宣布完成数亿元新一轮融资" in item


def test_detail_dom_relocation_revalidates_title_date_and_boundaries(tmp_path):
    index = _indexes(tmp_path)[0]
    context, _calls = _context(tmp_path)
    exact = ADAPTER.parse_detail(CHANNEL, index, _detail(index), context)
    relocated = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(index, drifted=True),
        context,
    )

    assert exact.extraction_method == "exact"
    assert relocated.extraction_method == "adaptive"
    assert relocated.content_hash == exact.content_hash

    with pytest.raises(DetailFetchError, match="title mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, title="完全无关的新闻标题"),
            context,
        )
    with pytest.raises(DetailFetchError, match="URL/date mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, published="2026-07-20 19:44:07"),
            context,
        )
    with pytest.raises(DetailFetchError, match="only 3 explicit"):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail(index, heading_count=3),
            context,
        )
    with pytest.raises(DetailFetchError, match="access control"):
        ADAPTER.parse_detail(CHANNEL, index, b"403 Forbidden", context)


def test_rules_reuse_funding_extraction_without_changing_source_boundaries(tmp_path):
    index = _indexes(tmp_path)[0]
    context, _calls = _context(tmp_path)
    article = ADAPTER.parse_detail(CHANNEL, index, _detail(index), context)
    explicit = replace(
        article,
        clean_body=(
            "星河机器人宣布完成2亿元A轮融资，本轮由红杉中国领投，"
            "中科创星跟投，资金用于建设新产线。"
        ),
        structured_data={"document_type": "multi_company_bulletin"},
        content_hash="explicit",
    )

    events = ADAPTER.rule_events(CHANNEL, explicit)

    assert any(event.event_type == "funding" for event in events)
    assert all(event.canonical_url == index.canonical_url for event in events)
