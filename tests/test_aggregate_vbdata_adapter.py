from datetime import datetime, timezone
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
from ht_lead_radar.aggregate_adapters.sites.vbdata import VbdataAdapter


NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)
ADAPTER = VbdataAdapter()
CHANNEL = ADAPTER.channel_for("vbdata-funding")
TITLES = (
    "【首发】汉禾生物完成数千万元战略融资",
    "行业报告完成融资成本调研",
    "对话格式塔彭雷：别只用医疗器械定义脑机接口",
    "医疗器械创新进入临床验证新阶段",
    "本土生物材料产业加快商业化",
    "医院数字化建设关注数据治理",
    "创新药研发迎来关键临床读出",
    "脑科学基础研究取得新进展",
    "医疗机器人加速进入真实场景",
    "生命科学工具企业拓展海外市场",
)


def _context(tmp_path):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
    )


def _card(article_id, title, *, date="2026-07-14"):
    return f"""
    <li><div class="card"><div class="article"><div class="article-content">
      <div class="spc_img">
        <a href="https://www.vbdata.cn/{article_id}"><img src="/x.png"></a>
        <span class="icon_column">创业公司</span>
      </div>
      <div class="spc_cnt">
        <a href="https://www.vbdata.cn/{article_id}"
           class="h1 over-p2">{title}</a>
        <h2 class="over-p1">公开摘要，不按融资关键词预过滤。</h2>
        <div class="bot_cnt">
          <div><div class="tags1"><span>相关赛道</span><a>生物制药</a></div></div>
          <div class="auth_time">
            <img src="/author.png"><span class="author">动脉网</span>
            <span>{date}</span>
          </div>
        </div>
      </div>
    </div></div></div></li>
    """


def _listing(*, list_class="special"):
    cards = [
        _card(str(1519000000 + position), title)
        for position, title in enumerate(TITLES, start=1)
    ]
    return (
        f"<html><body><ul class='{list_class}'>{''.join(cards)}</ul></body></html>"
    ).encode()


def _detail(
    title,
    body,
    *,
    company="",
    body_class="content",
    date="2026-07-14 08:00",
):
    entity = (
        f'<div class="entity-link"><h1 class="product-name">{company}</h1></div>'
        if company
        else ""
    )
    return f"""
    <html><body>
      <nav>首页 情报 原创 导航噪声</nav>
      <div class="lelt-container">
        <h1>{title}</h1>
        <div class="intel-source"><div class="card-info">
          <span class="spa1">李汶芸</span><span class="spa2">{date}</span>
        </div></div>
        {entity}
        <div class="{body_class}">
          <p>{body}</p>
          <p>文章继续介绍技术研发、产业化进展与团队背景，正文信息完整可追溯。</p>
        </div>
      </div>
      <aside>相关推荐噪声：另一家公司完成融资</aside>
      <footer>页脚噪声与联系方式</footer>
    </body></html>
    """.encode()


def _index(article_id, title, *, company=""):
    return SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id=article_id,
        channel="investment-tag",
        canonical_url=f"https://www.vbdata.cn/{article_id}",
        title=title,
        published_at="2026-07-14",
        discovered_at=NOW.isoformat(),
        cursor_value=f"2026-07-14|{article_id}",
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash=f"index-{article_id}",
        discovery_method="exact",
        structured_data={"company": company} if company else {},
    )


def test_vbdata_indexes_all_cards_without_keyword_filter(tmp_path):
    articles = ADAPTER.parse_listing(CHANNEL, _listing(), _context(tmp_path))

    assert len(articles) == 10
    assert [item.title for item in articles] == list(TITLES)
    assert [item.listing_position for item in articles] == list(range(1, 11))
    assert articles[0].canonical_url == "https://www.vbdata.cn/1519000001"
    assert articles[0].structured_data["company"] == "汉禾生物"
    assert articles[1].structured_data["company"] == ""
    assert {item.discovery_method for item in articles} == {"exact"}


def test_vbdata_embedded_state_supplies_exact_relative_date(tmp_path):
    html = (
        _listing()
        .decode()
        .replace(
            "<span>2026-07-14</span>",
            "<span>1 天前</span>",
            1,
        )
    )
    html += """
    <script>window.__NUXT__=(function(a,b){return {
      articleList:[{id:1519000001,publishTime:b}],total:1
    }}(0,"2026-07-14 08:00:00"));</script>
    """

    articles = ADAPTER.parse_listing(
        CHANNEL,
        html.encode(),
        _context(tmp_path),
    )

    assert articles[0].published_at == "2026-07-14"


def test_vbdata_detail_is_clean_and_checks_title_date(tmp_path):
    title = TITLES[0]
    body = (
        "上海汉禾生物新材料科技有限公司（以下简称“汉禾生物”）"
        "宣布完成数千万元战略融资。本轮资金将用于生物材料研发与产业化，"
        "并持续推进核心生产工艺验证和规模化制造平台建设。"
    )
    article = ADAPTER.parse_detail(
        CHANNEL,
        _index("1519000001", title, company="汉禾生物"),
        _detail(title, body, company="汉禾生物"),
        _context(tmp_path),
    )

    assert article.fetch_status == "ok"
    assert article.author == "李汶芸"
    assert article.structured_data["company"] == ("上海汉禾生物新材料科技有限公司")
    assert article.structured_data["company_mentions"] == (
        "上海汉禾生物新材料科技有限公司",
        "汉禾生物",
    )
    assert "宣布完成数千万元战略融资" in article.clean_body
    assert "导航噪声" not in article.clean_body
    assert "相关推荐噪声" not in article.clean_body
    assert "页脚噪声" not in article.clean_body


