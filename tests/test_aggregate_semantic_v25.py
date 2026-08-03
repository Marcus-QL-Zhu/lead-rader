from __future__ import annotations

import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


class StaticRunner:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def run(
        self,
        prompt: str,
        *,
        session_id: str,
        system_prompt: str = "",
    ) -> str:
        del prompt, session_id, system_prompt
        return json.dumps(self.payload, ensure_ascii=False)


class SequenceRunner:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads
        self.calls = 0

    def run(
        self,
        prompt: str,
        *,
        session_id: str,
        system_prompt: str = "",
    ) -> str:
        del prompt, session_id, system_prompt
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return json.dumps(payload, ensure_ascii=False)


def _article() -> CleanArticle:
    index = SourceArticleIndex(
        source_id="test-source",
        source_article_id="article-1",
        channel="test",
        canonical_url="https://example.com/article-1",
        title="星河科技完成融资",
        published_at="2026-08-01",
        discovered_at="2026-08-01T00:00:00+00:00",
        cursor_value="1",
        listing_page="https://example.com",
        listing_position=1,
        content_hash="index-hash",
        discovery_method="fixture",
    )
    return CleanArticle(
        index=index,
        clean_body="星河科技完成 “ A轮 ” 融资。",
        content_hash="article-hash",
    )


def _payload() -> dict[str, object]:
    return {
        "events": [
            {
                "company": "星河科技",
                "event_type": "funding",
                "event_status": "completed",
                "funding_round": "A轮",
                "funding_amount": "9亿元",
                "cumulative_funding_amount": "",
                "investors": ["不存在资本"],
                "industry_tags": ["semiconductor"],
                "event_summary": "星河科技完成A轮融资。",
                "evidence_quotes": ["星河科技完成 A轮 融资。"],
                "confidence": "high",
            }
        ],
        "rejections": [],
        "ambiguities": [],
    }


def test_project_payload_restores_source_quote_and_removes_ungrounded_fields() -> None:
    processor = MiniMaxSemanticProcessor(None)

    events = processor.project_payload(_article(), [], _payload())

    assert len(events) == 1
    assert events[0].evidence_quotes[0] == "星河科技完成 “ A轮 ” 融资。"
    assert events[0].funding_round == "A轮"
    assert events[0].funding_amount == ""
    assert events[0].investors == ()
    assert "minimax_ungrounded_field_removed:funding_amount" in events[0].ambiguities
    assert "minimax_ungrounded_investor_removed:不存在资本" in events[0].ambiguities
    assert processor.last_audit["status"] == "projected"
    assert processor.last_audit["unmapped_candidate_count"] == 0
    assert processor.last_audit["document_type"] == "single_company_flash"


def test_project_payload_matches_the_production_process_projection() -> None:
    article = _article()
    payload = _payload()
    channel = SourceChannel(
        source_id="test-source",
        name="Test",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )

    projected = MiniMaxSemanticProcessor(None).project_payload(article, [], payload)
    processed = MiniMaxSemanticProcessor(StaticRunner(payload)).process(
        channel,
        article,
        [],
    )

    assert [event.to_dict() for event in projected] == [
        event.to_dict() for event in processed
    ]


def test_claim_and_span_citation_restores_host_owned_source_text() -> None:
    article = _article()
    candidate = MiniMaxSemanticProcessor._event_candidates(article.clean_body)[0]
    payload = _payload()
    event = payload["events"][0]
    assert isinstance(event, dict)
    event["claim_ids"] = [candidate["claim_id"]]
    event["span_ids"] = [candidate["span_id"]]
    event["evidence_quotes"] = ["模型改写且不在原文中的句子"]

    processor = MiniMaxSemanticProcessor(None)
    events = processor.project_payload(article, [], payload)

    assert len(events) == 1
    assert events[0].evidence_quotes == ("星河科技完成 “ A轮 ” 融资。",)
    assert events[0].claim_ids == (candidate["claim_id"],)
    assert events[0].span_ids == (candidate["span_id"],)
    assert candidate["char_start"] == 0
    assert candidate["char_end"] == len(article.clean_body)
    assert candidate["event_status_hint"] == "completed"
    assert processor.last_audit["strict_claim_contract_ready"] is True


