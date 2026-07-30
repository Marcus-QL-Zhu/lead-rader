import json

import pytest

from ht_lead_radar.aggregate_adapters.entities import is_company_like
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
from ht_lead_radar.aggregate_adapters.sites.cls import ClsAdapter


class _Runner:
    def __init__(self, response=None):
        self.calls = 0
        self.response = response

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        self.calls += 1
        return self.response or json.dumps({"events": [], "ambiguities": []})


def _article() -> tuple[SourceChannel, CleanArticle]:
    channel = SourceChannel(
        source_id="v17-test",
        name="v17-test",
        url="https://example.com",
        source_grade="B",
        event_prior=("technical_milestone",),
        allowed_hosts=("example.com",),
    )
    index = SourceArticleIndex(
        source_id=channel.source_id,
        source_article_id="1",
        channel="latest",
        canonical_url="https://example.com/1",
        title="行业观察",
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
        clean_body="没有确定性公司事件。",
        content_hash="article",
    )


def test_v17_skips_minimax_when_no_rule_seed():
    channel, article = _article()
    runner = _Runner()
    processor = MiniMaxSemanticProcessor(runner)

    assert processor.process(channel, article, []) == []
    assert runner.calls == 0
    assert processor.last_audit["status"] == "no_rule_seed"
    assert PROMPT_VERSION == "aggregate-semantic-v18"


def test_v18_seed_quote_replaces_ungrounded_model_quote_and_field():
    channel, base = _article()
    article = CleanArticle(
        index=base.index,
        clean_body=(
            "【星河芯片】完成亿元首轮融资，远山资本领投。银河机器人发布了产品介绍。"
        ),
        content_hash=base.content_hash,
    )
    seed = SemanticEvent(
        source_id=channel.source_id,
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=("星河芯片",),
        canonical_company="星河芯片",
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=("semiconductor",),
        funding_amount="亿元",
        event_summary="【星河芯片】完成亿元首轮融资，远山资本领投。",
        evidence_quotes=("【星河芯片】完成亿元首轮融资，远山资本领投。",),
        processor="rules:test",
        content_hash=article.content_hash,
    )
    response = json.dumps(
        {
            "events": [
                {
                    "company": "星河芯片",
                    "event_type": "funding",
                    "industry_tags": ["semiconductor"],
                    "funding_round": "A++轮",
                    "funding_amount": "亿元",
                    "cumulative_funding_amount": "",
                    "investors": ["远山资本"],
                    "event_status": "completed",
                    "event_summary": "星河芯片完成融资",
                    "evidence_quotes": ['"星河芯片"完成亿元首轮融资，远山资本领投。'],
                    "confidence": "high",
                },
                {
                    "company": "银河机器人",
                    "event_type": "technical_milestone",
                    "industry_tags": ["robotics"],
                    "funding_round": "",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "event_status": "completed",
                    "event_summary": "银河机器人实现量产",
                    "evidence_quotes": ["银河机器人已经实现量产。"],
                    "confidence": "high",
                },
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )
    processor = MiniMaxSemanticProcessor(_Runner(response))

    events = processor.process(channel, article, [seed])

    assert processor.last_audit["status"] == "accepted"
    assert len(events) == 1
    assert events[0].canonical_company == "星河芯片"
    assert events[0].funding_round == ""
    assert events[0].evidence_quotes == seed.evidence_quotes
    assert "minimax_seed_quote_substituted" in events[0].ambiguities
    assert "minimax_ungrounded_field_removed:funding_round" in events[0].ambiguities


@pytest.mark.parametrize(
    "value",
    (
        "蚂蚁数科筹备Pre-IPO轮融资",
        "王文涛祝贺雷诺兹履新",
        "竞价看龙头",
        "劳动最光荣",
        "冠军团队可",
    ),
)
def test_editorial_fragments_are_not_company_entities(value):
    assert not is_company_like(value)


@pytest.mark.parametrize(
    "title",
    (
        "永鼎股份收到采购订单",
        "亿田智能签署长期供货合同",
        "爱美客取得医疗器械注册证",
        "恒兴新材拟投资建设新基地",
        "SK海力士样品已交付并扩充产能",
        "Grok 4.5现已正式发布",
    ),
)
def test_cls_routes_explicit_operational_signals_without_sector_keyword(title):
    index = SourceArticleIndex(
        source_id="cls-telegraph",
        source_article_id="1",
        channel="telegraph",
        canonical_url="https://www.cls.cn/detail/1",
        title=title,
        published_at="2026-07-29T12:00:00+08:00",
        discovered_at="2026-07-30T05:00:00+08:00",
        cursor_value="1",
        listing_page="https://www.cls.cn/telegraph",
        listing_position=1,
        content_hash="index",
        discovery_method="xhr:cls-v1-roll",
        summary=title,
    )

    assert ClsAdapter().should_fetch_detail(ClsAdapter.channels[0], index)
