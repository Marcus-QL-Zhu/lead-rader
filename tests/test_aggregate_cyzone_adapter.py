from datetime import datetime, timezone
import json
import sqlite3

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    DetailFetchError,
    ListingInvariantError,
)
from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SourceArticleIndex,
)
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.sites.cyzone import CyzoneAdapter


NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)
ADAPTER = CyzoneAdapter()
FINANCING = ADAPTER.channel_for("cyzone-financing")
LATEST = ADAPTER.channel_for("cyzone-latest")


def _context(tmp_path):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
    )


def _latest_item(
    article_id: str,
    title: str,
    *,
    summary: str = "完整列表摘要，不按标题关键词过滤。",
    tags: tuple[str, ...] = ("创业公司", "融资", "快鲤鱼"),
    date: str = "07-29",
) -> str:
    tag_html = "".join(f'<a href="/label/{tag}" rel="tag">{tag}</a>' for tag in tags)
    return f"""
    <div>
      <div class="article-item" data-id="{article_id}">
        <a class="type-0 pic" href="/article/{article_id}.html"></a>
        <div class="item-intro">
          <a target="_blank" href="/article/{article_id}.html"
             class="item-title">{title}</a>
          <div class="item-desc"><a>{summary}</a></div>
          <div class="item-push-info">
            <div class="tags">{tag_html}</div>
            <div class="time"><span class="time">{date}</span></div>
          </div>
        </div>
      </div>
    </div>
    """


def _latest_listing(*, item_class: str = "article-item", date: str = "07-29") -> bytes:
    items = [
        _latest_item(
            "910001",
            "融资丨星河芯片完成1亿元A轮融资",
            tags=("星河芯片", "融资", "快鲤鱼"),
        ),
        _latest_item(
            "910002",
            "8岁融资200万？AI正在批量制造神童",
            tags=("智能体", "神童", "卖课"),
        ),
    ]
    items.extend(
        _latest_item(
            str(910000 + number),
            f"第{number}篇公开资讯，不含融资关键词",
            tags=("产业观察", "创业邦"),
        )
        for number in range(3, 21)
    )
    html = (
        '<html><body><div id="pane-recommend">'
        + "".join(items)
        + "</div></body></html>"
    )
    if item_class != "article-item":
        html = html.replace('class="article-item"', f'class="{item_class}"')
    if date != "07-29":
        html = html.replace(">07-29<", f">{date}<")
    return html.encode()


def _capital_listing() -> bytes:
    items = []
    for position in range(1, 11):
        article_id = str(800000 + position)
        day = 10 + position
        items.append(
            f"""
            <div class="article-item clearfix">
              <a class="pic-a" href="//www.cyzone.cn/article/{article_id}.html">
                <img src="//oss.cyzone.cn/2025/09{day:02d}/thumb.png">
              </a>
              <div class="item-intro">
                <a href="//www.cyzone.cn/article/{article_id}.html"
                   class="item-title">第{position}篇融资频道公开文章</a>
                <p class="item-desc">融资频道摘要{position}</p>
              </div>
            </div>
            """
        )
    return (
        "<html><body><div class='m-article-list'><div class='list-inner'>"
        + "".join(items)
        + "</div></div></body></html>"
    ).encode()


def _detail(article_id: str, title: str, body: str, *, body_class="g-art-content"):
    return f"""
    <html><body>
      <nav>导航噪声</nav>
      <h1 class="art-title">{title}</h1>
      <div class="art-help"><div class="author-date">
        <a class="author">睿兽分析</a><span>·</span><span>2026-07-29</span>
      </div></div>
      <div class="{body_class}">
        <p>{body}</p>
        <p>资金将用于研发、产品迭代和量产验证，推进技术在真实场景落地。</p>
        <p>查看更多项目信息，请前往「睿兽分析」。</p>
      </div>
      <div class="tag-group"><a>半导体</a><a>融资</a></div>
      <aside>相关推荐噪声</aside><footer>页脚噪声</footer>
      <a href="/article/{article_id}.html">当前文章</a>
    </body></html>
    """.encode()