def test_shadow_audit_exposes_uncited_legacy_event() -> None:
    processor = MiniMaxSemanticProcessor(None)

    events = processor.project_payload(_article(), [], _payload())

    assert len(events) == 1
    assert events[0].claim_ids == ()
    assert processor.last_audit["uncited_model_event_count"] == 1
    assert processor.last_audit["strict_claim_contract_ready"] is False


def test_strict_contract_removes_uncited_model_only_event() -> None:
    processor = MiniMaxSemanticProcessor(None, strict_claim_contract=True)

    events = processor.project_payload(_article(), [], _payload())

    assert events == []
    assert processor.last_audit["status"] == "projected_partial"
    assert processor.last_audit["strict_claim_contract_ready"] is False
    assert processor.last_audit["model_unadjudicated_candidate_ids"]


def test_strict_contract_retries_uncited_claim_by_identity() -> None:
    article = _article()
    candidate = MiniMaxSemanticProcessor._event_candidates(article.clean_body)[0]
    cited = _payload()
    cited_event = cited["events"][0]
    assert isinstance(cited_event, dict)
    cited_event["claim_ids"] = [candidate["claim_id"]]
    cited_event["span_ids"] = [candidate["span_id"]]
    runner = SequenceRunner([_payload(), cited])
    channel = SourceChannel(
        source_id="test-source",
        name="Test",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )
    processor = MiniMaxSemanticProcessor(runner, strict_claim_contract=True)

    events = processor.process(channel, article, [])

    assert runner.calls == 2
    assert len(events) == 1
    assert events[0].claim_ids == (candidate["claim_id"],)
    assert processor.last_audit["claim_retry_attempted"] is True
    assert processor.last_audit["claim_retry_resolved_candidate_ids"] == [
        candidate["id"]
    ]
    assert processor.last_audit["strict_claim_contract_ready"] is True
    assert processor.last_audit["status"] == "repaired"


def test_rule_seed_without_regex_candidate_gets_host_owned_claim() -> None:
    article = _article()
    quote = "星河科技与远山大学开展联合研究。"
    article = CleanArticle(
        index=article.index,
        clean_body=quote,
        content_hash=article.content_hash,
    )
    seed = SemanticEvent(
        source_id=article.index.source_id,
        source_article_id=article.index.source_article_id,
        canonical_url=article.index.canonical_url,
        company_mentions=("星河科技",),
        canonical_company="星河科技",
        event_type="partnership",
        event_date=article.index.published_at,
        industry_tags=(),
        event_summary=quote,
        evidence_quotes=(quote,),
        processor="rules:test",
        content_hash=article.content_hash,
    )
    channel = SourceChannel(
        source_id="test-source",
        name="Test",
        url="https://example.com",
        source_grade="B",
        event_prior=("partnership",),
        allowed_hosts=("example.com",),
    )

    events = MiniMaxSemanticProcessor(None).process(channel, article, [seed])

    assert len(events) == 1
    assert events[0].claim_ids[0].startswith("c_seed_")
    assert events[0].span_ids[0].startswith("s_")


def test_subject_grounding_ignores_stock_code_parenthetical() -> None:
    quote = (
        "专注创新药物开发的汉康-KY（股票代码：7827）今日以每股120元"
        "承销价正式挂牌创新板上市。"
    )

    assert MiniMaxSemanticProcessor._company_event_subject_grounded(
        "汉康-KY",
        quote,
        "ipo_or_listing",
    )
    assert MiniMaxSemanticProcessor._company_event_subject_grounded(
        "天微电子",
        (
            "天微电子(688511)7月30日公告，公司拟通过增资扩股及"
            "存量股权收购组合方式取得秀为科技60%控股股权。"
        ),
        "merger_acquisition",
    )


