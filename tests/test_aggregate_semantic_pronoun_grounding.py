import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


class _Runner:
    def __init__(self, payload):
        self.response = json.dumps(payload, ensure_ascii=False)

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return self.response


def _article(body: str) -> tuple[SourceChannel, CleanArticle]:
    channel = SourceChannel(
        source_id="test",
        name="test",
        url="https://example.com",
        source_grade="B",
        event_prior=("technical_milestone",),
        allowed_hosts=("example.com",),
    )
    index = SourceArticleIndex(
        source_id="test",
        source_article_id="1",
        channel="latest",
        canonical_url="https://example.com/1",
        title="\u4ea7\u54c1\u8fdb\u5c55",
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


def _payload(company: str, quote: str) -> dict:
    return {
        "events": [{
            "company": company,
            "event_type": "technical_milestone",
            "industry_tags": ["embodied_intelligence"],
            "funding_round": "",
            "funding_amount": "",
            "cumulative_funding_amount": "",
            "investors": [],
            "event_status": "target",
            "event_summary": quote,
            "evidence_quotes": [quote],
            "confidence": "medium",
        }],
        "ambiguities": [],
    }


def _seed(
    article: CleanArticle,
    company: str,
    quote: str,
    *,
    evidence: str = "",
) -> SemanticEvent:
    evidence = evidence or (
        "\u636e\u6089\uff0c\u539f\u529b\u7075\u673a\u5728\u63a5\u4e0b\u6765\u7684\u51e0\u4e2a\u6708\u5185"
        "\u8fd8\u5c06\u5bc6\u96c6\u53d1\u5e03\u65b0\u4ea7\u54c1\uff1a"
        f"{quote}"
    )
    return SemanticEvent(
        source_id="test",
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=tuple(
            item
            for item in (company, "\u539f\u529b\u7075\u673a")
            if item in evidence
        ),
        canonical_company=company,
        event_type="technical_milestone",
        event_date="2026-07-29",
        industry_tags=("embodied_intelligence",),
        event_summary=evidence,
        evidence_quotes=(evidence,),
        processor="rules:test",
        content_hash="article",
        event_status="target",
    )


def test_pronominal_quote_uses_explicit_same_sentence_antecedent():
    company = "\u539f\u529b\u7075\u673a\uff08\u91cd\u5e86\uff09\u667a\u80fd\u79d1\u6280\u6709\u9650\u516c\u53f8"
    quote = "\u5176\u4e0b\u4e00\u4ee3\u5927\u6a21\u578b\u3001\u9996\u6b3e\u901a\u7528\u673a\u5668\u4eba\u4ee5\u53ca\u5168\u65b0\u7684\u5e94\u7528\u57fa\u7840\u8bbe\u65bd\u4e5f\u5373\u5c06\u53d1\u5e03"
    body = (
        f"{company}\u5b8c\u6210\u6218\u7565\u878d\u8d44\u3002"
        "\u636e\u6089\uff0c\u539f\u529b\u7075\u673a\u5728\u63a5\u4e0b\u6765\u7684\u51e0\u4e2a\u6708\u5185\u8fd8\u5c06\u5bc6\u96c6\u53d1\u5e03\u65b0\u4ea7\u54c1\uff1a"
        f"{quote}\u3002"
    )
    channel, article = _article(body)

    events = MiniMaxSemanticProcessor(_Runner(_payload(company, quote))).process(
        channel,
        article,
        [_seed(article, company, quote)],
    )

    assert len(events) == 1
    assert events[0].canonical_company == company
    assert events[0].evidence_quotes[0].startswith("\u636e\u6089\uff0c\u539f\u529b\u7075\u673a")
    assert events[0].evidence_quotes[0].endswith("\u5373\u5c06\u53d1\u5e03")


def test_pronominal_quote_does_not_cross_a_sentence_boundary():
    company = "\u7532\u516c\u53f8"
    quote = "\u5176\u4e0b\u4e00\u4ee3\u5927\u6a21\u578b\u5373\u5c06\u53d1\u5e03"
    seed_quote = "\u7532\u516c\u53f8\u7684\u4e0b\u4e00\u4ee3\u5927\u6a21\u578b\u5373\u5c06\u53d1\u5e03"
    channel, article = _article(
        f"{seed_quote}\u3002"
        f"\u4e59\u516c\u53f8\u8868\u793a\uff0c{quote}\u3002"
    )

    events = MiniMaxSemanticProcessor(_Runner(_payload(company, quote))).process(
        channel,
        article,
        [_seed(article, company, quote, evidence=seed_quote)],
    )

    assert len(events) == 1
    assert events[0].processor == "rules:test"
    assert events[0].evidence_quotes == (seed_quote,)