def _index(article_id: str, title: str, *, company: str = ""):
    return SourceArticleIndex(
        source_id=LATEST.source_id,
        source_article_id=article_id,
        channel="latest",
        canonical_url=f"https://www.cyzone.cn/article/{article_id}.html",
        title=title,
        published_at="2026-07-29",
        discovered_at=NOW.isoformat(),
        cursor_value=f"2026-07-29|{article_id}",
        listing_page=LATEST.url,
        listing_position=1,
        content_hash=f"index-{article_id}",
        discovery_method="exact",
        structured_data={"company": company} if company else {},
    )


def test_cyzone_indexes_every_public_item_in_both_listing_fixtures(tmp_path):
    context = _context(tmp_path)

    latest = ADAPTER.parse_listing(LATEST, _latest_listing(), context)
    financing = ADAPTER.parse_listing(FINANCING, _latest_listing(), context)

    assert len(latest) == 19
    assert latest[0].source_article_id == "910002"
    assert latest[0].title == "8岁融资200万？AI正在批量制造神童"
    assert len(financing) == 1
    assert financing[0].source_article_id == "910001"
    assert financing[0].published_at == "2026-07-29"
    assert financing[0].structured_data["company"] == "星河芯片"
    assert {item.source_id for item in financing} == {"cyzone-financing"}
    assert {
        item.source_article_id for item in latest
    }.isdisjoint(item.source_article_id for item in financing)
    assert len(latest) + len(financing) == 20


def test_cyzone_time_label_does_not_change_content_hash(tmp_path):
    context = _context(tmp_path)

    first = ADAPTER.parse_listing(FINANCING, _latest_listing(date="07-29"), context)
    later = ADAPTER.parse_listing(
        FINANCING,
        _latest_listing(date="2026-07-29"),
        context,
    )

    assert [item.published_at for item in first] == [
        item.published_at for item in later
    ]
    assert [item.content_hash for item in first] == [
        item.content_hash for item in later
    ]
    assert [item.structured_data["time_label"] for item in first] != [
        item.structured_data["time_label"] for item in later
    ]


def test_cyzone_relative_listing_time_uses_china_timezone_and_elapsed_time(tmp_path):
    context = AdapterContext.create(
        state_db=tmp_path / "relative-time.sqlite3",
        fetch=lambda _url: b"",
        now=datetime(2026, 7, 30, 21, 0, tzinfo=timezone.utc),
    )

    assert ADAPTER._parse_listing_time("今天 04:30", context) == "2026-07-31"
    assert ADAPTER._parse_listing_time("10 小时前", context) == "2026-07-30"
    assert ADAPTER._parse_listing_time("30 分钟前", context) == "2026-07-31"
    assert ADAPTER._parse_listing_time("昨天", context) == "2026-07-30"


def test_cyzone_full_listing_and_details_accept_local_today_at_0500(tmp_path):
    local_0500 = datetime(2026, 7, 30, 21, 0, tzinfo=timezone.utc)
    context = AdapterContext.create(
        state_db=tmp_path / "local-today.sqlite3",
        fetch=lambda _url: b"",
        now=local_0500,
    )
    index = ADAPTER.parse_listing(
        FINANCING,
        _latest_listing(date="今天 04:30"),
        context,
    )[0]
    assert index.published_at == "2026-07-31"

    body = (
        "The company completed a financing round and described product research, "
        "manufacturing validation, customer delivery, supply chain preparation, "
        "and organization building in enough detail for deterministic validation."
    )
    html = _detail(index.source_article_id, index.title, body).replace(
        b"2026-07-29", b"2026-07-31"
    )
    html_article = ADAPTER.parse_detail(FINANCING, index, html, context)
    assert html_article.structured_data["detail_published_at"] == "2026-07-31"

    api = json.dumps(
        {
            "data": {
                "content_id": int(index.source_article_id),
                "title": index.title,
                "content": f"<p>{body}</p>",
                "published_at": "2026-07-31 04:30:00",
            }
        },
        ensure_ascii=False,
    ).encode()
    api_article = ADAPTER.parse_detail(FINANCING, index, api, context)
    assert api_article.index.published_at == "2026-07-31"


