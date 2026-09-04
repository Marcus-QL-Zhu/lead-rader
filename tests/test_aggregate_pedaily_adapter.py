from datetime import datetime, timedelta, timezone
import re

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
from ht_lead_radar.aggregate_adapters.sites.pedaily import PedailyAdapter


NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)
VCPE = PedailyAdapter.channels[0]
INDUSTRY = PedailyAdapter.channels[1]
TITLES = (
    "武汉超导完成近亿元种子轮融资，加速超导装备产业化",
    "华辰芯光完成新一轮超亿元融资，同创伟业领投",
    "创新药械投融资（南京）专场对接活动在宁成功举办",
    "耐心资本护航创新，创投价值共生新时代",
    "毅达资本完成对苏州利亚得智能装备有限公司的独家投资",
    "新鼎资本牵手新能源飞机项目",
    "行业报告完成融资成本调研",
    "启明创投主管合伙人荣膺年度创投人",
    "投资浮盈两百倍，联讯仪器科创板成功上市",
    "一村资本与宜兴共同设立10亿元AI基金",
)


def _context(tmp_path, *, url_map=None):
    routes = url_map or {}
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda url: routes[url],
        now=NOW,
    )


def _listing(
    titles=TITLES,
    *,
    month="202607",
    first_id=566976,
    item_class="h-news lazyload",
    list_id="newslist-all",
    same_time=False,
):
    items = []
    for position, title in enumerate(titles):
        article_id = str(first_id - position)
        age_hours = 1 if same_time else position + 1
        published = (
            NOW.astimezone(timezone(timedelta(hours=8))) - timedelta(hours=age_hours)
        ).strftime("%Y-%m-%d %H:%M")
        items.append(
            f"""
            <li class="{item_class}" data-special="16"
                data-restypeid="2" data-indid="2842">
              <div class="image"><a href="https://news.pedaily.cn/{month}/{article_id}.shtml">
                <img alt="{title}">
              </a></div>
              <div class="txt">
                <h3><a href="https://news.pedaily.cn/{month}/{article_id}.shtml">{title}</a></h3>
                <div class="info">
                  <span class="author">投资界综合</span>
                  <span class="date">{published}</span>
                </div>
              </div>
            </li>
            """
        )
    return (
        f"<html><body><ul class='masonry-list' id='{list_id}'>"
        f"{''.join(items)}</ul></body></html>"
    ).encode()


def test_pedaily_listing_rank_drift_does_not_change_article_hash(tmp_path):
    first_html = _listing(same_time=True).decode()
    items = re.findall(r"<li\b.*?</li>", first_html, flags=re.DOTALL)
    assert len(items) == len(TITLES)
    second_html = (
        "<html><body><ul class='masonry-list' id='newslist-all'>"
        + "".join(reversed(items))
        + "</ul></body></html>"
    ).encode()

    adapter = PedailyAdapter()
    first = adapter.parse_listing(VCPE, first_html.encode(), _context(tmp_path))
    second = adapter.parse_listing(VCPE, second_html, _context(tmp_path))
    first_hashes = {item.source_article_id: item.content_hash for item in first}
    second_hashes = {item.source_article_id: item.content_hash for item in second}

    assert first_hashes == second_hashes
    assert first[0].source_article_id == second[-1].source_article_id


def _detail(title, body=None, *, author="投资界综合"):
    content = body or (
        f"{title}。该报道完整介绍了相关主体的公开进展和背景信息，"
        "并说明后续资金将用于研发、团队建设和产业化，所有事实均来自公开消息。"
        "这是用于验证专属正文选择器的第二个正文段落。"
    )
    return f"""
    <html><body>
      <nav>首页 资讯 投资圈</nav>
      <article id="final-content" class="final-content news-show">
        <header class="newsinfo">
          <h1 id="newstitle">{title}</h1>
          <div class="subject">公开摘要</div>
          <div class="info">
            <time datetime="2026-07-30T11:00:00+08:00">2026-07-30 11:00</time>
            <span id="newsauthor">{author}</span>
          </div>
        </header>
        <div id="article-body">
          <div id="news-content">
            <p>{content}</p>
            <p class="orginstr">文章来源：投资界 原文地址：https://example.invalid</p>
            <div class="originatips">本文不构成投资建议。</div>
          </div>
        </div>
      </article>
      <section class="related-news">相关推荐：另一家公司完成融资</section>
      <footer>版权与联系方式</footer>
    </body></html>
    """.encode()


