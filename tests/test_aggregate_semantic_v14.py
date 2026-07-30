import json

import pytest

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import (
    MiniMaxSemanticProcessor,
    PROMPT_VERSION,
)


class _Runner:
    def __init__(self, payload):
        self.payload = payload

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return json.dumps(self.payload, ensure_ascii=False)


def _article(company: str, body: str) -> tuple[SourceChannel, CleanArticle]:
    channel = SourceChannel(
        source_id="test",
        name="test",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding", "technical_milestone"),
        allowed_hosts=("example.com",),
    )
    index = SourceArticleIndex(
        source_id="test",
        source_article_id="1",
        channel="latest",
        canonical_url="https://example.com/1",
        title=f"{company}完成A轮融资",
        published_at="2026-06-12",
        discovered_at="2026-06-12T00:00:00+00:00",
        cursor_value="1",
        listing_page=channel.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
    )
    return channel, CleanArticle(
        index=index,
        clean_body=body,
        content_hash="article",
    )


def _seed(
    article: CleanArticle,
    company: str,
    event_type: str,
    status: str,
    quote: str,
    *,
    round_name: str = "",
) -> SemanticEvent:
    return SemanticEvent(
        source_id="test",
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=(company,),
        canonical_company=company,
        event_type=event_type,
        event_date=article.index.published_at[:10],
        industry_tags=(),
        funding_round=round_name,
        event_summary=quote,
        evidence_quotes=(quote,),
        processor="rules:test",
        content_hash="article",
        event_status=status,
    )


@pytest.mark.parametrize(
    ("reported", "canonical"),
    (
        (
            "聚焦新一代脑机交互神经调控技术的上海空山慧科技有限公司",
            "上海空山慧科技有限公司",
        ),
        (
            "可控核聚变FRC技术路线企业合肥星能玄光科技有限责任公司",
            "合肥星能玄光科技有限责任公司",
        ),
        (
            "跨境电商行业金融科技企业迈豹云数（深圳）科技有限公司",
            "迈豹云数（深圳）科技有限公司",
        ),
        (
            "机器人灵巧手初创企业苏州伯牙智能科技有限公司",
            "苏州伯牙智能科技有限公司",
        ),
    ),
)
def test_descriptive_prose_is_removed_from_legal_company(reported, canonical):
    quote = f"{reported}宣布完成A轮融资。"
    channel, article = _article(reported, quote)
    payload = {
        "events": [
            {
                "company": reported,
                "event_type": "funding",
                "industry_tags": [],
                "funding_round": "A轮",
                "funding_amount": "",
                "cumulative_funding_amount": "",
                "investors": [],
                "event_status": "completed",
                "event_summary": quote,
                "evidence_quotes": [quote],
                "confidence": "high",
            }
        ],
        "ambiguities": [],
    }

    events = MiniMaxSemanticProcessor(_Runner(payload)).process(
        channel,
        article,
        [_seed(article, reported, "funding", "completed", quote, round_name="A杞?")],
    )

    assert events[0].canonical_company == canonical
    assert reported in events[0].company_mentions


def test_named_owned_investment_vehicle_replaces_parent_investor():
    company = "合肥星能玄光科技有限责任公司"
    funding_quote = f"{company}近日完成A系列融资。"
    investor_quote = (
        "本轮新进投资方包括上海国投旗下上海科创集团、合肥产投旗下合肥天使投资基金。"
    )
    channel, article = _article(
        company,
        f"{funding_quote}{investor_quote}",
    )
    payload = {
        "events": [
            {
                "company": company,
                "event_type": "funding",
                "industry_tags": [],
                "funding_round": "A系列",
                "funding_amount": "",
                "cumulative_funding_amount": "",
                "investors": ["上海国投", "合肥产投"],
                "event_status": "completed",
                "event_summary": funding_quote,
                "evidence_quotes": [funding_quote, investor_quote],
                "confidence": "high",
            }
        ],
        "ambiguities": [],
    }

    events = MiniMaxSemanticProcessor(_Runner(payload)).process(
        channel,
        article,
        [
            _seed(
                article,
                company,
                "funding",
                "completed",
                funding_quote,
                round_name=payload["events"][0]["funding_round"],
            )
        ],
    )

    assert events[0].investors == ("上海科创集团", "合肥天使投资基金")


def test_other_events_are_not_persisted_or_duplicated():
    company = "原力灵机（重庆）智能科技有限公司"
    quote = f"{company}的下一代大模型和首款通用机器人即将发布。"
    channel, article = _article(company, quote)
    base = {
        "company": company,
        "industry_tags": ["robotics"],
        "funding_round": "",
        "funding_amount": "",
        "cumulative_funding_amount": "",
        "investors": [],
        "event_status": "target",
        "event_summary": quote,
        "evidence_quotes": [quote],
        "confidence": "medium",
    }
    payload = {
        "events": [
            {**base, "event_type": "technical_milestone"},
            {**base, "event_type": "other"},
        ],
        "ambiguities": [],
    }

    events = MiniMaxSemanticProcessor(_Runner(payload)).process(
        channel,
        article,
        [_seed(article, company, "technical_milestone", "target", quote)],
    )

    assert [event.event_type for event in events] == ["technical_milestone"]
    assert all(event.prompt_version == PROMPT_VERSION for event in events)
    assert PROMPT_VERSION == "aggregate-semantic-v18"


def test_rules_only_path_uses_same_company_normalization_and_drops_other():
    reported = "机器人灵巧手初创企业苏州伯牙智能科技有限公司"
    quote = f"{reported}宣布完成天使轮融资。"
    channel, article = _article(reported, quote)
    base = dict(
        source_id="test",
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=(reported,),
        canonical_company=reported,
        event_date="2026-06-12",
        industry_tags=(),
        evidence_quotes=(quote,),
        content_hash="article",
    )
    seeds = [
        SemanticEvent(
            **base,
            event_type="funding",
            funding_round="天使轮",
        ),
        SemanticEvent(
            **base,
            event_type="other",
        ),
    ]

    events = MiniMaxSemanticProcessor(None).process(channel, article, seeds)

    assert len(events) == 1
    assert events[0].canonical_company == "苏州伯牙智能科技有限公司"