def test_vbdata_positive_false_positive_and_roundup():
    positive = CleanArticle(
        index=_index("1519000001", TITLES[0], company="汉禾生物"),
        clean_body=(
            "上海汉禾生物新材料科技有限公司（以下简称“汉禾生物”）"
            "宣布完成数千万元战略融资，本轮资金将用于生物材料产业化。"
        ),
        structured_data={
            "company": "上海汉禾生物新材料科技有限公司",
            "company_mentions": (
                "上海汉禾生物新材料科技有限公司",
                "汉禾生物",
            ),
        },
        content_hash="positive-body",
    )
    negative = CleanArticle(
        index=_index("1519000002", TITLES[1]),
        clean_body=(
            "行业报告完成融资成本调研，内容讨论银行贷款利率，"
            "没有披露任何公司的股权融资交易。"
        ),
        content_hash="negative-body",
    )
    roundup = CleanArticle(
        index=_index("1519000099", "医疗投融资周报"),
        clean_body=(
            "“甲芯医疗”宣布完成数千万元天使轮融资。“乙机器人”近日完成1亿元A轮融资。"
        ),
        content_hash="roundup-body",
    )

    events = ADAPTER.rule_events(CHANNEL, positive)
    assert len(events) == 1
    assert events[0].canonical_company == ("上海汉禾生物新材料科技有限公司")
    assert events[0].funding_round == "战略融资"
    assert events[0].funding_amount == "数千万元"
    assert events[0].processor == "rules:vbdata-v1"
    assert ADAPTER.rule_events(CHANNEL, negative) == []
    assert {
        (event.canonical_company, event.funding_round, event.funding_amount)
        for event in ADAPTER.rule_events(CHANNEL, roundup)
    } == {
        ("甲芯医疗", "天使轮", "数千万元"),
        ("乙机器人", "A轮", "1亿元"),
    }


def test_vbdata_adaptive_recovery_and_fail_closed(tmp_path):
    context = _context(tmp_path)
    original = ADAPTER.parse_listing(CHANNEL, _listing(), context)
    relocated = ADAPTER.parse_listing(
        CHANNEL,
        _listing(list_class="story-list"),
        context,
    )

    assert len(original) == len(relocated) == 10
    assert {item.discovery_method for item in relocated} == {"adaptive"}
    invalid = _listing().replace(
        b"https://www.vbdata.cn/1519000005",
        b"https://evil.example/1519000005",
    )
    with pytest.raises(ListingInvariantError):
        ADAPTER.parse_listing(CHANNEL, invalid, context)

    title = TITLES[0]
    index = _index("1519000001", title, company="汉禾生物")
    body = (
        "汉禾生物宣布完成数千万元战略融资，资金将用于生物材料技术研发、"
        "生产工艺验证与产业化平台建设，持续提升规模化制造能力。"
    )
    ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(title, body, company="汉禾生物"),
        context,
    )
    moved = ADAPTER.parse_detail(
        CHANNEL,
        index,
        _detail(title, body, company="汉禾生物", body_class="article-content"),
        context,
    )
    assert moved.extraction_method == "adaptive"
    assert moved.adaptive_similarity == 72
    with pytest.raises(DetailFetchError):
        ADAPTER.parse_detail(
            CHANNEL,
            index,
            _detail("完全无关的详情标题", body, company="汉禾生物"),
            context,
        )


def test_vbdata_second_run_skips_unchanged_details(tmp_path):
    listing = _listing()
    parsed = ADAPTER.parse_listing(CHANNEL, listing, _context(tmp_path))
    routes = {CHANNEL.url: listing}
    for item in parsed:
        body = (
            "本文完整介绍医疗健康产业的技术研发、临床验证、产品迭代与"
            "商业化进展，所有内容均来自公开文章并足以通过正文长度检查。"
        )
        routes[item.canonical_url] = _detail(item.title, body)
    calls = []

    def fetch(url):
        calls.append(url)
        return routes[url]

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "coordinator.sqlite3",
        registry=DedicatedAdapterRegistry((VbdataAdapter(),)),
        fetch=fetch,
        now=NOW,
    )
    first = coordinator.collect_source(CHANNEL.source_id, "医疗硬科技")
    calls.clear()
    second = coordinator.collect_source(CHANNEL.source_id, "医疗硬科技")

    assert first.run.incremental_count == 10
    assert second.run.incremental_count == 0
    assert calls == [CHANNEL.url]
    connection = sqlite3.connect(tmp_path / "coordinator.sqlite3")
    assert (
        connection.execute("SELECT COUNT(*) FROM aggregate_clean_articles").fetchone()[
            0
        ]
        == 10
    )
    connection.close()
