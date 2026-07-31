from datetime import datetime, timezone
import json

from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SourceArticleIndex,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from ht_lead_radar.aggregate_adapters.sites.kr36 import Kr36Adapter


CHANNEL = Kr36Adapter.channels[0]


class Runner:
    def __init__(self, response):
        self.response = response

    def run(self, prompt, *, session_id, system_prompt=""):
        return self.response


def _article(article_id, title, body, *, company=""):
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id=article_id,
        channel="financing-flash",
        canonical_url=f"https://36kr.com/p/{article_id}",
        title=title,
        published_at="2026-07-29",
        discovered_at="2026-07-29T12:00:00+00:00",
        cursor_value=article_id,
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash=f"index-{article_id}",
        discovery_method="exact",
        structured_data={"company": company} if company else {},
    )
    return CleanArticle(
        index=index,
        clean_body=body,
        content_hash=f"body-{article_id}",
    )


def test_invalid_minimax_event_salvages_only_grounded_named_investors():
    article = _article(
        "yaole",
        "“尧乐科技”完成Pre-A+新一轮融资",
        (
            "柔性触觉感知企业“尧乐科技”近日完成Pre-A+新一轮融资，"
            "本轮融资由鼎和高达领投，上市公司常熟汽饰、祖龙娱乐跟投。"
        ),
        company="尧乐科技",
    )
    seed = Kr36Adapter().rule_events(CHANNEL, article)
    invalid = json.dumps(
        {
            "events": [
                {
                    "company": "尧乐科技",
                    "event_type": "funding",
                    "industry_tags": ["embodied_intelligence"],
                    "funding_round": "Pre-A+轮",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [
                        "鼎和高达",
                        "常熟汽饰",
                        "祖龙娱乐",
                        "知名产业方",
                    ],
                    "event_status": "completed",
                    "event_summary": "完成融资",
                    "evidence_quotes": ["这不是原文逐字引文"],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )

    processor = MiniMaxSemanticProcessor(Runner(invalid))
    events = processor.process(CHANNEL, article, seed)

    assert processor.last_audit["status"] == "accepted"
    assert events[0].investors == ("鼎和高达", "常熟汽饰", "祖龙娱乐")
    assert "知名产业方" not in events[0].investors
    assert "minimax_seed_quote_substituted" in events[0].ambiguities


def test_owned_platform_uses_legal_company_as_canonical_entity():
    article = _article(
        "tabtin",
        "“TabTin”完成6000万元天使轮融资",
        (
            "近日，上海摹范科技有限公司旗下的人和Agent协作平台"
            "“TabTin”完成6000万元天使轮融资。"
        ),
        company="TabTin",
    )

    events = Kr36Adapter().rule_events(CHANNEL, article)

    assert len(events) == 1
    assert events[0].canonical_company == "上海摹范科技有限公司"
    assert events[0].company_mentions == (
        "上海摹范科技有限公司",
        "TabTin",
    )


def test_391676_pre_ipo_round_does_not_turn_target_valuation_into_amount():
    article = _article(
        "3916765840043656",
        "月之暗面已完成新一轮35亿美元融资：估值升至350亿美元",
        (
            "大模型公司月之暗面在最新一轮融资中募集了35亿美元。"
            "目前，月之暗面已开始筹划Pre-IPO轮融资，并已开始接触"
            "潜在投资者，目标是以500亿美元的投前估值完成筹资。"
        ),
        company="月之暗面",
    )

    events = Kr36Adapter().rule_events(CHANNEL, article)
    pre_ipo = next(event for event in events if event.event_status == "started")

    assert pre_ipo.funding_round == "Pre-IPO轮"
    assert pre_ipo.funding_amount == ""


def test_adapter_run_finish_time_never_precedes_injected_start_time(tmp_path):
    listing = """
    <div><div class="item-title"><a class="title"
    href="//36kr.com/newsflashes/1001">星河芯片完成A轮融资</a></div>
    <div class="item-desc">星河芯片完成A轮融资</div>
    <div class="project-card-wrp"><div class="right-top">
    <div class="title">星河芯片</div><div class="tag fin-tag">A轮</div>
    </div></div><span class="time">2小时前</span></div>
    """.encode()
    listing *= 5

    class FiveItemAdapter(Kr36Adapter):
        minimum_listing_count = 1

    future = datetime(2030, 1, 1, tzinfo=timezone.utc)
    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "state.sqlite3",
        registry=None,
        fetch=lambda url: listing,
        now=future,
    )
    result = coordinator.collect_source("36kr-financing-flash", "硬科技")

    assert result.run.finished_at >= result.run.started_at
