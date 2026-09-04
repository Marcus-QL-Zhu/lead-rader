from datetime import datetime, timezone

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.suzhou_robot_association import (
    SuzhouRobotAssociationAdapter,
)


NOW = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
ADAPTER = SuzhouRobotAssociationAdapter()
CHANNEL = ADAPTER.channels[0]
INDUSTRY = "\u884c\u4e1a\u8d44\u8baf"
TRENDS = "\u534f\u4f1a\u52a8\u6001"
ACTIVITY = "\u534f\u4f1a\u6d3b\u52a8"
POLICY = "\u653f\u7b56\u4fe1\u606f"
FACTORY_TITLE = "\u7075\u7334\u673a\u5668\u4eba\u5609\u5174\u57fa\u5730\u6b63\u5f0f\u6295\u4ea7"


def _context(tmp_path):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3", fetch=lambda _url: b"", now=NOW
    )


def _news_card(token, title, date):
    return f'''<li><a href="https://mp.weixin.qq.com/s/{token}">
        <h4>{title}</h4><span class="time">{date}</span>
      </a></li>'''


def _policy_card(path, title, date):
    return f'''<li><a href="{path}"><div class="tit f16">{title}</div>
      <span class="time">{date}</span></a></li>'''


def _listing(*, item_class="item"):
    industry = "".join((
        _news_card("industry_token_0001", "\u673a\u5668\u4eba\u884c\u4e1a\u70ed\u70b9\u8d44\u8baf\u6c47\u603b", "2026/06/03"),
        _news_card("industry_token_0002", "\u4eba\u5f62\u673a\u5668\u4eba\u4ea7\u4e1a\u6807\u51c6\u8fdb\u5c55", "2026/06/02"),
    ))
    trends = "".join((
        _news_card("trends_token_00001", FACTORY_TITLE, "2026/06/03"),
        _news_card("trends_token_00002", "\u4e50\u805a\u673a\u5668\u4eba\u4e0e\u4eac\u4e1c\u4ea7\u53d1\u7b7e\u7f72\u6218\u7565\u5408\u4f5c\u534f\u8bae", "2026/05/22"),
    ))
    policies = "".join((
        _policy_card("/policy/32.html", "\u673a\u5668\u4eba\u4ea7\u4e1a\u521b\u65b0\u53d1\u5c55\u884c\u52a8\u8ba1\u5212", "2026/05/20"),
        _policy_card("/localPolicy/31.html", "\u82cf\u5dde\u5177\u8eab\u667a\u80fd\u673a\u5668\u4eba\u652f\u6301\u63aa\u65bd", "2026/05/19"),
    ))
    return f'''<html><body>
      <div class="i-news"><div class="tabs">
        <a>{INDUSTRY}</a><a>{TRENDS}</a><a>{ACTIVITY}</a>
      </div></div>
      <div class="i-newsCon">
        <div class="{item_class}"><ul>{industry}</ul></div>
        <div class="{item_class}"><ul>{trends}</ul></div>
        <div class="{item_class}"><ul></ul></div>
      </div>
      <div class="i-plylist"><ul>{policies}</ul></div>
    </body></html>'''.encode()


def _wechat_detail(title, *, date="2026-06-03", body_class="rich_media_content", body_tag="div"):
    body = (
        "\u7075\u7334\u673a\u5668\u4eba\u5609\u5174\u57fa\u5730\u6b63\u5f0f\u6295\u4ea7\uff0c"
        "\u9879\u76ee\u56f4\u7ed5\u5de5\u4e1a\u5177\u8eab\u667a\u80fd\u673a\u5668\u4eba\u5f00\u5c55\u89c4\u6a21\u5316\u5236\u9020\u3002"
        "\u516c\u53f8\u5df2\u7ecf\u5b8c\u6210\u751f\u4ea7\u7ebf\u8c03\u8bd5\u4e0e\u5ba2\u6237\u9a8c\u8bc1\uff0c"
        "\u5e76\u5c06\u6301\u7eed\u63d0\u5347\u673a\u5668\u4eba\u6838\u5fc3\u96f6\u90e8\u4ef6\u7684\u4ea4\u4ed8\u80fd\u529b\u3002"
        "\u672c\u6b21\u6295\u4ea7\u5c06\u652f\u6301\u9762\u5411\u5148\u8fdb\u5236\u9020\u5ba2\u6237\u7684\u4ea7\u54c1\u8fed\u4ee3\u548c\u5e94\u7528\u843d\u5730\u3002"
    )
    return f'''<html><body><main>
      <h1 id="activity-name">{title}</h1>
      <span id="publish_time">{date}</span><a id="js_name">\u82cf\u5dde\u673a\u5668\u4eba\u534f\u4f1a</a>
      <{body_tag} id="js_content" class="{body_class}"><p>{body}</p><p>\u63a8\u8350\u9605\u8bfb</p></{body_tag}>
    </main><aside>navigation noise</aside></body></html>'''.encode()


