import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


class _RepeatRunner:
    def __init__(self, payload):
        self.response = json.dumps(payload, ensure_ascii=False)

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return self.response


def _context(title: str, body: str):
    channel = SourceChannel(
        source_id="test",
        name="test",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )
    index = SourceArticleIndex(
        source_id="test",
        source_article_id="1",
        channel="latest",
        canonical_url="https://example.com/1",
        title=title,
        published_at="2026-07-29",
        discovered_at="2026-07-29T00:00:00+00:00",
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


def _seed(article, company, quote, *, amount="", round_name="A轮"):
    return SemanticEvent(
        source_id="test",
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=(company,),
        canonical_company=company,
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=(),
        funding_round=round_name,
        funding_amount=amount,
        evidence_quotes=(quote,),
        content_hash="article",
    )


def test_rules_only_drops_explicitly_historical_funding():
    company = "丘大叔"
    quote = "企查查显示，2021年，丘大叔先后完成天使轮和Pre-A轮融资，总额超亿元；"
    channel, article = _context("现制茶饮行业观察", quote)

    events = MiniMaxSemanticProcessor(None).process(
        channel,
        article,
        [_seed(article, company, quote, amount="超亿元", round_name="Pre-A轮")],
    )

    assert events == []


def test_rules_only_drops_subject_inherited_from_unrelated_article_title():
    quote = "月之暗面即将启动新一轮融资。"
    channel, article = _context("全球摇人：AI人才战", quote)

    events = MiniMaxSemanticProcessor(None).process(
        channel,
        article,
        [_seed(article, "全球摇人", quote, round_name="")],
    )

    assert events == []


def test_model_event_requires_subject_in_primary_quote():
    company = "甲公司"
    body = "甲公司是行业参与者。乙公司完成A轮融资。"
    channel, article = _context("行业融资盘点", body)
    payload = {
        "events": [
            {
                "company": company,
                "event_type": "funding",
                "industry_tags": [],
                "funding_round": "A轮",
                "funding_amount": "",
                "cumulative_funding_amount": "",
                "investors": [],
                "event_status": "completed",
                "event_summary": "乙公司完成A轮融资。",
                "evidence_quotes": ["乙公司完成A轮融资。"],
                "confidence": "high",
            }
        ],
        "ambiguities": [],
    }
    processor = MiniMaxSemanticProcessor(_RepeatRunner(payload))

    events = processor.process(channel, article, [])

    assert events == []
    assert processor.last_audit["status"] == "fallback_to_rules"


def test_historical_date_flows_through_followup_financing_sentence():
    company = "DeepSeek"
    first = "2026年3月，DeepSeek完成首轮外部融资。"
    second = "在首轮融资后，DeepSeek又迅速启动了第二轮融资。"
    channel, article = _context("DeepSeek融资回顾", f"{first}{second}")
    base = {
        "company": company,
        "event_type": "funding",
        "industry_tags": ["artificial_intelligence"],
        "funding_round": "",
        "funding_amount": "",
        "cumulative_funding_amount": "",
        "investors": [],
        "event_summary": "",
        "confidence": "high",
    }
    payload = {
        "events": [
            {
                **base,
                "event_status": "completed",
                "event_summary": first,
                "evidence_quotes": [first],
            },
            {
                **base,
                "event_status": "started",
                "event_summary": second,
                "evidence_quotes": [second],
            },
        ],
        "ambiguities": [],
    }

    events = MiniMaxSemanticProcessor(_RepeatRunner(payload)).process(
        channel,
        article,
        [],
    )

    assert events == []