def test_subject_grounding_accepts_digest_company_colon_action() -> None:
    assert MiniMaxSemanticProcessor._company_event_subject_grounded(
        "\u91d1\u676f\u7535\u5de5",
        "\u3010\u4e2d\u6807\u5408\u540c\u3011 \u91d1\u676f\u7535\u5de5\uff1a\u4e2d\u6807\u5927\u5510\u96c6\u56e2\u7ea611.5\u4ebf\u5143\u91c7\u8d2d\u9879\u76ee\u3002",
        "major_order",
    )
    assert MiniMaxSemanticProcessor._company_event_subject_grounded(
        "\u6d77\u878d\u79d1\u6280",
        "\u6d77\u878d\u79d1\u6280\uff1a\u62df1.3\u4ebf\u5143\u6536\u8d2d\u7cd6\u76d2\u98df\u54c155%\u80a1\u6743\u5e76\u4e0e\u76d2\u9a6c\u7b7e\u7f72\u957f\u671f\u6218\u7565\u5408\u4f5c\u534f\u8bae\u3002",
        "merger_acquisition",
    )


def test_pronominal_result_can_bind_to_company_two_sentences_earlier() -> None:
    body = (
        "嘉亨家化披露杭州拼便宜要约收购结果。"
        "根据报告书，杭州拼便宜将按比例收购股份。"
        "此次要约收购完成后，杭州拼便宜及其一致行动人持有公司50.8%股份。"
    )
    primary = "此次要约收购完成后，杭州拼便宜及其一致行动人持有公司50.8%股份。"

    expanded = MiniMaxSemanticProcessor._expand_pronominal_subject_context(
        body,
        primary,
        "嘉亨家化",
    )

    assert expanded == body
    assert MiniMaxSemanticProcessor._company_event_subject_grounded(
        "嘉亨家化",
        expanded,
        "merger_acquisition",
    )


def test_new_entity_candidate_supports_company_before_成立() -> None:
    body = "近日，江苏华脉小草科技有限公司成立，经营范围包含光通信设备制造。"

    candidates = MiniMaxSemanticProcessor._event_candidates(body)

    assert len(candidates) == 1
    assert candidates[0]["event_type"] == "new_site_or_entity"
    assert candidates[0]["quote"] == body


def test_headline_can_trigger_a_clue_but_cannot_become_evidence() -> None:
    article = _article()
    article = CleanArticle(
        index=article.index,
        clean_body="这是一段不包含融资事实的公司介绍。",
        content_hash=article.content_hash,
    )

    events = MiniMaxSemanticProcessor(None).project_payload(article, [], _payload())

    assert events == []


def test_mixed_status_sentence_exposes_independent_atomic_claims() -> None:
    candidates = MiniMaxSemanticProcessor._event_candidates(
        "甲辰科技完成A轮融资，并启动B轮融资。"
    )
    by_round = {candidate["funding_round"]: candidate for candidate in candidates}

    assert {"A轮", "B轮"}.issubset(by_round)
    assert {
        item["event_status"] for item in by_round["A轮"]["atomic_action_hints"]
    } == {"completed"}
    assert {
        item["event_status"] for item in by_round["B轮"]["atomic_action_hints"]
    } == {"started"}
    assert all(
        item["claim_id"].startswith("ac_")
        for candidate in by_round.values()
        for item in candidate["atomic_action_hints"]
    )
    assert all(
        item["text"] == candidate["quote"]
        and item["action_text"] in candidate["quote"]
        for candidate in by_round.values()
        for item in candidate["atomic_action_hints"]
    )