def _native_detail(
    title,
    *,
    date="2026-05-20",
    body_class="news-article",
    body=None,
):
    body = body or (
        "\u8be5\u884c\u52a8\u8ba1\u5212\u660e\u786e\u652f\u6301\u5177\u8eab\u667a\u80fd\u673a\u5668\u4eba\u5173\u952e\u6280\u672f\u653b\u5173\u3001"
        "\u5e94\u7528\u573a\u666f\u5efa\u8bbe\u4e0e\u6807\u51c6\u4f53\u7cfb\u5b8c\u5584\u3002\u76f8\u5173\u90e8\u95e8\u5c06\u7ec4\u7ec7\u4ea7\u4e1a\u534f\u540c\uff0c"
        "\u63a8\u8fdb\u673a\u5668\u4eba\u4ea7\u54c1\u6d4b\u8bd5\u9a8c\u8bc1\u3001\u89c4\u6a21\u5316\u5e94\u7528\u548c\u91cd\u70b9\u4f01\u4e1a\u80fd\u529b\u63d0\u5347\u3002"
        "\u6587\u4ef6\u540c\u65f6\u63d0\u51fa\u52a0\u5f3a\u4eba\u624d\u57f9\u517b\u548c\u521b\u65b0\u5e73\u53f0\u5efa\u8bbe\u3002"
    )
    return f'''<html><body><nav>navigation</nav>
      <div class="content content-news">
        <div class="newstit">{title}</div>
        <div class="newstm"><span>\u53d1\u5e03\u65f6\u95f4\uff1a{date}</span></div>
        <div class="{body_class}"><p>{body}</p></div>
        <div class="page_2">previous next</div>
      </div><footer>footer</footer>
    </body></html>'''.encode()


def test_homepage_indexes_every_visible_news_and_policy_card(tmp_path):
    indexes = ADAPTER.parse_listing(CHANNEL, _listing(), _context(tmp_path))

    assert len(indexes) == 6
    assert [item.channel for item in indexes] == [
        INDUSTRY, INDUSTRY, TRENDS, TRENDS, POLICY, POLICY
    ]
    assert [item.listing_position for item in indexes] == list(range(1, 7))
    assert indexes[2].canonical_url.startswith("https://mp.weixin.qq.com/s/")
    assert indexes[4].canonical_url == "https://robotsz.org.cn/policy/32.html"
    assert indexes[4].source_article_id == "policy-32"


def test_date_label_format_drift_does_not_refetch_existing_details(tmp_path):
    listing = _listing()
    drifted = listing
    for source, target in (
        (b"2026/06/03", b"2026-06-03"),
        (b"2026/06/02", b"2026-06-02"),
        (b"2026/05/22", b"2026-05-22"),
        (b"2026/05/20", b"2026-05-20"),
        (b"2026/05/19", b"2026-05-19"),
    ):
        drifted = drifted.replace(source, target)
    initial = ADAPTER.parse_listing(CHANNEL, listing, _context(tmp_path))
    relabeled = ADAPTER.parse_listing(CHANNEL, drifted, _context(tmp_path))
    assert [item.content_hash for item in initial] == [
        item.content_hash for item in relabeled
    ]
    assert [item.structured_data["listing_date_label"] for item in initial] != [
        item.structured_data["listing_date_label"] for item in relabeled
    ]

    network = {CHANNEL.url: listing}
    for item in initial:
        if item.canonical_url.startswith("https://mp.weixin.qq.com/"):
            network[item.canonical_url] = _wechat_detail(
                item.title,
                date=item.published_at[:10],
            )
        else:
            network[item.canonical_url] = _native_detail(
                item.title,
                date=item.published_at[:10],
            )
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
    first = coordinator.collect_source(CHANNEL.source_id, "具身智能")
    network[CHANNEL.url] = drifted
    calls.clear()
    second = coordinator.collect_source(CHANNEL.source_id, "具身智能")

    assert first.run.incremental_count == len(initial)
    assert second.run.incremental_count == 0
    assert calls == [CHANNEL.url]


