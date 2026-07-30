import json


from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import (
    MiniMaxSemanticProcessor,
)


class Runner:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def run(self, prompt, *, session_id, system_prompt=""):
        self.calls.append((prompt, session_id, system_prompt))
        return next(self.responses)


def _channel():
    return SourceChannel(
        source_id="test",
        name="测试",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )


def _article():
    index = SourceArticleIndex(
        source_id="test",
        source_article_id="1",
        channel="funding",
        canonical_url="https://example.com/1",
        title="星河芯片完成1亿元A轮融资",
        published_at="2026-07-29",
        discovered_at="2026-07-29T00:00:00+00:00",
        cursor_value="1",
        listing_page="https://example.com",
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
    )
    return CleanArticle(
        index=index,
        clean_body="星河芯片完成1亿元A轮融资，远山资本领投。",
        content_hash="body",
    )


def _seed():
    return [
        SemanticEvent(
            source_id="test",
            source_article_id="1",
            canonical_url="https://example.com/1",
            company_mentions=("星河芯片",),
            canonical_company="星河芯片",
            event_type="funding",
            event_date="2026-07-29",
            industry_tags=("semiconductor",),
            funding_round="A轮",
            funding_amount="1亿元",
            evidence_quotes=("星河芯片完成1亿元A轮融资，远山资本领投。",),
            content_hash="body",
        )
    ]


def test_minimax_semantic_output_is_grounded_and_normalized():
    response = json.dumps(
        {
            "events": [
                {
                    "company": "星河芯片",
                    "event_type": "funding",
                    "industry_tags": ["semiconductor"],
                    "funding_round": "A轮",
                    "funding_amount": "1亿元",
                    "investors": ["远山资本"],
                    "event_summary": "完成A轮融资",
                    "evidence_quotes": [
                        "星河芯片完成1亿元A轮融资，远山资本领投。"
                    ],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )
    processor = MiniMaxSemanticProcessor(Runner([response]))

    events = processor.process(_channel(), _article(), _seed())

    assert len(events) == 1
    assert events[0].canonical_company == "星河芯片"
    assert events[0].investors == ("远山资本",)
    assert events[0].processor == "minimax"
    assert events[0].event_date == "2026-07-29"


def test_minimax_semantic_rejects_invented_subject_after_one_repair():
    invented = json.dumps(
        {
            "events": [
                {
                    "company": "不存在公司",
                    "event_type": "funding",
                    "industry_tags": ["semiconductor"],
                    "funding_round": "",
                    "funding_amount": "",
                    "investors": [],
                    "event_summary": "",
                    "evidence_quotes": ["星河芯片完成1亿元A轮融资，远山资本领投。"],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )
    processor = MiniMaxSemanticProcessor(Runner([invented, invented]))

    events = processor.process(_channel(), _article(), _seed())

    assert len(events) == 1
    assert events[0].canonical_company == _seed()[0].canonical_company
    assert events[0].processor == "rules"
    assert events[0].ambiguities == (
        "minimax_validation_failed:SemanticOutputError",
    )
    assert processor.last_audit["status"] == "fallback_to_rules"
