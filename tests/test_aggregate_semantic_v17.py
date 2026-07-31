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


def test_v21_runs_minimax_when_no_rule_seed():
    channel, article = _article()
    runner = _Runner()
    processor = MiniMaxSemanticProcessor(runner)

    assert processor.process(channel, article, []) == []
    assert runner.calls == 1
    assert processor.last_audit["status"] == "accepted"
    assert PROMPT_VERSION == "aggregate-semantic-v22"


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



def test_duration_suffix_is_removed_and_non_company_fragments_fail_closed():
    from ht_lead_radar.aggregate_adapters.entities import canonical_company_name

    assert canonical_company_name("\u53ef\u8fbe\u667a\u7075\u5341\u4e2a\u6708\u5185") == "\u53ef\u8fbe\u667a\u7075"
    assert not is_company_like("3\u4e2a\u6708")
    assert not is_company_like("\u4eac\u6d25\u5180\u9996\u5ea7\u5177\u8eab\u667a\u80fd\u8d85\u7ea7\u5de5\u5382\u5728\u4ea6\u5e84")


def test_minimax_can_correct_a_grounded_but_wrong_rule_subject():
    channel, base = _article()
    company = "\u8c6a\u80fd\u80a1\u4efd"
    wrong = "\u4e2d\u4fe1\u8bc1\u5238"
    quote = f"{company}\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c{wrong}\u53d1\u5e03\u7814\u62a5\u8fdb\u884c\u5206\u6790\u3002"
    article = CleanArticle(
        index=base.index,
        clean_body=quote,
        content_hash="correction-body",
    )
    seed = SemanticEvent(
        source_id=channel.source_id,
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=(wrong,),
        canonical_company=wrong,
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=("advanced_manufacturing",),
        funding_round="A\u8f6e",
        funding_amount="1\u4ebf\u5143",
        event_summary=quote,
        evidence_quotes=(quote,),
        confidence="medium",
        processor="rules:test",
        content_hash=article.content_hash,
    )
    response = json.dumps(
        {
            "events": [
                {
                    "company": company,
                    "event_type": "funding",
                    "industry_tags": ["advanced_manufacturing"],
                    "funding_round": "A\u8f6e",
                    "funding_amount": "1\u4ebf\u5143",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "event_status": "completed",
                    "event_summary": quote,
                    "evidence_quotes": [quote],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )

    events = MiniMaxSemanticProcessor(_Runner(response)).process(channel, article, [seed])

    assert len(events) == 1
    assert events[0].canonical_company == company
    assert f"minimax_corrected_rule_company:{wrong}" in events[0].ambiguities
    assert all(event.canonical_company != wrong for event in events)


def _subject_test_event(article, company):
    quote = (
        "\u8c6a\u80fd\u80a1\u4efd\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u4e2d\u4fe1\u8bc1\u5238\u53d1\u5e03\u7814\u62a5\u8fdb\u884c\u5206\u6790\u3002"
    )
    return SemanticEvent(
        source_id=article.index.source_id,
        source_article_id=article.index.source_article_id,
        canonical_url=article.index.canonical_url,
        company_mentions=(company,),
        canonical_company=company,
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=("advanced_manufacturing",),
        funding_round="A\u8f6e",
        funding_amount="1\u4ebf\u5143",
        event_summary=quote,
        evidence_quotes=(quote,),
        confidence="medium",
        processor="rules:test",
        content_hash=article.content_hash,
    )


def _subject_test_response(company, quote):
    return json.dumps(
        {
            "events": [
                {
                    "company": company,
                    "event_type": "funding",
                    "industry_tags": ["advanced_manufacturing"],
                    "funding_round": "A\u8f6e",
                    "funding_amount": "1\u4ebf\u5143",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "event_status": "completed",
                    "event_summary": quote,
                    "evidence_quotes": [quote],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )


def test_minimax_cannot_replace_event_subject_with_analyst_in_same_quote():
    channel, base = _article()
    quote = (
        "\u8c6a\u80fd\u80a1\u4efd\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u4e2d\u4fe1\u8bc1\u5238\u53d1\u5e03\u7814\u62a5\u8fdb\u884c\u5206\u6790\u3002"
    )
    article = CleanArticle(index=base.index, clean_body=quote, content_hash="role")
    seed = _subject_test_event(article, "\u8c6a\u80fd\u80a1\u4efd")
    processor = MiniMaxSemanticProcessor(
        _Runner(_subject_test_response("\u4e2d\u4fe1\u8bc1\u5238", quote))
    )

    events = processor.process(channel, article, [seed])

    assert [item.canonical_company for item in events] == ["\u8c6a\u80fd\u80a1\u4efd"]


def test_model_subject_removes_invalid_duplicate_rule_subject():
    channel, base = _article()
    quote = (
        "\u8c6a\u80fd\u80a1\u4efd\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u4e2d\u4fe1\u8bc1\u5238\u53d1\u5e03\u7814\u62a5\u8fdb\u884c\u5206\u6790\u3002"
    )
    article = CleanArticle(index=base.index, clean_body=quote, content_hash="dedupe")
    correct = _subject_test_event(article, "\u8c6a\u80fd\u80a1\u4efd")
    wrong = _subject_test_event(article, "\u4e2d\u4fe1\u8bc1\u5238")
    processor = MiniMaxSemanticProcessor(
        _Runner(_subject_test_response("\u8c6a\u80fd\u80a1\u4efd", quote))
    )

    events = processor.process(channel, article, [wrong, correct])

    assert [item.canonical_company for item in events] == ["\u8c6a\u80fd\u80a1\u4efd"]


def test_minimax_cannot_treat_lead_investor_as_funding_subject():
    channel, base = _article()
    quote = (
        "\u4e2d\u4fe1\u8bc1\u5238\u9886\u6295\uff0c"
        "\u8c6a\u80fd\u80a1\u4efd\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\u3002"
    )
    article = CleanArticle(index=base.index, clean_body=quote, content_hash="investor")
    seed = _subject_test_event(article, "\u8c6a\u80fd\u80a1\u4efd")
    seed = SemanticEvent(**{**seed.to_dict(), "evidence_quotes": (quote,), "event_summary": quote})
    processor = MiniMaxSemanticProcessor(
        _Runner(_subject_test_response("\u4e2d\u4fe1\u8bc1\u5238", quote))
    )

    events = processor.process(channel, article, [seed])

    assert [item.canonical_company for item in events] == ["\u8c6a\u80fd\u80a1\u4efd"]


def test_correct_model_removes_lead_investor_duplicate_seed():
    channel, base = _article()
    quote = (
        "\u4e2d\u4fe1\u8bc1\u5238\u9886\u6295\uff0c"
        "\u8c6a\u80fd\u80a1\u4efd\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\u3002"
    )
    article = CleanArticle(index=base.index, clean_body=quote, content_hash="investor-dedupe")
    correct = _subject_test_event(article, "\u8c6a\u80fd\u80a1\u4efd")
    wrong = _subject_test_event(article, "\u4e2d\u4fe1\u8bc1\u5238")
    correct = SemanticEvent(**{**correct.to_dict(), "evidence_quotes": (quote,), "event_summary": quote})
    wrong = SemanticEvent(**{**wrong.to_dict(), "evidence_quotes": (quote,), "event_summary": quote})
    processor = MiniMaxSemanticProcessor(
        _Runner(_subject_test_response("\u8c6a\u80fd\u80a1\u4efd", quote))
    )

    events = processor.process(channel, article, [wrong, correct])

    assert [item.canonical_company for item in events] == ["\u8c6a\u80fd\u80a1\u4efd"]


def test_minimax_subject_can_bridge_attribution_comma():
    channel, base = _article()
    quote = (
        "\u8c6a\u80fd\u80a1\u4efd\u5ba3\u5e03\uff0c"
        "\u5df2\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u4e2d\u4fe1\u8bc1\u5238\u53d1\u5e03\u7814\u62a5\u3002"
    )
    article = CleanArticle(index=base.index, clean_body=quote, content_hash="bridge")
    wrong = _subject_test_event(article, "\u4e2d\u4fe1\u8bc1\u5238")
    wrong = SemanticEvent(
        **{**wrong.to_dict(), "evidence_quotes": (quote,), "event_summary": quote}
    )
    processor = MiniMaxSemanticProcessor(
        _Runner(_subject_test_response("\u8c6a\u80fd\u80a1\u4efd", quote))
    )

    events = processor.process(channel, article, [wrong])

    assert [item.canonical_company for item in events] == ["\u8c6a\u80fd\u80a1\u4efd"]


@pytest.mark.parametrize("bridge", ["\u8868\u793a", "\u6d88\u606f\u79f0"])
def test_attribution_source_cannot_bridge_over_explicit_other_company(bridge):
    channel, base = _article()
    quote = (
        f"\u4e2d\u4fe1\u8bc1\u5238{bridge}\uff0c"
        "\u8c6a\u80fd\u80a1\u4efd\u5df2\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\u3002"
    )
    article = CleanArticle(index=base.index, clean_body=quote, content_hash="source")
    correct = _subject_test_event(article, "\u8c6a\u80fd\u80a1\u4efd")
    correct = SemanticEvent(
        **{**correct.to_dict(), "evidence_quotes": (quote,), "event_summary": quote}
    )
    processor = MiniMaxSemanticProcessor(
        _Runner(_subject_test_response("\u4e2d\u4fe1\u8bc1\u5238", quote))
    )

    events = processor.process(channel, article, [correct])

    assert [item.canonical_company for item in events] == ["\u8c6a\u80fd\u80a1\u4efd"]


def test_attribution_bridge_accepts_pronoun_with_adverbials():
    channel, base = _article()
    quote = (
        "\u8c6a\u80fd\u80a1\u4efd\u5ba3\u5e03\uff0c"
        "\u516c\u53f8\u672c\u8f6e\u5df2\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u4e2d\u4fe1\u8bc1\u5238\u53d1\u5e03\u7814\u62a5\u3002"
    )
    article = CleanArticle(index=base.index, clean_body=quote, content_hash="adverbial")
    wrong = _subject_test_event(article, "\u4e2d\u4fe1\u8bc1\u5238")
    wrong = SemanticEvent(
        **{**wrong.to_dict(), "evidence_quotes": (quote,), "event_summary": quote}
    )
    processor = MiniMaxSemanticProcessor(
        _Runner(_subject_test_response("\u8c6a\u80fd\u80a1\u4efd", quote))
    )

    events = processor.process(channel, article, [wrong])

    assert [item.canonical_company for item in events] == ["\u8c6a\u80fd\u80a1\u4efd"]


@pytest.mark.parametrize(
    "object_prefix",
    [
        "\u4e0e",
        "\u548c",
        "\u540c",
        "\u643a\u624b",
        "\u8054\u5408",
        "\u8054\u624b",
        "\u643a",
        "\u534f\u540c",
        "\u4f1a\u540c",
        "\u5055\u540c",
        "\u643a\u540c",
    ],
)
def test_attribution_bridge_accepts_pronoun_with_partnership_object(object_prefix):
    quote = (
        "\u8c6a\u80fd\u80a1\u4efd\u5ba3\u5e03\uff0c\u516c\u53f8"
        f"{object_prefix}\u82f1\u4f1f\u8fbe\u8fbe\u6210\u6218\u7565\u5408\u4f5c\u3002"
    )

    assert MiniMaxSemanticProcessor._company_event_subject_grounded(
        "\u8c6a\u80fd\u80a1\u4efd",
        quote,
        "partnership",
    )
    assert not MiniMaxSemanticProcessor._company_event_subject_grounded(
        "\u82f1\u4f1f\u8fbe",
        quote,
        "partnership",
    )