def test_cyzone_detail_extracts_only_article_body_and_checks_date(tmp_path):
    title = "融资丨星河芯片完成1亿元A轮融资"
    article = ADAPTER.parse_detail(
        LATEST,
        _index("910001", title, company="星河芯片"),
        _detail(
            "910001",
            title,
            (
                "星河芯片近日完成1亿元A轮融资，本轮由远山资本领投。"
                "公司长期专注先进制程芯片研发，已经建立完整研发团队。"
            ),
        ),
        _context(tmp_path),
    )

    assert article.fetch_status == "ok"
    assert article.author == "睿兽分析"
    assert article.tags == ("半导体", "融资")
    assert article.extraction_method == "exact"
    assert "星河芯片近日完成1亿元A轮融资" in article.clean_body
    assert "导航噪声" not in article.clean_body
    assert "相关推荐噪声" not in article.clean_body
    assert "页脚噪声" not in article.clean_body
    assert "查看更多项目信息" not in article.clean_body


def test_cyzone_uses_shared_funding_rules_and_rejects_headline_false_positive():
    positive_index = _index(
        "910001",
        "融资丨星河芯片完成1亿元A轮融资",
        company="星河芯片",
    )
    positive = CleanArticle(
        index=positive_index,
        clean_body=(
            "星河芯片近日完成1亿元A轮融资，本轮融资由远山资本领投，"
            "资金用于先进制程芯片研发。"
        ),
        content_hash="positive-body",
    )
    negative = CleanArticle(
        index=_index("910002", "8岁融资200万？AI正在批量制造神童"),
        clean_body=(
            "报道讨论青少年创业教育里夸张的融资叙事，"
            "文章没有披露任何公司的真实交易事实。"
        ),
        content_hash="negative-body",
    )

    events = ADAPTER.rule_events(LATEST, positive)

    assert len(events) == 1
    assert events[0].canonical_company == "星河芯片"
    assert events[0].funding_round == "A轮"
    assert events[0].funding_amount == "1亿元"
    assert events[0].event_status == "completed"
    assert events[0].processor == "rules:cyzone-v1"
    assert ADAPTER.rule_events(LATEST, negative) == []


def test_cyzone_shared_rules_emit_distinct_events_from_digest():
    index = _index("910099", "创业邦投融资速递")
    article = CleanArticle(
        index=index,
        clean_body=(
            "“甲芯科技”宣布完成数千万元天使轮融资。“乙机器人”近日完成1亿元A轮融资。"
        ),
        content_hash="digest-body",
    )

    events = ADAPTER.rule_events(LATEST, article)

    assert {
        (event.canonical_company, event.funding_round, event.funding_amount)
        for event in events
    } == {
        ("甲芯科技", "天使轮", "数千万元"),
        ("乙机器人", "A轮", "1亿元"),
    }


def test_cyzone_explicit_developer_subject_overrides_editorial_title():
    article = CleanArticle(
        index=_index(
            "841536",
            "估值3500亿，中国模型“全球摇人”",
        ),
        clean_body=(
            "据上海证券报报道，该模型的开发商月之暗面也顺势即将启动"
            "新一轮投前估值达500亿美元（约合3500亿）的融资。"
        ),
        content_hash="841536-body",
    )

    events = ADAPTER.rule_events(LATEST, article)

    assert [
        (
            event.canonical_company,
            event.event_type,
            event.event_status,
        )
        for event in events
    ] == [("月之暗面", "funding", "target")]
    assert events[0].company_mentions == ("月之暗面",)
    assert "全球摇人" not in events[0].company_mentions


def test_841582_related_reading_headline_is_not_a_funding_event():
    article = CleanArticle(
        index=_index(
            "841582",
            "FIFA募资42亿美元背后，体育IP资本化已成常态",
        ),
        clean_body=(
            "国际足联拟成立FFE并向外部投资者出售少数股权。"
            "新西兰橄榄球队曾从银湖资本获得融资。"
            "（延展阅读：估值90亿英镑，CVC打造的“体育版LVMH”启动融资）"
            "所以从模式上讲，这套商业逻辑已经具备可复制性。"
        ),
        content_hash="841582-body",
    )

    events = ADAPTER.rule_events(LATEST, article)

    assert all(event.canonical_company != "体育版LVMH" for event in events)
    assert all("延展阅读" not in quote for event in events for quote in event.evidence_quotes)


