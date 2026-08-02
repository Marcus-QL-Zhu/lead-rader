import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


class _Runner:
    def __init__(self, response):
        self.response = response

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return self.response


def _fixture(body: str):
    channel = SourceChannel(
        source_id="v24",
        name="v24",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )
    article = CleanArticle(
        index=SourceArticleIndex(
            source_id="v24",
            source_article_id="article",
            channel="latest",
            canonical_url="https://example.com/article",
            title="硬科技动态",
            published_at="2026-07-31",
            discovered_at="2026-07-31T05:00:00+08:00",
            cursor_value="1",
            listing_page="https://example.com",
            listing_position=1,
            content_hash="index",
            discovery_method="exact",
        ),
        clean_body=body,
        content_hash="body",
    )
    return channel, article


def _event(company: str, quote: str, *, round_name: str = ""):
    return {
        "company": company,
        "event_type": "funding",
        "industry_tags": ["semiconductor"],
        "funding_round": round_name,
        "funding_amount": "",
        "cumulative_funding_amount": "",
        "investors": [],
        "event_status": "completed",
        "event_summary": quote,
        "evidence_quotes": [quote],
        "confidence": "high",
    }


def _payload(events, rejections=()):
    return json.dumps(
        {
            "events": events,
            "rejections": list(rejections),
            "ambiguities": [],
        },
        ensure_ascii=False,
    )


def test_composite_model_round_covers_each_exact_ledger_round():
    for candidate in ("种子轮", "天使轮", "天使+轮", "天使++轮"):
        assert MiniMaxSemanticProcessor._funding_round_is_covered(
            candidate,
            "种子轮、天使轮、天使+/++轮",
        )
    assert not MiniMaxSemanticProcessor._funding_round_is_covered(
        "A轮",
        "A+轮",
    )


def test_covered_candidate_rejection_is_ignored_instead_of_failing_article():
    quote = "甲辰科技完成A轮融资。"
    channel, article = _fixture(quote)
    candidate = MiniMaxSemanticProcessor._event_candidates(quote)[0]
    response = _payload(
        [_event("甲辰科技", quote, round_name="A轮")],
        [{"id": candidate["id"], "reason_code": "invalid_subject"}],
    )
    processor = MiniMaxSemanticProcessor(_Runner(response))

    events = processor.process(channel, article, [])

    assert [(event.canonical_company, event.funding_round) for event in events] == [
        ("甲辰科技", "A轮")
    ]
    assert processor.last_audit["status"] == "accepted"
    assert processor.last_audit["rejected_candidate_count"] == 0


def test_corrected_seed_rejection_is_ignored_instead_of_failing_article():
    quote = "Nexus Data Centers正洽谈融资，资金拟用于建设服务Anthropic的项目。"
    channel, article = _fixture(quote)
    wrong_seed = SemanticEvent(
        source_id="v24",
        source_article_id="article",
        canonical_url=article.index.canonical_url,
        company_mentions=("Anthropic",),
        canonical_company="Anthropic",
        event_type="funding",
        event_date="2026-07-31",
        industry_tags=("artificial_intelligence",),
        event_summary=quote,
        evidence_quotes=(quote,),
        processor="rules:test",
        content_hash="body",
        event_status="completed",
    )
    seed_id = MiniMaxSemanticProcessor._rule_seed_id(wrong_seed)
    response = _payload(
        [
            {
                **_event("Nexus Data Centers", quote),
                "funding_round": "未披露",
                "event_status": "started",
            }
        ],
        [{"id": seed_id, "reason_code": "invalid_subject"}],
    )
    processor = MiniMaxSemanticProcessor(_Runner(response))

    events = processor.process(channel, article, [wrong_seed])

    assert [(event.canonical_company, event.funding_round) for event in events] == [
        ("Nexus Data Centers", "")
    ]
    assert processor.last_audit["status"] == "accepted"
    assert processor.last_audit["corrected_seed_count"] == 1


def test_explicit_legal_name_alias_grounds_short_primary_quote():
    body = (
        "武汉超导智能装备科技有限公司（以下简称“武汉超导”）专注高端装备。"
        "武汉超导完成A轮融资。"
    )
    channel, article = _fixture(body)
    quote = "武汉超导完成A轮融资。"
    response = _payload(
        [_event("武汉超导智能装备科技有限公司", quote, round_name="A轮")]
    )
    processor = MiniMaxSemanticProcessor(_Runner(response))

    events = processor.process(channel, article, [])

    assert [event.canonical_company for event in events] == [
        "武汉超导智能装备科技有限公司"
    ]
    assert processor.last_audit["status"] == "accepted"


def test_static_capability_is_filtered_but_future_capacity_target_remains():
    body = (
        "武汉超导具备从设计到交付的全流程能力。"
        "武汉超导将建设高温超导装备制造基地。"
    )

    candidates = MiniMaxSemanticProcessor._event_candidates(body)

    assert all("全流程能力" not in item["quote"] for item in candidates)
    assert any(
        item["event_type"] in {"factory_or_capacity", "new_site_or_entity"}
        and "将建设" in item["quote"]
        for item in candidates
    )


def test_aggregate_round_count_summary_is_not_a_second_candidate():
    body = "璨辰科技完成种子轮、天使轮融资，连续完成两轮融资。"

    candidates = MiniMaxSemanticProcessor._event_candidates(body)

    assert {item["funding_round"] for item in candidates} == {
        "种子轮",
        "天使轮",
    }


def test_market_expansion_path_is_not_misread_as_factory_capacity():
    body = (
        "到2027年，业务边界将向工业产线、零售货架延伸，"
        "遵循物流到商用服务的拓展路径，拓宽落地场景边界。"
    )

    assert all(
        item["event_type"] != "factory_or_capacity"
        for item in MiniMaxSemanticProcessor._event_candidates(body)
    )


def test_candidate_subject_hint_removes_event_date_suffix():
    assert MiniMaxSemanticProcessor._candidate_subject_hint(
        "月之暗面于7月17日"
    ) == "月之暗面"


def test_model_only_ungrounded_round_is_removed_without_losing_event():
    quote = "华辰芯光宣布完成新一轮超亿元融资。"
    channel, article = _fixture(quote)
    response = _payload(
        [_event("华辰芯光", quote, round_name="A++轮")]
    )
    processor = MiniMaxSemanticProcessor(_Runner(response))

    events = processor.process(channel, article, [])

    assert [(event.canonical_company, event.funding_round) for event in events] == [
        ("华辰芯光", "")
    ]
    assert (
        "minimax_ungrounded_field_removed:funding_round"
        in events[0].ambiguities
    )


def test_timeout_retries_original_prompt_instead_of_larger_repair_wrapper():
    quote = "甲辰科技完成A轮融资。"
    channel, article = _fixture(quote)
    valid = _payload([_event("甲辰科技", quote, round_name="A轮")])

    class _TimeoutThenSuccess:
        def __init__(self):
            self.prompts = []

        def run(self, prompt, *, session_id, system_prompt=""):
            del session_id, system_prompt
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise RuntimeError("LLM provider request failed: TimeoutError")
            return valid

    runner = _TimeoutThenSuccess()
    processor = MiniMaxSemanticProcessor(runner)

    events = processor.process(channel, article, [])

    assert len(events) == 1
    assert runner.prompts[1] == runner.prompts[0]
    assert processor.last_audit["status"] == "repaired"


def test_large_digest_is_split_before_it_reaches_model_timeout_size():
    body = "行业背景材料。" * 1000

    chunks = MiniMaxSemanticProcessor._semantic_chunks(body)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 5000 for chunk in chunks)
