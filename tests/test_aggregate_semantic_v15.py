import json

from ht_lead_radar.aggregate_adapters.industry_rules import IndustryRuleConfig, extract_industry_events
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor, PROMPT_VERSION

CHANNEL = SourceChannel(
    source_id="v15-test",
    name="v15-test",
    url="https://example.com",
    source_grade="B",
    event_prior=("technical_milestone", "procurement_tender"),
    allowed_hosts=("example.com",),
)

class _Runner:
    def __init__(self, payload):
        self.response = json.dumps(payload, ensure_ascii=False)

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return self.response

def _article(title: str, body: str) -> CleanArticle:
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id="1",
        channel="latest",
        canonical_url="https://example.com/1",
        title=title,
        published_at="2026-07-29",
        discovered_at="2026-07-29T00:00:00+00:00",
        cursor_value="1",
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
    )
    return CleanArticle(index=index, clean_body=body, content_hash="article")

def _event(company: str, event_type: str, status: str, quote: str, summary: str):
    return {
        "events": [{
            "company": company,
            "event_type": event_type,
            "industry_tags": ["artificial_intelligence"],
            "funding_round": "",
            "funding_amount": "",
            "cumulative_funding_amount": "",
            "investors": [],
            "event_status": status,
            "event_summary": summary,
            "evidence_quotes": [quote],
            "confidence": "high",
        }],
        "ambiguities": [],
    }

def test_prompt_version_v15():
    assert PROMPT_VERSION == "aggregate-semantic-v22"

def test_model_summary_with_prior_year_rejects_historical_case():
    quote = "\u6676\u6cf0\u79d1\u6280\u9009\u5b9a\u6280\u672f\u65b9\u5e76\u5b8c\u62109\u8f6e\u9ad8\u7cbe\u5ea6\u5b9e\u9a8c\u64cd\u4f5c\u3002"
    article = _article(
        "2026\u5168\u7403\u5f00\u53d1\u8005\u5927\u4f1a\u5f81\u96c6\u542f\u52a8",
        f"2025\u5168\u7403\u5f00\u53d1\u8005\u5927\u4f1a\u8d5b\u9879\u51a0\u519b\u3002{quote}",
    )
    payload = _event(
        "\u6676\u6cf0\u79d1\u6280",
        "customer_validation",
        "completed",
        quote,
        "\u6676\u6cf0\u79d1\u6280\u57282025\u5168\u7403\u5f00\u53d1\u8005\u5927\u4f1a\u5b8c\u6210\u5b9e\u666f\u9a8c\u8bc1",
    )
    assert MiniMaxSemanticProcessor(_Runner(payload)).process(CHANNEL, article, []) == []

def test_explicit_release_normalizes_started_to_completed():
    quote = "7\u670828\u65e5\uff0cUnity\u4e2d\u56fd\u53d1\u5e03\u56e2\u7ed3\u5f15\u64ce2.0\uff0c\u5e76\u63a8\u51fa\u56e2\u7ed3Codely\u3002"
    article = _article("Unity\u4e2d\u56fd\u53d1\u5e03\u56e2\u7ed3\u5f15\u64ce2.0", quote)
    payload = _event(
        "Unity\u4e2d\u56fd",
        "technical_milestone",
        "started",
        quote,
        "Unity\u4e2d\u56fd\u53d1\u5e03\u56e2\u7ed3\u5f15\u64ce2.0",
    )
    seed = SemanticEvent(
        source_id=CHANNEL.source_id,
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=("Unity中国",),
        canonical_company="Unity中国",
        event_type="technical_milestone",
        event_date="2026-07-29",
        industry_tags=(),
        event_summary=quote,
        evidence_quotes=(quote,),
        processor="rules:test",
        content_hash="article",
        event_status="completed",
    )
    events = MiniMaxSemanticProcessor(_Runner(payload)).process(
        CHANNEL,
        article,
        [seed],
    )
    assert len(events) == 1
    assert events[0].event_status == "completed"

def test_gdps_scene_solicitation_becomes_started_tender_seed():
    quote = "\u73b0\u5728\u626b\u7801\uff0cGDPS\u573a\u666f\u5f81\u96c6\uff0c\u6709\u573a\u666f\u75db\u70b9\u5c31\u6765\u62a5\u540d\u3002"
    article = _article("GDPS\u5168\u7403\u573a\u666f\u5f81\u96c6\u542f\u52a8", quote)
    events = extract_industry_events(
        CHANNEL,
        article,
        config=IndustryRuleConfig(processor="rules:test"),
    )
    assert [
        (event.canonical_company, event.event_type, event.event_status)
        for event in events
    ] == [("GDPS", "procurement_tender", "started")]