def test_cyzone_listing_drift_fails_closed_on_invalid_result(tmp_path):
    context = _context(tmp_path)
    invalid = _latest_listing().replace(
        b"/article/910010.html",
        b"https://evil.example/article/910010.html",
    )
    with pytest.raises(ListingInvariantError):
        ADAPTER.parse_listing(LATEST, invalid, context)


def test_cyzone_detail_body_adaptive_relocation(tmp_path):
    context = _context(tmp_path)
    title = "融资丨星河芯片完成1亿元A轮融资"
    index = _index("910001", title, company="星河芯片")
    ADAPTER.parse_detail(
        LATEST,
        index,
        _detail(
            "910001",
            title,
            (
                "星河芯片近日完成1亿元A轮融资，本轮由远山资本领投。"
                "公司长期专注先进制程芯片研发，已经建立完整研发团队。"
            ),
        ),
        context,
    )

    relocated = ADAPTER.parse_detail(
        LATEST,
        index,
        _detail(
            "910001",
            title,
            (
                "星河芯片近日完成1亿元A轮融资，本轮由远山资本领投。"
                "公司长期专注先进制程芯片研发，已经建立完整研发团队。"
            ),
            body_class="article-rich-text",
        ),
        context,
    )

    assert relocated.extraction_method == "adaptive"
    assert relocated.adaptive_similarity == 72


def test_cyzone_second_run_does_not_refetch_unchanged_details(tmp_path):
    listing = _latest_listing()
    context = _context(tmp_path)
    indexes = ADAPTER.parse_listing(LATEST, listing, context)
    routes = {LATEST.url: listing}
    for index in indexes:
        body = (
            f"{index.structured_data.get('company') or '创业邦'}近日发布公开资讯，"
            "文章完整说明公司业务进展、产品研发和产业背景，"
            "正文长度足以通过专属解析器的不变量检查。"
        )
        routes[index.canonical_url] = _detail(
            index.source_article_id,
            index.title,
            body,
        )
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        return routes[url]

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "coordinator.sqlite3",
        registry=DedicatedAdapterRegistry((CyzoneAdapter(),)),
        fetch=fetch,
        now=NOW,
    )
    first = coordinator.collect_source(LATEST.source_id, "硬科技")
    calls.clear()
    second = coordinator.collect_source(LATEST.source_id, "硬科技")

    assert first.run.listing_count == second.run.listing_count == 19
    assert first.run.incremental_count == 19
    assert second.run.incremental_count == 0
    assert calls == [LATEST.url]
    connection = sqlite3.connect(tmp_path / "coordinator.sqlite3")
    assert (
        connection.execute("SELECT COUNT(*) FROM aggregate_clean_articles").fetchone()[
            0
        ]
        == 19
    )
    connection.close()


