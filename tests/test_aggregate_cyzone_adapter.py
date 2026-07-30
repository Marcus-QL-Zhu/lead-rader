from datetime import datetime, timezone
import sqlite3

import pytest

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
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


def _latest_listing(*, item_class: str = "article-item") -> bytes:
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
    financing = ADAPTER.parse_listing(FINANCING, _capital_listing(), context)

    assert len(latest) == 20
    assert [item.listing_position for item in latest] == list(range(1, 21))
    assert latest[0].source_article_id == "910001"
    assert latest[0].published_at == "2026-07-29"
    assert latest[0].structured_data["company"] == "星河芯片"
    assert latest[1].title == "8岁融资200万？AI正在批量制造神童"
    assert len(financing) == 10
    assert financing[0].published_at == "2025-09-11"
    assert financing[-1].published_at == "2025-09-20"
    assert {item.source_id for item in financing} == {"cyzone-financing"}


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
    ] == [("月之暗面", "funding", "started")]
    assert events[0].company_mentions == ("月之暗面",)
    assert "全球摇人" not in events[0].company_mentions


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

    assert first.run.listing_count == second.run.listing_count == 20
    assert first.run.incremental_count == 20
    assert second.run.incremental_count == 0
    assert calls == [LATEST.url]
    connection = sqlite3.connect(tmp_path / "coordinator.sqlite3")
    assert (
        connection.execute("SELECT COUNT(*) FROM aggregate_clean_articles").fetchone()[
            0
        ]
        == 20
    )
    connection.close()
