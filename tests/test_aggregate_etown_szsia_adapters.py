from datetime import datetime, timezone
import pytest
from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.sites.beijing_etown import (
    BeijingEtownMajorProjectsAdapter,
)
from ht_lead_radar.aggregate_adapters.sites.shenzhen_semiconductor import (
    ShenzhenSemiconductorAssociationAdapter,
)

NOW = datetime(2026, 7, 30, 2, tzinfo=timezone.utc)


def ctx(tmp, routes={}, *, full_visible_window=False):
    return AdapterContext.create(
        state_db=tmp / "state.sqlite3",
        fetch=lambda u: routes[u],
        now=NOW,
        decision_state={
            "capture_full_visible_window": {"enabled": full_visible_window}
        },
    )


def etown_list(
    title="\u6df1\u82af\u79d1\u6280\u53d1\u5e03\u9996\u6b3e\u82af\u7247\u5e76\u5b9e\u73b0\u91cf\u4ea7",
    date="2026-07-29",
):
    return f'<html><div class="container clearfix"><ul class="list"><li><a href="./202607/t20260729_4797457.html" title="{title}">{title}</a><span class="date">{date}</span></li></ul></div></html>'.encode()


def etown_detail(
    title="\u6df1\u82af\u79d1\u6280\u53d1\u5e03\u9996\u6b3e\u82af\u7247\u5e76\u5b9e\u73b0\u91cf\u4ea7",
    date="2026-07-29",
):
    body = (
        "\u6df1\u82af\u79d1\u6280\u53d1\u5e03\u9996\u6b3e\u82af\u7247\u5e76\u5b9e\u73b0\u91cf\u4ea7\uff0c\u9879\u76ee\u5df2\u5b8c\u6210\u5b89\u88c5\u3001\u8c03\u8bd5\u548c\u9a8c\u6536\u3002"
        * 5
    )
    return f'<html><head><meta name="SiteIDCode" content="1100000158"><meta name="ColumnName" content="\\u91cd\\u5927\\u9879\\u76ee"><meta name="ArticleTitle" content="{title}"><meta name="PubDate" content="{date}"></head><div class="details_page"><h2></h2><div id="div_zhengwen"><div class="view">{body}</div></div></div></html>'.encode().replace(
        b"\\u91cd\\u5927\\u9879\\u76ee", "\u91cd\u5927\u9879\u76ee".encode()
    )


def sz_list(
    category, title="Shenzhen chip company completed mass production", date="2026-07-29"
):
    return f'<html><div class="mainBody"><div class="wp-block-columns hdlist"><div class="wp-block-column"><div class="block-imgL"><div class="txt"><h2><a href="https://www.szsia.com/?p={4700 + category}">{title}</a></h2><div class="time">{date}</div></div></div></div></div></div></html>'.encode()


def sz_detail(
    title="Shenzhen chip company completed mass production",
    date="2026-07-29",
    body=None,
):
    body = body or (
        "Shenzhen semiconductor association confirms the company completed chip mass production and customer validation. "
        * 4
    )
    return (
        f'<html><footer>\\u6df1\\u5733\\u5e02\\u534a\\u5bfc\\u4f53\\u884c\\u4e1a\\u534f\\u4f1a</footer><div class="mainBody"><div class="wp-block-columns xmfd"><div class="wp-block-column"><h1>{title}</h1><div class="time text-center">\\u6765\\u6e90\\uff1a\\u6df1\\u5733\\u5e02\\u534a\\u5bfc\\u4f53\\u884c\\u4e1a\\u534f\\u4f1a {date}</div><div class="content">{body}</div></div></div></div></html>'.encode()
        .decode("unicode_escape")
        .encode()
    )


def test_etown_exact_adaptive_and_closed(tmp_path):
    a = BeijingEtownMajorProjectsAdapter()
    c = ctx(tmp_path)
    idx = a.parse_listing(a.channels[0], etown_list(), c)[0]
    article = a.parse_detail(a.channels[0], idx, etown_detail(), c)
    assert article.extraction_method == "exact"
    assert a.rule_events(a.channels[0], article)
    moved = a.parse_listing(
        a.channels[0], etown_list().replace(b'ul class="list"', b'ul class="moved"'), c
    )
    assert moved[0].discovery_method == "adaptive"
    with pytest.raises(DetailFetchError, match="title mismatch"):
        a.parse_detail(a.channels[0], idx, etown_detail("wrong title"), c)
    with pytest.raises(ListingInvariantError, match="no bypass"):
        a.parse_listing(a.channels[0], b"403 Forbidden", c)


def test_szsia_exact_adaptive_and_closed(tmp_path):
    a = ShenzhenSemiconductorAssociationAdapter()
    routes = {a._listing_url(k, 1): sz_list(k) for k in (22, 20, 34)}
    c = ctx(tmp_path, routes)
    indexes = a.parse_listing(a.channels[0], sz_list(21), c)
    assert len(indexes) == 4
    article = a.parse_detail(a.channels[0], indexes[0], sz_detail(), c)
    assert article.extraction_method == "exact"
    moved = a.parse_detail(
        a.channels[0],
        indexes[0],
        sz_detail().replace(b'class="content"', b'class="moved-content"'),
        c,
    )
    assert moved.extraction_method == "adaptive"
    assert moved.adaptive_similarity == 72
    fallback = a.parse_detail(
        a.channels[0],
        indexes[0],
        sz_detail(body="image only"),
        c,
    )
    assert fallback.fetch_status == "listing_complete"
    assert fallback.clean_body == indexes[0].title
    with pytest.raises(DetailFetchError, match="date mismatch"):
        a.parse_detail(a.channels[0], indexes[0], sz_detail(date="2026-07-28"), c)
    with pytest.raises(ListingInvariantError, match="no bypass"):
        a.parse_listing(a.channels[0], b"403 Forbidden", c)


def test_full_visible_window_keeps_older_first_page_items(tmp_path):
    etown = BeijingEtownMajorProjectsAdapter()
    newer = etown_list()
    old_item = (
        '<li><a href="./202606/t20260601_4797001.html" '
        'title="Older project milestone">Older project milestone</a>'
        '<span class="date">2026-06-01</span></li>'
    ).encode()
    listing = newer.replace(b"</ul>", old_item + b"</ul>")
    full = ctx(tmp_path / "etown", full_visible_window=True)
    assert len(etown.parse_listing(etown.channels[0], listing, full)) == 2

    szsia = ShenzhenSemiconductorAssociationAdapter()
    routes = {
        szsia._listing_url(category, 1): sz_list(category, date="2026-06-01")
        for category in (22, 20, 34)
    }
    full = ctx(tmp_path / "szsia", routes, full_visible_window=True)
    indexes = szsia.parse_listing(
        szsia.channels[0],
        sz_list(21, date="2026-06-01"),
        full,
    )
    assert len(indexes) == 4