def test_cyzone_api_detail_is_primary_and_api_date_is_authoritative(tmp_path):
    title = "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44"
    index = _index("910001", title, company="\u661f\u6cb3\u82af\u7247")
    body = (
        "\u661f\u6cb3\u82af\u7247\u8fd1\u65e5\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u672c\u8f6e\u7531\u8fdc\u5c71\u8d44\u672c\u9886\u6295\u3002"
        "\u8d44\u91d1\u5c06\u7528\u4e8e\u82af\u7247\u7814\u53d1\u3001\u4ea7\u54c1\u8fed\u4ee3\u548c\u91cf\u4ea7\u9a8c\u8bc1\uff0c"
        "\u63a8\u8fdb\u6280\u672f\u5728\u771f\u5b9e\u5de5\u4e1a\u573a\u666f\u7684\u89c4\u6a21\u5316\u843d\u5730\uff0c"
        "\u5e76\u7ee7\u7eed\u5efa\u8bbe\u7814\u53d1\u56e2\u961f\u3001\u4f9b\u5e94\u94fe\u4f53\u7cfb\u548c\u5ba2\u6237\u4ea4\u4ed8\u80fd\u529b\u3002"
    )
    payload = json.dumps(
        {
            "data": {
                "content_id": 910001,
                "title": title,
                "content": f"<p>{body}</p>",
                "published_at": "2026-07-30 09:00:00",
                "author_name": "\u521b\u4e1a\u90a6",
                "tags": "\u534a\u5bfc\u4f53,\u878d\u8d44",
            }
        },
        ensure_ascii=False,
    ).encode()
    context = AdapterContext.create(
        state_db=tmp_path / "api.sqlite3",
        fetch=lambda _url: (_ for _ in ()).throw(AssertionError("HTML fallback used")),
        post_json=lambda _url, request: payload if request["content_id"] == 910001 else b"",
        now=NOW,
    )

    raw = ADAPTER.fetch_detail(LATEST, index, context)
    article = ADAPTER.parse_detail(LATEST, index, raw, context)

    assert article.fetch_status == "structured_complete"
    assert article.extraction_method == "api:app_content/show"
    assert article.index.published_at == "2026-07-30"
    assert article.structured_data["published_at_provenance"] == "api:published_at"
    assert article.structured_data["date_ambiguity"] == "listing=2026-07-29;api=2026-07-30"
    assert body in article.clean_body
    selector_db = tmp_path / "api-adaptive-selectors.sqlite3"
    assert not selector_db.exists()


def test_cyzone_html_missing_date_uses_listing_date_instead_of_dropping_article(tmp_path):
    title = "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44"
    body = (
        "\u661f\u6cb3\u82af\u7247\u8fd1\u65e5\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u672c\u8f6e\u7531\u8fdc\u5c71\u8d44\u672c\u9886\u6295\u3002"
        "\u8d44\u91d1\u5c06\u7528\u4e8e\u82af\u7247\u7814\u53d1\u3001\u4ea7\u54c1\u8fed\u4ee3\u548c\u91cf\u4ea7\u9a8c\u8bc1\uff0c"
        "\u5e76\u63a8\u8fdb\u6280\u672f\u5728\u771f\u5b9e\u5de5\u4e1a\u573a\u666f\u4e2d\u7684\u89c4\u6a21\u5316\u5e94\u7528\u548c\u4ea4\u4ed8\u3002"
    )
    html = _detail("910001", title, body).replace(b"2026-07-29", b"")

    article = ADAPTER.parse_detail(LATEST, _index("910001", title), html, _context(tmp_path))

    assert article.fetch_status == "ok"
    assert article.index.published_at == "2026-07-29"
    assert article.structured_data["published_at_provenance"] == "listing"


def test_cyzone_coordinator_resolves_prior_detail_dead_letters_via_api(tmp_path):
    from ht_lead_radar.aggregate_adapters.storage import AggregateStateStore

    listing = _latest_listing()
    indexes = ADAPTER.parse_listing(LATEST, listing, _context(tmp_path))
    titles = {item.source_article_id: item.title for item in indexes}

    class Fetcher:
        def __call__(self, url):
            if url == LATEST.url:
                return listing
            raise AssertionError(f"unexpected HTML detail fallback: {url}")

        def post_json(self, _url, payload):
            article_id = str(payload["content_id"])
            body = (
                f"{titles[article_id]} company update with enough audited article text. "
                "The company described product research, manufacturing validation, "
                "customer delivery, supply-chain preparation, and organization building."
            )
            return json.dumps(
                {
                    "data": {
                        "content_id": int(article_id),
                        "title": titles[article_id],
                        "content": f"<p>{body}</p>",
                        "published_at": next(
                            item.published_at
                            for item in indexes
                            if item.source_article_id == article_id
                        ),
                        "tags": "hardtech",
                    }
                },
                ensure_ascii=False,
            ).encode()

    database = tmp_path / "coordinator-api.sqlite3"
    with AggregateStateStore(database) as store:
        for article_id in ("910002", "910003"):
            store.record_dead_letter(
                source_id=LATEST.source_id,
                source_article_id=article_id,
                canonical_url=f"https://www.cyzone.cn/article/{article_id}.html",
                stage="detail_or_semantic",
                error="legacy detail date missing",
            )
    coordinator = DedicatedAggregateCoordinator(
        state_db=database,
        registry=DedicatedAdapterRegistry((CyzoneAdapter(),)),
        fetch=Fetcher(),
        now=NOW,
    )

    result = coordinator.collect_source(LATEST.source_id, "hardtech", force_reprocess=True)

    assert result.run.status == "ok"
    assert result.run.listing_count == 19
    assert result.run.detail_success_count == 19
    assert result.run.detail_failure_count == 0
    with AggregateStateStore(database) as store:
        assert store.health()["open_dead_letter_count"] == 0