def test_pedaily_channels_index_every_public_list_item_without_keyword_filter(
    tmp_path,
):
    adapter = PedailyAdapter()
    for channel, month, first_id in (
        (VCPE, "202607", 566976),
        (INDUSTRY, "202606", 565168),
    ):
        articles = adapter.parse_listing(
            channel,
            _listing(month=month, first_id=first_id),
            _context(tmp_path),
        )

        assert len(articles) == len(TITLES)
        assert [item.title for item in articles] == list(TITLES)
        assert [item.listing_position for item in articles] == list(range(1, 11))
        assert articles[0].source_id == channel.source_id
        assert articles[0].source_article_id == str(first_id)
        assert articles[0].canonical_url == (
            f"https://news.pedaily.cn/{month}/{first_id}.shtml"
        )
        assert articles[-1].title == "一村资本与宜兴共同设立10亿元AI基金"
        assert {item.discovery_method for item in articles} == {"exact"}


def test_pedaily_detail_extracts_body_without_navigation_recommendations_or_footer(
    tmp_path,
):
    adapter = PedailyAdapter()
    index = adapter.parse_listing(
        VCPE,
        _listing(),
        _context(tmp_path),
    )[0]
    body = (
        "武汉超导智能装备科技有限公司（以下简称“武汉超导”）"
        "宣布完成近1亿元种子轮融资，由武汉经开产投联合出资。"
        "资金将用于超导装备技术研发、产业化平台建设及高端人才引进。"
    )

    article = adapter.parse_detail(
        VCPE,
        index,
        _detail(index.title, body, author="关注你关注的"),
        _context(tmp_path),
    )

    assert article.fetch_status == "ok"
    assert article.extraction_method == "exact"
    assert "武汉超导智能装备科技有限公司" in article.clean_body
    assert "首页 资讯 投资圈" not in article.clean_body
    assert "相关推荐" not in article.clean_body
    assert "文章来源" not in article.clean_body
    assert "不构成投资建议" not in article.clean_body
    assert "版权与联系方式" not in article.clean_body
    assert article.author == "投资界综合"
    assert article.structured_data["company"] == ("武汉超导智能装备科技有限公司")


def test_pedaily_uses_shared_funding_rules_and_rejects_false_positive(tmp_path):
    adapter = PedailyAdapter()
    positive_index = adapter.parse_listing(
        VCPE,
        _listing(),
        _context(tmp_path),
    )[0]
    body = (
        "武汉超导智能装备科技有限公司（以下简称“武汉超导”）"
        "宣布完成近1亿元种子轮融资，由武汉经开产投联合出资。"
        "资金将用于超导装备技术研发和产业化平台建设，"
        "并持续推进关键装备测试平台和人才团队建设。"
    )
    positive = adapter.parse_detail(
        VCPE,
        positive_index,
        _detail(positive_index.title, body),
        _context(tmp_path),
    )

    events = adapter.rule_events(VCPE, positive)

    assert len(events) == 1
    assert events[0].canonical_company == ("武汉超导智能装备科技有限公司")
    assert events[0].company_mentions == (
        "武汉超导智能装备科技有限公司",
        "武汉超导",
    )
    assert events[0].funding_round == "种子轮"
    assert events[0].funding_amount == "近1亿元"
    assert events[0].event_status == "completed"
    assert events[0].processor == "rules:pedaily-v2"
    assert events[0].evidence_quotes[0] in positive.clean_body

    negative = _clean_article(
        "negative",
        "行业报告完成融资成本调研",
        (
            "行业报告完成融资成本调研，报告测算的是企业贷款成本，"
            "并未宣布任何公司完成股权融资。"
        ),
    )
    assert adapter.rule_events(INDUSTRY, negative) == []