def test_details_extract_article_body_and_validate_both_real_detail_shapes(tmp_path):
    context = _context(tmp_path)
    indexes = ADAPTER.parse_listing(CHANNEL, _listing(), context)
    wechat = ADAPTER.parse_detail(
        CHANNEL, indexes[2], _wechat_detail(indexes[2].title), context
    )
    native = ADAPTER.parse_detail(
        CHANNEL, indexes[4], _native_detail(indexes[4].title), context
    )

    assert "\u751f\u4ea7\u7ebf\u8c03\u8bd5\u4e0e\u5ba2\u6237\u9a8c\u8bc1" in wechat.clean_body
    assert "\u63a8\u8350\u9605\u8bfb" not in wechat.clean_body
    assert "navigation noise" not in wechat.clean_body
    assert "\u5e73\u53f0\u5efa\u8bbe" in native.clean_body
    assert "previous next" not in native.clean_body
    assert native.structured_data["detail_published_at"] == "2026-05-20"


def test_detail_fallback_preserves_only_the_auditable_listing_headline(tmp_path):
    context = _context(tmp_path)
    indexes = ADAPTER.parse_listing(CHANNEL, _listing(), context)

    wechat = ADAPTER.parse_detail(
        CHANNEL,
        indexes[2],
        b"<title>Just a moment</title>",
        context,
    )
    native = ADAPTER.parse_detail(
        CHANNEL,
        indexes[4],
        _native_detail(indexes[4].title, body="image only"),
        context,
    )

    assert wechat.clean_body == indexes[2].title
    assert native.clean_body == indexes[4].title
    assert wechat.fetch_status == native.fetch_status == "listing_complete"
    assert wechat.extraction_method == "listing-headline-fallback"
    assert wechat.failure_reason == "wechat_detail_access_interstitial"
    assert native.failure_reason == "native_detail_contains_no_extractable_text"


def test_rule_events_reuse_shared_industry_rules(tmp_path):
    index = ADAPTER.parse_listing(CHANNEL, _listing(), _context(tmp_path))[2]
    article = ADAPTER.parse_detail(
        CHANNEL, index, _wechat_detail(index.title), _context(tmp_path)
    )
    events = ADAPTER.rule_events(CHANNEL, article)

    assert {event.event_type for event in events} == {"factory_or_capacity", "customer_validation"}
    assert all(event.processor == "rules:suzhou-robot-association-v1" for event in events)


def test_scrapling_relocates_saved_homepage_and_detail_dom(tmp_path):
    context = _context(tmp_path)
    original = ADAPTER.parse_listing(CHANNEL, _listing(), context)
    relocated = ADAPTER.parse_listing(
        CHANNEL, _listing(item_class="latest-item"), context
    )
    assert len(relocated) == len(original)
    assert {item.discovery_method for item in relocated} == {"adaptive"}




def test_fail_closed_on_listing_and_detail_contract_violations(tmp_path):
    context = _context(tmp_path)
    bad_url = _listing().replace(
        b"https://mp.weixin.qq.com/s/industry_token_0001",
        b"https://example.invalid/article",
    )
    with pytest.raises(ListingInvariantError, match="invalid URL"):
        ADAPTER.parse_listing(CHANNEL, bad_url, context)

    indexes = ADAPTER.parse_listing(CHANNEL, _listing(), context)
    with pytest.raises(DetailFetchError, match="title mismatch"):
        ADAPTER.parse_detail(
            CHANNEL, indexes[2], _wechat_detail("different title"), context
        )
    with pytest.raises(DetailFetchError, match="date mismatch"):
        ADAPTER.parse_detail(
            CHANNEL,
            indexes[4],
            _native_detail(indexes[4].title, date="2026-05-19"),
            context,
        )
    short = (
        f'<h1 id="activity-name">{FACTORY_TITLE}</h1>'
        '<span id="publish_time">2026-06-03</span>'
        '<div id="js_content" class="rich_media_content"><p>short body</p></div>'
    ).encode()
    with pytest.raises(DetailFetchError, match="too short"):
        ADAPTER.parse_detail(CHANNEL, indexes[2], short, context)
    with pytest.raises(ListingInvariantError, match="no bypass"):
        ADAPTER.parse_listing(CHANNEL, b"<title>Just a moment</title>", context)
