import json

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


def test_kr36_digest_emits_every_current_funding_event():
    article = _article(
        "digest",
        "8点1氪｜今日投融资",
        (
            "月之暗面 Kimi 已完成F轮融资，融资金额超35亿美元，"
            "投后估值涨至350亿美元。因超目标金额3倍多，本轮融资系"
            "提前关闭，原定8月开始的G轮（Pre IPO轮）已提前开始。"
            "投融资：“智谷天厨”官宣完成新一轮近亿元战略融资。"
        ),
    )

    events = Kr36Adapter().rule_events(CHANNEL, article)

    assert {
        (event.canonical_company, event.funding_round, event.event_status)
        for event in events
    } == {
        ("月之暗面 Kimi", "F轮", "completed"),
        ("月之暗面 Kimi", "G轮", "started"),
        ("智谷天厨", "战略融资", "completed"),
    }
    moonshot = next(
        event for event in events if event.funding_round == "F轮"
    )
    assert moonshot.funding_amount == "超35亿美元"


def test_kr36_seed_completion_and_new_angel_start_are_distinct():
    article = _article(
        "wuhan",
        "武汉超导完成近亿元种子轮融资，启动天使轮",
        (
            "武汉超导宣布完成近亿元的种子轮融资。"
            "在种子轮融资顺利落地的同时，武汉超导正式宣布开启"
            "新一轮面向产业资本、战略投资人的天使轮融资。"
        ),
        company="武汉超导",
    )

    events = Kr36Adapter().rule_events(CHANNEL, article)

    assert {
        (event.funding_round, event.event_status) for event in events
    } == {("种子轮", "completed"), ("天使轮", "started")}


def test_kr36_cumulative_amount_is_not_current_round_amount():
    article = _article(
        "ciyuan",
        "“词元无限”完成天使++轮融资，累计融资金额达数亿元",
        (
            "“词元无限”宣布完成天使++轮融资。本轮融资由临芯投资"
            "领投，这也是一个月内完成的第二笔融资，累计融资额已达"
            "数亿元人民币。"
        ),
        company="词元无限",
    )

    events = Kr36Adapter().rule_events(CHANNEL, article)

    assert len(events) == 1
    assert events[0].funding_round == "天使++轮"
    assert events[0].funding_amount == ""


def test_kr36_prefers_the_funding_subject_over_a_quoted_metaphor():
    article = _article(
        "minen",
        "自研SNN类脑芯片、做医疗设备的“上游大脑”，「米能科技」获数千万元融资",
        "近期，前沿生理类脑芯片企业米能科技完成数千万元股权融资。",
    )

    events = Kr36Adapter().rule_events(CHANNEL, article)

    assert {event.canonical_company for event in events} == {"米能科技"}


def test_minimax_keeps_rule_omissions_and_filters_unnamed_investors():
    article = _article(
        "kando",
        "Kando AI完成数千万元种子轮融资",
        (
            "Kando AI已完成数千万元种子轮融资，星连资本领投，"
            "力合金控跟投，知名产业方参与投资。"
        ),
        company="Kando AI",
    )
    seed = Kr36Adapter().rule_events(CHANNEL, article)
    response = json.dumps(
        {
            "events": [
                {
                    "company": "Kando AI",
                    "event_type": "funding",
                    "industry_tags": ["artificial_intelligence"],
                    "funding_round": "种子轮",
                    "funding_amount": "数千万元",
                    "cumulative_funding_amount": "",
                    "investors": ["星连资本", "力合金控", "知名产业方"],
                    "event_status": "completed",
                    "event_summary": "完成种子轮融资",
                    "evidence_quotes": [article.clean_body],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )

    events = MiniMaxSemanticProcessor(Runner(response)).process(
        CHANNEL,
        article,
        seed,
    )

    assert len(events) == 1
    assert events[0].investors == ("星连资本", "力合金控")
    assert events[0].ambiguities == (
        "unnamed_investor_omitted:知名产业方",
    )