def test_pedaily_seeds_explicit_merger_and_future_product_milestone():
    base = _clean_article(
        "565150",
        "「原力灵机」完成与Atomix机器人战略合并，获新一轮战略融资",
        (
            "近日，原力灵机（重庆）智能科技有限公司「原力灵机」"
            "宣布完成与Atomix机器人的战略合并，并同步完成新一轮融资。"
            "据悉，原力灵机在接下来的几个月内还将密集发布新产品："
            "其下一代大模型、首款通用机器人以及全新的应用基础设施"
            "也即将发布。"
        ),
    )
    article = CleanArticle(
        index=base.index,
        clean_body=base.clean_body,
        structured_data={
            "company": "原力灵机（重庆）智能科技有限公司",
            "company_mentions": (
                "原力灵机（重庆）智能科技有限公司",
                "原力灵机",
            ),
        },
        content_hash=base.content_hash,
    )

    events = PedailyAdapter().rule_events(VCPE, article)

    assert {
        (
            event.event_type,
            event.event_status,
            event.canonical_company,
        )
        for event in events
    } == {
        (
            "funding",
            "completed",
            "原力灵机（重庆）智能科技有限公司",
        ),
        (
            "merger_acquisition",
            "completed",
            "原力灵机（重庆）智能科技有限公司",
        ),
        (
            "technical_milestone",
            "target",
            "原力灵机（重庆）智能科技有限公司",
        ),
    }
    assert all(
        event.evidence_quotes[0] in article.clean_body
        for event in events
        if event.event_type != "funding"
    )


def test_pedaily_adaptive_listing_and_detail_relocation_are_controlled(
    tmp_path,
):
    adapter = PedailyAdapter()
    context = _context(tmp_path)
    original = _listing()
    changed = _listing(
        item_class="story-card lazyload",
        list_id="latest-stories",
    )
    seeded = adapter.parse_listing(VCPE, original, context)
    relocated = adapter.parse_listing(VCPE, changed, context)

    assert len(seeded) == len(relocated) == 10
    assert {item.discovery_method for item in relocated} == {"adaptive"}

    index = seeded[0]
    exact_detail = _detail(index.title)
    moved_detail = exact_detail.replace(
        b'<h1 id="newstitle">',
        b'<h1 id="article-title" class="headline">',
    ).replace(
        b'<div id="news-content">',
        b'<div id="story-content" class="news-content">',
    )
    adapter.parse_detail(VCPE, index, exact_detail, context)
    article = adapter.parse_detail(VCPE, index, moved_detail, context)

    assert article.extraction_method == "adaptive"
    assert article.adaptive_similarity == 72


def test_pedaily_adaptive_drift_fails_closed_when_invariants_do_not_hold(
    tmp_path,
):
    adapter = PedailyAdapter()
    context = _context(tmp_path)
    adapter.parse_listing(VCPE, _listing(), context)

    with pytest.raises(ListingInvariantError):
        adapter.parse_listing(
            VCPE,
            b"<html><body><ul><li>navigation only</li></ul></body></html>",
            context,
        )

    index = adapter.parse_listing(VCPE, _listing(), context)[0]
    with pytest.raises(DetailFetchError):
        adapter.parse_detail(
            VCPE,
            index,
            b"<html><body><h1>unrelated page</h1><p>short</p></body></html>",
            context,
        )


def test_pedaily_second_run_does_not_refetch_unchanged_details(tmp_path):
    adapter = PedailyAdapter()
    listing = _listing()
    parsed = adapter.parse_listing(VCPE, listing, _context(tmp_path))
    routes = {VCPE.url: listing}
    routes.update({item.canonical_url: _detail(item.title) for item in parsed})
    calls = []

    def fetch(url):
        calls.append(url)
        return routes[url]

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "coordinator.sqlite3",
        registry=DedicatedAdapterRegistry((PedailyAdapter(),)),
        fetch=fetch,
        now=NOW,
    )
    first = coordinator.collect_source(VCPE.source_id, "硬科技")
    calls.clear()
    second = coordinator.collect_source(VCPE.source_id, "硬科技")

    assert first.run.incremental_count == 10
    assert second.run.incremental_count == 0
    assert calls == [VCPE.url]
    assert len(first.evidence) == len(second.evidence)


def _clean_article(article_id, title, body):
    index = SourceArticleIndex(
        source_id=INDUSTRY.source_id,
        source_article_id=article_id,
        channel="investment-news",
        canonical_url=(f"https://news.pedaily.cn/202606/{article_id}.shtml"),
        title=title,
        published_at="2026-06-12T15:59:00+08:00",
        discovered_at=NOW.isoformat(),
        cursor_value=article_id,
        listing_page=INDUSTRY.url,
        listing_position=1,
        content_hash=f"index-{article_id}",
        discovery_method="exact",
    )
    return CleanArticle(
        index=index,
        clean_body=body,
        content_hash=f"body-{article_id}",
    )
