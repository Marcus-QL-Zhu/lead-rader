from dataclasses import replace
import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


class _SequenceRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.prompts = []

    def run(self, prompt, *, session_id, system_prompt=""):
        del session_id, system_prompt
        self.prompts.append(prompt)
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def _fixture(body: str):
    channel = SourceChannel(
        source_id="v21",
        name="v21",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )
    article = CleanArticle(
        index=SourceArticleIndex(
            source_id="v21",
            source_article_id="article",
            channel="latest",
            canonical_url="https://example.com/article",
            title="硬科技融资动态",
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


def _payload(*events):
    return json.dumps(
        {"events": list(events), "ambiguities": []},
        ensure_ascii=False,
    )


def _funding(company, quote, *, round_name="", amount="", status="completed"):
    return {
        "company": company,
        "event_type": "funding",
        "industry_tags": ["artificial_intelligence"],
        "funding_round": round_name,
        "funding_amount": amount,
        "cumulative_funding_amount": "",
        "investors": [],
        "event_status": status,
        "event_summary": quote,
        "evidence_quotes": [quote],
        "confidence": "high",
    }


def _seed(
    article,
    company,
    quote,
    *,
    round_name="",
    amount="",
    status="completed",
):
    return SemanticEvent(
        source_id="v21",
        source_article_id="article",
        canonical_url=article.index.canonical_url,
        company_mentions=(company,),
        canonical_company=company,
        event_type="funding",
        event_date="2026-07-31",
        industry_tags=("artificial_intelligence",),
        funding_round=round_name,
        funding_amount=amount,
        event_summary=quote,
        evidence_quotes=(quote,),
        processor="rules:test",
        content_hash="body",
        event_status=status,
    )


def test_no_seed_grounded_event_is_accepted_and_audited():
    quote = "Kando AI完成种子轮融资，用于扩充产品团队。"
    channel, article = _fixture(quote)
    processor = MiniMaxSemanticProcessor(
        _SequenceRunner([_payload(_funding("Kando AI", quote, round_name="种子轮"))])
    )

    events = processor.process(channel, article, [])

    assert [(event.canonical_company, event.funding_round) for event in events] == [
        ("Kando AI", "种子轮")
    ]
    assert processor.last_audit["model_only_count"] == 1


def test_model_correction_replaces_wrong_rule_subject_instead_of_unioning_it():
    quote = "Nexus Data Centers正洽谈融资，资金拟用于建设服务Anthropic的项目。"
    channel, article = _fixture(quote)
    wrong = _seed(article, "Anthropic", quote, status="completed")
    response = _payload(_funding("Nexus Data Centers", quote, status="started"))

    processor = MiniMaxSemanticProcessor(_SequenceRunner([response]))
    events = processor.process(channel, article, [wrong])

    assert [(event.canonical_company, event.event_status) for event in events] == [
        ("Nexus Data Centers", "started")
    ]
    assert processor.last_audit["corrected_seed_count"] == 1
    assert processor.last_audit["rejected_seed_count"] == 0


def test_model_only_pre_ipo_event_survives_next_to_seeded_completed_round():
    first = "月之暗面已完成F轮融资。"
    second = "月之暗面同时启动Pre-IPO轮融资。"
    channel, article = _fixture(first + second)
    seed = _seed(article, "月之暗面", first, round_name="F轮")
    response = _payload(
        _funding("月之暗面", first, round_name="F轮"),
        _funding("月之暗面", second, round_name="Pre-IPO轮", status="started"),
    )

    events = MiniMaxSemanticProcessor(_SequenceRunner([response])).process(
        channel, article, [seed]
    )

    assert {(event.funding_round, event.event_status) for event in events} == {
        ("F轮", "completed"),
        ("Pre-IPO", "started"),
    }


def test_model_authority_deduplicates_same_rule_fact():
    quote = "英灵殿科技完成A轮融资。"
    channel, article = _fixture(quote)
    seed = _seed(article, "英灵殿科技", quote, round_name="A轮")
    response = _payload(_funding("英灵殿科技", quote, round_name="A轮"))

    events = MiniMaxSemanticProcessor(_SequenceRunner([response])).process(
        channel, article, [seed]
    )

    assert len(events) == 1
    assert events[0].processor == "minimax"


def test_valuation_is_not_retained_as_funding_amount():
    quote = "蚂蚁数科启动Pre-IPO轮融资，投前估值500亿美元。"
    channel, article = _fixture(quote)
    response = _payload(
        _funding(
            "蚂蚁数科",
            quote,
            round_name="Pre-IPO轮",
            amount="500亿美元",
            status="started",
        )
    )

    events = MiniMaxSemanticProcessor(_SequenceRunner([response])).process(
        channel, article, []
    )

    assert events[0].funding_round == "Pre-IPO"
    assert events[0].funding_amount == ""


def test_funding_amount_is_not_confused_with_following_separate_valuation():
    quote = "月之暗面完成F轮融资，融资金额超35亿美元，投后估值涨至350亿美元。"
    channel, article = _fixture(quote)
    response = _payload(
        _funding(
            "月之暗面",
            quote,
            round_name="F轮",
            amount="超35亿美元",
        )
    )

    events = MiniMaxSemanticProcessor(_SequenceRunner([response])).process(
        channel, article, []
    )

    assert events[0].funding_amount == "超35亿美元"


def test_same_value_can_be_a_funding_amount_and_a_valuation():
    quote = "甲辰科技完成A轮融资10亿元，投后估值10亿元。"
    channel, article = _fixture(quote)
    response = _payload(_funding("甲辰科技", quote, round_name="A轮", amount="10亿元"))

    events = MiniMaxSemanticProcessor(_SequenceRunner([response])).process(
        channel, article, []
    )

    assert events[0].funding_amount == "10亿元"


def test_amount_before_funding_noun_is_not_erased_by_same_value_valuation():
    for quote in (
        "甲辰科技获10亿元融资，投后估值10亿元。",
        "甲辰科技完成10亿元A轮融资，投后估值10亿元。",
    ):
        channel, article = _fixture(quote)
        round_name = "A轮" if "A轮" in quote else ""
        response = _payload(
            _funding("甲辰科技", quote, round_name=round_name, amount="10亿元")
        )

        events = MiniMaxSemanticProcessor(_SequenceRunner([response])).process(
            channel, article, []
        )

        assert events[0].funding_amount == "10亿元"


def test_coreferential_next_sentence_keeps_second_funding_round():
    first = "月之暗面Kimi已完成F轮融资，融资金额超35亿美元。"
    second = "因超目标金额3倍多，本轮融资系提前关闭，原定8月开始的G轮（Pre IPO轮）已提前开始。"
    channel, article = _fixture(first + second)
    response = _payload(
        _funding("月之暗面", first, round_name="F轮"),
        _funding("月之暗面", second, round_name="G轮（Pre IPO轮）", status="started"),
    )

    events = MiniMaxSemanticProcessor(_SequenceRunner([response])).process(
        channel, article, []
    )

    assert {(event.funding_round, event.event_status) for event in events} == {
        ("F轮", "completed"),
        ("G轮", "started"),
    }


def test_truncated_json_is_repaired_not_silently_accepted():
    quote = "真觉万象完成天使轮融资。"
    channel, article = _fixture(quote)
    valid = _payload(_funding("真觉万象", quote, round_name="天使轮"))
    runner = _SequenceRunner(['{"events":[', valid])
    processor = MiniMaxSemanticProcessor(runner)

    events = processor.process(channel, article, [])

    assert len(events) == 1
    assert runner.calls == 2
    assert processor.last_audit["status"] == "repaired"


class _ChunkRunner:
    def __init__(self):
        self.calls = 0

    def run(self, prompt, *, session_id, system_prompt=""):
        del session_id, system_prompt
        self.calls += 1
        events = []
        first = "甲辰科技完成A轮融资。"
        second = "乙巳机器人完成B轮融资。"
        if first in prompt:
            events.append(_funding("甲辰科技", first, round_name="A轮"))
        if second in prompt:
            events.append(_funding("乙巳机器人", second, round_name="B轮"))
        return _payload(*events)


def test_long_digest_is_chunked_and_global_events_are_merged():
    body = "甲辰科技完成A轮融资。" + "行业背景信息。" * 1800 + "乙巳机器人完成B轮融资。"
    channel, article = _fixture(body)
    runner = _ChunkRunner()
    processor = MiniMaxSemanticProcessor(runner)

    events = processor.process(channel, article, [])

    assert runner.calls > 1
    assert {event.canonical_company for event in events} == {
        "甲辰科技",
        "乙巳机器人",
    }
    assert processor.last_audit["chunk_count"] == runner.calls


def test_long_digest_rule_seed_is_injected_only_into_its_evidence_chunk():
    quote = "甲辰科技完成A轮融资。"
    body = quote + "背景材料" * 3000
    channel, article = _fixture(body)
    seed = _seed(article, "甲辰科技", quote, round_name="A轮")

    class _SeedIsolationRunner:
        def __init__(self):
            self.prompts = []

        def run(self, prompt, *, session_id, system_prompt=""):
            del session_id, system_prompt
            self.prompts.append(prompt)
            if quote in prompt:
                return _payload(_funding("甲辰科技", quote, round_name="A轮"))
            return _payload()

    runner = _SeedIsolationRunner()
    processor = MiniMaxSemanticProcessor(runner)

    events = processor.process(channel, article, [seed])

    seed_id = processor._rule_seed_id(seed)
    assert sum(seed_id in prompt for prompt in runner.prompts) == 1
    assert [event.canonical_company for event in events] == ["甲辰科技"]
    assert processor.last_audit["status"] == "accepted"


def test_partial_candidate_output_retries_only_missing_claim_and_repairs_peer():
    first = "甲辰科技完成A轮融资。"
    second = "乙巳机器人完成B轮融资。"
    channel, article = _fixture(first + second)
    partial = _payload(_funding("甲辰科技", first, round_name="A轮"))
    complete = _payload(
        _funding("甲辰科技", first, round_name="A轮"),
        _funding("乙巳机器人", second, round_name="B轮"),
    )
    runner = _SequenceRunner([partial, complete])
    processor = MiniMaxSemanticProcessor(runner)

    events = processor.process(channel, article, [])

    assert runner.calls == 2
    assert processor.last_audit["status"] == "repaired"
    assert {event.canonical_company for event in events} == {
        "甲辰科技",
        "乙巳机器人",
    }
    assert processor.last_audit["unmapped_candidate_count"] == 0
    assert processor.last_audit["claim_retry_attempted"] is True
    assert processor.last_audit["claim_retry_resolved_candidate_ids"]
    assert processor.last_audit["candidate_disposition_complete"] is True
    assert "adjudicate_failed_claims_only" in runner.prompts[1]
    assert second in runner.prompts[1]
    assert first not in runner.prompts[1]


def test_partial_candidate_output_is_preserved_and_is_not_cache_healthy():
    first = "甲辰科技完成A轮融资。"
    second = "乙巳机器人完成B轮融资。"
    channel, article = _fixture(first + second)
    seed = _seed(article, "甲辰科技", first, round_name="A轮")
    partial = _payload(_funding("甲辰科技", first, round_name="A轮"))
    processor = MiniMaxSemanticProcessor(_SequenceRunner([partial, partial]))

    events = processor.process(channel, article, [seed])

    assert processor.last_audit["status"] == "partial"
    assert [event.canonical_company for event in events] == ["甲辰科技"]
    assert processor.last_audit["unmapped_candidate_count"] >= 1


def test_candidate_cannot_be_silently_rejected_by_an_ambiguity_string():
    quote = "甲辰科技宣布启动A轮融资。"
    channel, article = _fixture(quote)
    candidate = MiniMaxSemanticProcessor._event_candidates(quote)[0]
    response = json.dumps(
        {
            "events": [],
            "ambiguities": [f"candidate:{candidate['id']}:仅为媒体转述，正文未确认"],
        },
        ensure_ascii=False,
    )
    processor = MiniMaxSemanticProcessor(_SequenceRunner([response]))

    assert processor.process(channel, article, []) == []
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["unmapped_candidate_count"] == 1


def test_reason_coded_candidate_rejection_requires_deterministic_support():
    _, article = _fixture("本次募资将全部投入医疗级模组量产迭代。")
    candidate = {
        "id": "c_funding_use",
        "event_type": "technical_milestone",
        "funding_round": "",
        "quote": "本次募资将全部投入医疗级模组量产迭代。",
    }
    payload = {
        "rejections": [{"id": candidate["id"], "reason_code": "funding_use_or_plan"}]
    }

    rejected_candidates, rejected_seeds = (
        MiniMaxSemanticProcessor._validated_rejections(
            article,
            payload,
            [candidate],
            [],
            [],
        )
    )

    assert rejected_candidates == {candidate["id"]}
    assert rejected_seeds == set()


def test_free_text_cannot_reject_a_grounded_real_seed():
    quote = "甲辰科技宣布启动A轮融资。"
    channel, article = _fixture(quote)
    seed = _seed(article, "甲辰科技", quote, round_name="A轮", status="started")
    seed_id = MiniMaxSemanticProcessor._rule_seed_id(seed)
    response = json.dumps(
        {
            "events": [],
            "rejections": [{"id": seed_id, "reason_code": "generic_commentary"}],
            "ambiguities": [f"{seed_id}: 未证实转述"],
        },
        ensure_ascii=False,
    )
    processor = MiniMaxSemanticProcessor(_SequenceRunner([response, response]))

    events = processor.process(channel, article, [seed])

    assert [event.canonical_company for event in events] == ["甲辰科技"]
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["rejected_seed_count"] == 0


def test_rejection_with_unknown_id_fails_closed():
    channel, article = _fixture("甲辰科技宣布启动A轮融资。")
    response = json.dumps(
        {
            "events": [],
            "rejections": [
                {"id": "c_not_in_ledger", "reason_code": "generic_commentary"}
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )
    processor = MiniMaxSemanticProcessor(_SequenceRunner([response]))

    assert processor.process(channel, article, []) == []
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["rejection_issue_count"] == 2
    assert processor.last_audit["claim_retry_attempted"] is True


def test_global_rejection_removes_rule_fallback_conflict_after_chunk_fan_in():
    quote = "本次募资将全部投入医疗级模组量产迭代"
    _, article = _fixture(quote)
    seed = SemanticEvent(
        source_id="v21",
        source_article_id="article",
        canonical_url=article.index.canonical_url,
        company_mentions=("甲辰科技",),
        canonical_company="甲辰科技",
        event_type="technical_milestone",
        event_date="2026-07-31",
        industry_tags=("medical_device",),
        event_summary=quote,
        evidence_quotes=(quote,),
        processor="rules:test",
        content_hash="body",
        event_status="target",
    )
    seed_id = MiniMaxSemanticProcessor._rule_seed_id(seed)
    events, removed_count = MiniMaxSemanticProcessor._remove_rejection_conflicts(
        [seed],
        [],
        set(),
        {seed_id},
    )

    assert events == []
    assert removed_count == 1


def test_rejection_conflict_removes_only_matching_evidence_not_same_key_event():
    first_quote = "甲辰科技发布第一代工业控制平台。"
    second_quote = "甲辰科技发布第二代边缘计算平台。"
    _, article = _fixture(first_quote + second_quote)
    first = SemanticEvent(
        source_id="v21",
        source_article_id="article",
        canonical_url=article.index.canonical_url,
        company_mentions=("甲辰科技",),
        canonical_company="甲辰科技",
        event_type="technical_milestone",
        event_date="2026-07-31",
        industry_tags=("industrial_software",),
        event_summary=first_quote,
        evidence_quotes=(first_quote,),
        processor="minimax",
        content_hash="body",
        event_status="completed",
    )
    second = replace(
        first,
        event_summary=second_quote,
        evidence_quotes=(second_quote,),
    )
    candidate = {
        "id": "c_first",
        "event_type": "technical_milestone",
        "funding_round": "",
        "subject_hint": "甲辰科技",
        "quote": first_quote,
    }

    events, removed_count = MiniMaxSemanticProcessor._remove_rejection_conflicts(
        [first, second],
        [candidate],
        {candidate["id"]},
        set(),
    )

    assert events == [second]
    assert removed_count == 1


def test_unmapped_candidate_fallback_does_not_restore_rejected_seed():
    rejected_quote = "北部湾港集团与交通运输部天津水运工程科学研究院签署战略合作协议。"
    body = rejected_quote + "乙卯机器人完成B轮融资。"
    channel, article = _fixture(body)
    seed = SemanticEvent(
        source_id="v21",
        source_article_id="article",
        canonical_url=article.index.canonical_url,
        company_mentions=("北部湾港集团与交通运输部天津水运工程科学研究院",),
        canonical_company="北部湾港集团与交通运输部天津水运工程科学研究院",
        event_type="partnership",
        event_date="2026-07-31",
        industry_tags=("medical_device",),
        event_summary=rejected_quote,
        evidence_quotes=(rejected_quote,),
        processor="rules:test",
        content_hash="body",
        event_status="completed",
    )
    seed_id = MiniMaxSemanticProcessor._rule_seed_id(seed)
    response = json.dumps(
        {
            "events": [],
            "rejections": [{"id": seed_id, "reason_code": "invalid_subject"}],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )
    processor = MiniMaxSemanticProcessor(_SequenceRunner([response, response]))

    assert processor.process(channel, article, [seed]) == []
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["rejected_seed_count"] == 1
    assert processor.last_audit["rules_preserved_count"] == 0


def test_partial_projection_recomputes_model_only_and_corrected_audit_counts():
    first = "甲辰科技完成A轮融资。"
    second = "乙卯机器人完成B轮融资。"
    third = "丙午芯片完成C轮融资。"
    channel, article = _fixture(first + second + third)
    seed = _seed(article, "甲辰科技", first, round_name="A轮")
    partial = _payload(_funding("乙卯机器人", second, round_name="B轮"))
    processor = MiniMaxSemanticProcessor(_SequenceRunner([partial, partial]))

    events = processor.process(channel, article, [seed])

    assert [event.canonical_company for event in events] == ["乙卯机器人"]
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["model_only_count"] == 1
    assert processor.last_audit["corrected_seed_count"] == 0
    assert processor.last_audit["rules_preserved_count"] == 0


def test_candidate_ledger_covers_all_high_value_allowed_event_families():
    body = "".join(
        (
            "甲辰科技成立上海研发中心。",
            "乙巳机器人取得L4测试许可。",
            "工信部印发机器人行业标准通知。",
            "丙午电子启动芯片采购招标。",
            "头部车企客户已导入丁未科技方案。",
            "戊申集团宣布收购己酉芯片。",
            "庚戌科技上线企业ERP系统。",
        )
    )

    event_types = {
        item["event_type"] for item in MiniMaxSemanticProcessor._event_candidates(body)
    }

    assert {
        "new_site_or_entity",
        "regulatory_or_clinical",
        "policy_or_standard",
        "procurement_tender",
        "customer_validation",
        "merger_acquisition",
        "enterprise_system",
    } <= event_types


def test_same_quote_two_rounds_cannot_be_covered_by_one_model_event():
    quote = "月之暗面完成F轮融资并同时启动Pre-IPO轮融资。"
    channel, article = _fixture(quote)
    seeds = [
        _seed(article, "月之暗面", quote, round_name="F轮"),
        _seed(
            article,
            "月之暗面",
            quote,
            round_name="Pre-IPO",
            status="started",
        ),
    ]
    partial = _payload(_funding("月之暗面", quote, round_name="F轮"))
    processor = MiniMaxSemanticProcessor(_SequenceRunner([partial, partial]))

    events = processor.process(channel, article, seeds)

    assert processor.last_audit["status"] == "partial"
    assert {(event.funding_round, event.event_status) for event in events} == {
        ("F轮", "completed"),
    }


def test_same_amount_two_companies_cannot_adjudicate_each_other():
    first = "甲辰科技完成A轮融资10亿元。"
    second = "乙巳机器人完成A轮融资10亿元。"
    channel, article = _fixture(first + second)
    seeds = [
        _seed(article, "甲辰科技", first, round_name="A轮", amount="10亿元"),
        _seed(article, "乙巳机器人", second, round_name="A轮", amount="10亿元"),
    ]
    partial = _payload(_funding("甲辰科技", first, round_name="A轮", amount="10亿元"))
    processor = MiniMaxSemanticProcessor(_SequenceRunner([partial, partial]))

    events = processor.process(channel, article, seeds)

    assert processor.last_audit["status"] == "partial"
    assert {event.canonical_company for event in events} == {"甲辰科技"}


def test_same_sentence_same_round_two_subjects_cannot_be_silently_merged():
    quote = "甲辰科技完成A轮融资，乙巳机器人也完成A轮融资。"
    channel, article = _fixture(quote)
    partial = _payload(_funding("甲辰科技", quote, round_name="A轮"))
    processor = MiniMaxSemanticProcessor(_SequenceRunner([partial, partial]))

    events = processor.process(channel, article, [])
    assert {event.canonical_company for event in events} == {"甲辰科技"}
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["candidate_count"] == 2
    assert processor.last_audit["unmapped_candidate_count"] >= 1


def test_same_sentence_without_comma_two_subjects_are_separate_candidates():
    quote = "甲辰科技完成A轮融资同时乙巳机器人也完成A轮融资。"
    channel, article = _fixture(quote)
    partial = _payload(_funding("甲辰科技", quote, round_name="A轮"))
    processor = MiniMaxSemanticProcessor(_SequenceRunner([partial, partial]))

    events = processor.process(channel, article, [])
    assert {event.canonical_company for event in events} == {"甲辰科技"}
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["candidate_count"] == 2
    assert processor.last_audit["unmapped_candidate_count"] >= 1


def test_respectively_joined_subjects_are_both_required():
    quote = "甲辰科技与乙巳机器人分别完成A轮融资。"
    channel, article = _fixture(quote)
    partial = _payload(_funding("甲辰科技", quote, round_name="A轮"))
    processor = MiniMaxSemanticProcessor(_SequenceRunner([partial, partial]))

    events = processor.process(channel, article, [])
    assert {event.canonical_company for event in events} == {"甲辰科技"}
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["unmapped_candidate_count"] >= 1


def test_long_candidate_quote_window_contains_the_signal():
    body = "背景" * 400 + "甲辰科技完成A轮融资" + "背景" * 300

    candidates = MiniMaxSemanticProcessor._event_candidates(body)

    assert len(candidates) == 1
    assert "甲辰科技完成A轮融资" in candidates[0]["quote"]


def test_long_unpunctuated_boundary_candidate_is_not_cacheable_when_missing():
    body = "背景" * 3497 + "甲辰科技启动A轮融资" + "背景" * 2000
    channel, article = _fixture(body)
    empty = _payload()
    processor = MiniMaxSemanticProcessor(_SequenceRunner([empty, empty]))

    assert processor.process(channel, article, []) == []
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["unmapped_candidate_count"] >= 1


def test_single_sentence_multiple_event_families_all_require_coverage():
    quote = "甲辰科技与乙巳集团达成战略合作，并发布新型机器人产品。"
    channel, article = _fixture(quote)
    partnership = {
        **_funding("甲辰科技", quote),
        "event_type": "partnership",
        "funding_round": "",
    }
    partial = _payload(partnership)
    processor = MiniMaxSemanticProcessor(_SequenceRunner([partial, partial]))

    events = processor.process(channel, article, [])
    assert [event.event_type for event in events] == ["partnership"]
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["unmapped_candidate_count"] >= 1


def test_reverse_technical_and_delivery_phrasing_enters_candidate_ledger():
    body = "集创北方芯片实现规模化量产。某机器人产品批量下线暨首批交付。"

    event_types = {
        item["event_type"] for item in MiniMaxSemanticProcessor._event_candidates(body)
    }

    assert "technical_milestone" in event_types


def test_non_operating_fund_and_public_body_noise_are_not_candidates():
    body = (
        "某产业股权投资基金合伙企业正式成立。"
        "中国认证委员会与海外国家认可机构签署战略合作协议。"
    )

    assert MiniMaxSemanticProcessor._event_candidates(body) == []
