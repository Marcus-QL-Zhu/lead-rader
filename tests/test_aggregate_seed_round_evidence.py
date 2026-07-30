import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


COMPANY = "\u539f\u529b\u7075\u673a\uff08\u91cd\u5e86\uff09\u667a\u80fd\u79d1\u6280\u6709\u9650\u516c\u53f8"
BRAND = "\u539f\u529b\u7075\u673a"
ROUND = "\u6218\u7565\u878d\u8d44"
TITLE_QUOTE = f"\u300c\u539f\u529b\u7075\u673a\u300d\u83b7\u65b0\u4e00\u8f6e{ROUND}"
BODY_QUOTE = (
    f"{COMPANY}\u5ba3\u5e03\u540c\u6b65\u5b8c\u6210\u65b0\u4e00\u8f6e\u878d\u8d44\u3002"
)


class Runner:
    model_identity = "minimax/MiniMax-M3"

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return json.dumps(
            {
                "events": [
                    {
                        "company": COMPANY,
                        "event_type": "funding",
                        "industry_tags": ["embodied_intelligence"],
                        "funding_round": ROUND,
                        "funding_amount": "",
                        "cumulative_funding_amount": "",
                        "investors": [],
                        "event_status": "completed",
                        "event_summary": BODY_QUOTE,
                        "evidence_quotes": [BODY_QUOTE],
                        "confidence": "high",
                    }
                ],
                "ambiguities": [],
            },
            ensure_ascii=False,
        )


def test_restored_seed_round_also_restores_its_grounding_quote():
    channel = SourceChannel(
        source_id="pedaily-vcpe-events",
        name="pedaily",
        url="https://news.pedaily.cn/",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("news.pedaily.cn",),
        allowed_path_patterns=(r"/\d+\.shtml",),
    )
    index = SourceArticleIndex(
        source_id=channel.source_id,
        source_article_id="565150",
        channel="vcpe",
        canonical_url="https://news.pedaily.cn/202606/565150.shtml",
        title=TITLE_QUOTE,
        published_at="2026-06-12T12:00:00+08:00",
        discovered_at="2026-06-13T00:00:00+08:00",
        cursor_value="565150",
        listing_page="https://news.pedaily.cn/",
        listing_position=1,
        content_hash="index",
        discovery_method="html",
        summary=BODY_QUOTE,
    )
    article = CleanArticle(
        index=index,
        clean_body=BODY_QUOTE,
        content_hash="article",
    )
    seed = SemanticEvent(
        source_id=channel.source_id,
        source_article_id=index.source_article_id,
        canonical_url=index.canonical_url,
        company_mentions=(COMPANY, BRAND),
        canonical_company=COMPANY,
        event_type="funding",
        event_date="2026-06-12",
        industry_tags=("embodied_intelligence",),
        funding_round=ROUND,
        event_summary=TITLE_QUOTE,
        evidence_quotes=(TITLE_QUOTE,),
        confidence="medium",
        processor="rules:test",
        content_hash=article.content_hash,
        phase="build_organize",
        event_status="completed",
    )

    events = MiniMaxSemanticProcessor(Runner()).process(
        channel,
        article,
        [seed],
    )

    assert len(events) == 1
    assert events[0].funding_round == ROUND
    assert BODY_QUOTE in events[0].evidence_quotes
    assert TITLE_QUOTE in events[0].evidence_quotes
    assert any(
        value == "minimax_ungrounded_field_removed:funding_round"
        for value in events[0].ambiguities
    )