def test_same_status_attributes_keep_one_parent_claim() -> None:
    body = (
        "\u7532\u8fb0\u79d1\u6280\u6709\u9650\u516c\u53f8\u6210\u7acb\u4e8e2025\u5e741\u6708\uff0c"
        "\u6ce8\u518c\u8d44\u672c5000\u4e07\u5143\u4eba\u6c11\u5e01\u3002"
    )

    candidates = MiniMaxSemanticProcessor._event_candidates(body)

    assert len(candidates) == 1
    assert candidates[0]["event_status_hint"] == "completed"
    assert MiniMaxSemanticProcessor._required_claim_ids(candidates[0]) == {
        candidates[0]["claim_id"]
    }
    assert all(
        "\u6ce8\u518c\u8d44\u672c" not in item["action_text"]
        for item in candidates[0]["atomic_action_hints"]
    )


def test_projection_salvages_valid_event_when_peer_event_is_invalid() -> None:
    payload = _payload()
    events = payload["events"]
    assert isinstance(events, list)
    events.append(
        {
            "company": "不存在的主体",
            "event_type": "funding",
            "event_status": "completed",
            "industry_tags": [],
            "investors": [],
            "evidence_quotes": ["不存在的主体完成融资。"],
        }
    )
    processor = MiniMaxSemanticProcessor(None)

    projected = processor.project_payload(_article(), [], payload)

    assert [event.canonical_company for event in projected] == ["星河科技"]
    assert processor.last_audit["status"] == "projected_partial"
    assert processor.last_audit["validation_issue_count"] == 1


def test_projection_ignores_bad_rejection_without_losing_valid_event() -> None:
    payload = _payload()
    payload["rejections"] = [{"id": "c_unknown", "reason_code": "generic_commentary"}]
    processor = MiniMaxSemanticProcessor(None)

    projected = processor.project_payload(_article(), [], payload)

    assert [event.canonical_company for event in projected] == ["星河科技"]
    assert processor.last_audit["status"] == "projected_partial"
    assert processor.last_audit["rejection_issue_count"] == 1


def test_production_process_keeps_valid_peer_in_partial_chunk() -> None:
    payload = _payload()
    events = payload["events"]
    assert isinstance(events, list)
    events.append(
        {
            "company": "不存在的主体",
            "event_type": "funding",
            "event_status": "completed",
            "industry_tags": [],
            "investors": [],
            "evidence_quotes": ["不存在的主体完成融资。"],
        }
    )
    channel = SourceChannel(
        source_id="test-source",
        name="Test",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )
    processor = MiniMaxSemanticProcessor(StaticRunner(payload))

    projected = processor.process(channel, _article(), [])

    assert [event.canonical_company for event in projected] == ["星河科技"]
    assert processor.last_audit["status"] == "partial"
    assert processor.last_audit["validation_issue_count"] == 1


def test_strict_contract_tracks_each_atomic_claim_in_a_mixed_candidate() -> None:
    body = "甲辰科技计划建设工厂，并已完成一期投产。"
    article = CleanArticle(
        index=SourceArticleIndex(
            source_id="test-source",
            source_article_id="mixed-1",
            channel="test",
            canonical_url="https://example.com/mixed-1",
            title="甲辰科技工厂规划",
            published_at="2026-08-01",
            discovered_at="2026-08-01T00:00:00+00:00",
            cursor_value="mixed-1",
            listing_page="https://example.com",
            listing_position=1,
            content_hash="mixed-index-hash",
            discovery_method="fixture",
        ),
        clean_body=body,
        content_hash="mixed-article-hash",
    )
    candidate = next(
        item
        for item in MiniMaxSemanticProcessor._event_candidates(body)
        if item["event_status_hint"] == "mixed"
    )
    first_atomic = candidate["atomic_action_hints"][0]
    payload = {
        "events": [
            {
                "company": "甲辰科技",
                "event_type": "factory_or_capacity",
                "event_status": "target",
                "claim_ids": [first_atomic["claim_id"]],
                "span_ids": [first_atomic["span_id"]],
                "industry_tags": [],
                "investors": [],
                "evidence_quotes": ["模型改写不可信"],
                "confidence": "high",
            }
        ],
        "rejections": [],
        "ambiguities": [],
    }
    processor = MiniMaxSemanticProcessor(None, strict_claim_contract=True)

    events = processor.project_payload(article, [], payload)

    assert len(events) == 1
    assert processor.last_audit["strict_claim_contract_ready"] is False
    assert processor.last_audit["model_accepted_claim_ids"] == [
        first_atomic["claim_id"]
    ]
    assert processor.last_audit["model_unadjudicated_claim_ids"] == [
        candidate["atomic_action_hints"][1]["claim_id"]
    ]