def test_cyzone_api_rejects_wrong_content_id_and_audits_html_fallback(tmp_path):
    title = "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44"
    index = _index("910001", title, company="\u661f\u6cb3\u82af\u7247")
    api = json.dumps(
        {
            "data": {
                "content_id": 999999,
                "title": title,
                "content": "<p>wrong article body with enough content for parsing</p>",
                "published_at": "2026-07-29",
            }
        }
    ).encode()
    html = _detail(
        "910001",
        title,
        "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u672c\u8f6e\u7531\u8fdc\u5c71\u8d44\u672c\u9886\u6295\u3002"
        "\u8d44\u91d1\u5c06\u7528\u4e8e\u7814\u53d1\u3001\u91cf\u4ea7\u548c\u4ea4\u4ed8\u4f53\u7cfb\u5efa\u8bbe\uff0c"
        "\u5e76\u5efa\u8bbe\u4f9b\u5e94\u94fe\u3001\u5ba2\u6237\u670d\u52a1\u548c\u4ea7\u54c1\u9a8c\u8bc1\u80fd\u529b\u3002",
    )
    decisions = []
    context = AdapterContext.create(
        state_db=tmp_path / "api-mismatch.sqlite3",
        fetch=lambda _url: html,
        post_json=lambda _url, _request: api,
        record_decision=lambda label, payload: decisions.append((label, payload)),
        now=NOW,
    )

    raw = ADAPTER.fetch_detail(LATEST, index, context)
    article = ADAPTER.parse_detail(LATEST, index, raw, context)

    assert raw == html
    assert article.fetch_status == "ok"
    decision = article.structured_data["acquisition_decision"]
    assert decision["outcome"] == "html_fallback"
    assert "content_id mismatch" in decision["api_failure"]
    assert decisions[0][0] == "910001"


def test_cyzone_api_rejects_future_published_at(tmp_path):
    title = "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44"
    index = _index("910001", title, company="\u661f\u6cb3\u82af\u7247")
    payload = json.dumps(
        {
            "data": {
                "content_id": 910001,
                "title": title,
                "content": "<p>" + ("complete future article body " * 10) + "</p>",
                "published_at": "2099-01-01",
            }
        }
    ).encode()
    context = AdapterContext.create(
        state_db=tmp_path / "future.sqlite3",
        fetch=lambda _url: b"",
        post_json=lambda _url, _request: payload,
        now=NOW,
    )

    raw = ADAPTER.fetch_detail(LATEST, index, context)
    with pytest.raises(DetailFetchError, match="future dated"):
        ADAPTER.parse_detail(LATEST, index, raw, context)


def test_cyzone_api_timeout_does_not_fallback_to_html(tmp_path):
    title = "星河芯片完成1亿元A轮融资"
    index = _index("910001", title, company="星河芯片")
    decisions = []
    context = AdapterContext.create(
        state_db=tmp_path / "api-timeout.sqlite3",
        fetch=lambda _url: (_ for _ in ()).throw(AssertionError("HTML fallback used")),
        post_json=lambda _url, _request: (_ for _ in ()).throw(
            TimeoutError("API deadline")
        ),
        record_decision=lambda label, payload: decisions.append((label, payload)),
        now=NOW,
    )

    with pytest.raises(DetailFetchError, match="API detail timeout"):
        ADAPTER.fetch_detail(LATEST, index, context)

    assert decisions[-1][1]["outcome"] == "api_timeout"