def test_chunk_claim_spans_keep_full_article_offsets() -> None:
    prefix = "背景。" * 1700
    signal = "星河科技完成A轮融资。"
    article = CleanArticle(
        index=_article().index,
        clean_body=prefix + signal,
        structured_data={
            "_semantic_unit": {
                "unit_id": "u_tail",
                "char_start": len(prefix),
                "char_end": len(prefix) + len(signal),
            }
        },
        content_hash="chunked-article-hash",
    )

    candidates = MiniMaxSemanticProcessor._claim_candidates(article, [])

    assert len(candidates) == 1
    assert candidates[0]["char_start"] == len(prefix)
    assert candidates[0]["char_end"] == len(prefix) + len(signal)
    assert (
        article.clean_body[
            candidates[0]["char_start"] : candidates[0]["char_end"]
        ]
        == signal
    )


def test_final_fan_in_revalidates_claim_span_pairs() -> None:
    article = _article()
    candidate = MiniMaxSemanticProcessor._event_candidates(article.clean_body)[0]
    payload = _payload()
    raw_event = payload["events"][0]
    assert isinstance(raw_event, dict)
    raw_event["claim_ids"] = [candidate["claim_id"]]
    raw_event["span_ids"] = [candidate["span_id"]]
    processor = MiniMaxSemanticProcessor(None)
    valid = processor.project_payload(article, [], payload)[0]
    invalid = SemanticEvent(
        **{
            **valid.to_dict(),
            "span_ids": ("s_not_in_ledger",),
        }
    )

    kept, issues = MiniMaxSemanticProcessor(
        None,
        strict_claim_contract=True,
    )._enforce_claim_contract([invalid], [candidate])

    assert kept == []
    assert "invalid claim/span pair" in issues[0]


def test_model_can_correct_candidate_type_only_when_cited_span_supports_it() -> None:
    body = "甲辰科技与乙巳集团达成战略合作，并发布新型机器人产品。"
    article = CleanArticle(
        index=_article().index,
        clean_body=body,
        content_hash="type-correction-hash",
    )
    candidate = next(
        item
        for item in MiniMaxSemanticProcessor._event_candidates(body)
        if item["event_type"] == "technical_milestone"
    )
    base = {
        "company": "甲辰科技",
        "event_status": "completed",
        "claim_ids": [candidate["claim_id"]],
        "span_ids": [candidate["span_id"]],
        "industry_tags": [],
        "investors": [],
        "evidence_quotes": ["ignored"],
        "confidence": "high",
    }
    processor = MiniMaxSemanticProcessor(None, strict_claim_contract=True)

    corrected = processor.project_payload(
        article,
        [],
        {
            "events": [{**base, "event_type": "partnership"}],
            "rejections": [],
            "ambiguities": [],
        },
    )
    rejected = processor.project_payload(
        article,
        [],
        {
            "events": [{**base, "event_type": "merger_acquisition"}],
            "rejections": [],
            "ambiguities": [],
        },
    )

    assert len(corrected) == 1
    assert any(
        marker.startswith("minimax_corrected_claim_type:")
        for marker in corrected[0].ambiguities
    )
    assert rejected == []


def test_redundant_valid_span_ids_are_normalized_from_claim_identity() -> None:
    article = CleanArticle(
        index=_article().index,
        clean_body="星河科技完成A轮融资，并启动B轮融资。",
        content_hash="redundant-span-hash",
    )
    candidate = MiniMaxSemanticProcessor._event_candidates(article.clean_body)[0]
    atomic = candidate["atomic_action_hints"][0]
    payload = {
        "events": [
            {
                "company": "星河科技",
                "event_type": "funding",
                "event_status": "completed",
                "funding_round": "A轮",
                "claim_ids": [candidate["claim_id"]],
                "span_ids": [candidate["span_id"], atomic["span_id"]],
                "industry_tags": [],
                "investors": [],
                "evidence_quotes": ["ignored"],
                "confidence": "high",
            }
        ],
        "rejections": [],
        "ambiguities": [],
    }
    processor = MiniMaxSemanticProcessor(None, strict_claim_contract=True)

    events = processor.project_payload(article, [], payload)

    assert len(events) == 1
    assert events[0].span_ids == (candidate["span_id"],)
    assert "minimax_redundant_span_ids_removed" in events[0].ambiguities
    assert processor.last_audit["final_bad_claim_pair_event_count"] == 0


def test_missing_but_valid_span_is_projected_from_claim_identity() -> None:
    article = CleanArticle(
        index=_article().index,
        clean_body="鏄熸渤绉戞妧瀹屾垚A杞瀺璧勶紝骞跺惎鍔˙杞瀺璧勩€?",
        content_hash="missing-span-hash",
    )
    article = CleanArticle(
        index=_article().index,
        clean_body="\u661f\u6cb3\u79d1\u6280\u5b8c\u6210A\u8f6e\u878d\u8d44\uff0c\u5e76\u542f\u52a8B\u8f6e\u878d\u8d44\u3002",
        content_hash="missing-span-hash",
    )
    candidate = MiniMaxSemanticProcessor._event_candidates(article.clean_body)[0]
    atomic = candidate["atomic_action_hints"][0]
    payload = {
        "events": [
            {
                "company": "鏄熸渤绉戞妧",
                "event_type": "funding",
                "event_status": "completed",
                "funding_round": "A杞?",
                "claim_ids": [candidate["claim_id"], atomic["claim_id"]],
                "span_ids": [candidate["span_id"]],
                "industry_tags": [],
                "investors": [],
                "evidence_quotes": ["ignored"],
                "confidence": "high",
            }
        ],
        "rejections": [],
        "ambiguities": [],
    }
    payload["events"][0]["company"] = "\u661f\u6cb3\u79d1\u6280"
    payload["events"][0]["funding_round"] = "A\u8f6e"

    events = MiniMaxSemanticProcessor(
        None,
        strict_claim_contract=True,
    ).project_payload(article, [], payload)

    assert len(events) == 1
    assert events[0].span_ids == (candidate["span_id"],)
    assert "minimax_redundant_span_ids_removed" not in events[0].ambiguities


def test_numeric_confidence_is_normalized_without_changing_facts() -> None:
    article = _article()
    candidate = MiniMaxSemanticProcessor._event_candidates(article.clean_body)[0]
    payload = _payload()
    event = payload["events"][0]
    assert isinstance(event, dict)
    event["claim_ids"] = [candidate["claim_id"]]
    event["span_ids"] = [candidate["span_id"]]
    event["confidence"] = 0.95

    events = MiniMaxSemanticProcessor(
        None,
        strict_claim_contract=True,
    ).project_payload(article, [], payload)

    assert len(events) == 1
    assert events[0].confidence == "high"
    assert "minimax_numeric_confidence_normalized" in events[0].ambiguities


def test_model_prompt_flattens_atomic_claims_into_independent_rows() -> None:
    article = CleanArticle(
        index=_article().index,
        clean_body="鐢茶景绉戞妧璁″垝寤鸿宸ュ巶锛屽苟宸插畬鎴愪竴鏈熸姇浜с€?",
        content_hash="flat-ledger-hash",
    )
    article = CleanArticle(
        index=_article().index,
        clean_body="\u7532\u8fb0\u79d1\u6280\u8ba1\u5212\u5efa\u8bbe\u5de5\u5382\uff0c\u5e76\u5df2\u5b8c\u6210\u4e00\u671f\u6295\u4ea7\u3002",
        content_hash="flat-ledger-hash",
    )
    channel = SourceChannel(
        source_id="test-source",
        name="Test",
        url="https://example.com",
        source_grade="B",
        event_prior=("factory_or_capacity",),
        allowed_hosts=("example.com",),
    )

    prompt = MiniMaxSemanticProcessor._prompt(channel, article, [])
    payload = json.loads(prompt.split("\u8f93\u5165\uff1a", 1)[1])
    ledger = payload["candidate_ledger"]

    assert len(ledger) >= 2
    assert all("atomic_action_hints" not in row for row in ledger)
    assert all(row["id"] == row["claim_id"] for row in ledger)
    assert len({row["claim_id"] for row in ledger}) == len(ledger)


def test_atomic_claim_can_be_independently_rejected() -> None:
    body = "甲辰科技计划建设工厂，行业通常计划建设产能。"
    article = CleanArticle(
        index=_article().index,
        clean_body=body,
        content_hash="atomic-rejection-hash",
    )
    candidate = next(
        item
        for item in MiniMaxSemanticProcessor._event_candidates(body)
        if len(item["atomic_action_hints"]) == 2
    )
    first, second = candidate["atomic_action_hints"]
    payload = {
        "events": [
            {
                "company": "甲辰科技",
                "event_type": "factory_or_capacity",
                "event_status": "target",
                "claim_ids": [first["claim_id"]],
                "span_ids": [first["span_id"]],
                "industry_tags": [],
                "investors": [],
                "evidence_quotes": ["ignored"],
                "confidence": "high",
            }
        ],
        "rejections": [
            {
                "id": second["claim_id"],
                "reason_code": "generic_commentary",
            }
        ],
        "ambiguities": [],
    }
    processor = MiniMaxSemanticProcessor(None, strict_claim_contract=True)

    events = processor.project_payload(article, [], payload)

    assert len(events) == 1
    assert processor.last_audit["model_unadjudicated_claim_ids"] == []
    assert processor.last_audit["strict_claim_contract_ready"] is True


def test_bounded_roundup_history_is_rejected_before_claim_retry() -> None:
    article = CleanArticle(
        index=SourceArticleIndex(
            **{
                **_article().index.__dict__,
                "title": "8\u6708\u65b0\u767b\u8bb05\u5bb6\u57fa\u91d1\u7ba1\u7406\u4eba",
                "published_at": "2025-09-02",
            }
        ),
        clean_body=(
            "8\u6708\u65b0\u767b\u8bb0\u57fa\u91d1\u7ba1\u7406\u4eba "
            "\u4e00\u3001\u7532\u57fa\u91d1\u7ba1\u7406\u6709\u9650\u516c\u53f8"
            "\u6210\u7acb\u4e8e2024\u5e748\u67088\u65e5\uff0c"
            "\u7531\u4e59\u8d44\u672c\u4e0e\u4e19\u8d44\u672c\u5171\u540c\u51fa\u8d44\u8bbe\u7acb\u3002"
        ),
        content_hash="roundup-history-hash",
    )
    candidates = MiniMaxSemanticProcessor._claim_candidates(article, [])
    failed = {
        claim_id
        for candidate in candidates
        for claim_id in MiniMaxSemanticProcessor._required_claim_ids(candidate)
    }

    rejected = MiniMaxSemanticProcessor._deterministic_historical_rejections(
        article,
        candidates,
        failed,
    )

    assert rejected == failed


def test_auxiliary_subject_prefix_scanner_avoids_backtracking() -> None:
    assert MiniMaxSemanticProcessor._is_auxiliary_subject_prefix("公司" * 2_000)
    assert not MiniMaxSemanticProcessor._is_auxiliary_subject_prefix("公司" * 2_000 + "X")
