from __future__ import annotations

import json

import pytest

from ht_lead_radar.aggregate_adapters.action_span_ledger import (
    ActionSpanLedger,
    AtomicClaim,
)
from ht_lead_radar.aggregate_adapters.claim_adjudication import (
    ClaimCentricSemanticProcessor,
    _merge_events,
    _technical_product_keys,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def _article(body: str) -> CleanArticle:
    return CleanArticle(
        index=SourceArticleIndex(
            source_id="source",
            source_article_id="1",
            channel="news",
            canonical_url="https://example.invalid/1",
            title="测试",
            published_at="2026-08-01T00:00:00+08:00",
            discovered_at="2026-08-01T01:00:00+08:00",
            cursor_value="1",
            listing_page="https://example.invalid",
            listing_position=1,
            content_hash="index-hash",
            discovery_method="exact",
        ),
        clean_body=body,
        content_hash="body-hash",
    )


def _channel() -> SourceChannel:
    return SourceChannel(
        source_id="source",
        name="source",
        url="https://example.invalid",
        source_grade="A",
        event_prior=(),
        allowed_hosts=("example.invalid",),
    )


def _input_payload(prompt: str) -> dict:
    marker = "\ninput="
    assert marker in prompt
    return json.loads(prompt.split(marker, 1)[1])


class AcceptingRunner:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls = 0

    def run(self, prompt: str, *, session_id: str, system_prompt: str = "") -> str:
        del session_id, system_prompt
        self.calls += 1
        if self.fail_first and self.calls == 1:
            return "not-json"
        if prompt.startswith("修复上一份JSON"):
            embedded = prompt.split("input=", 1)[1].split("\nprior_output=", 1)[0]
            payload = _input_payload(embedded)
        else:
            payload = _input_payload(prompt)
        decisions = []
        for claim in payload["claims"]:
            decisions.append(
                {
                    "claim_id": claim["claim_id"],
                    "decision": "accept",
                    "subject_entity_id": claim["allowed_subject_entity_ids"][0],
                    "event_type": claim["event_type_hint"],
                    "event_status": claim["event_status_hint"],
                    "funding_round": "",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "industry_tags": [],
                    "confidence": "high",
                }
            )
        return json.dumps({"decisions": decisions}, ensure_ascii=False)


class RejectingRunner:
    def run(self, prompt: str, *, session_id: str, system_prompt: str = "") -> str:
        del session_id, system_prompt
        payload = _input_payload(prompt)
        return json.dumps(
            {
                "decisions": [
                    {
                        "claim_id": claim["claim_id"],
                        "decision": "reject",
                        "reason_code": "not_company_event",
                    }
                    for claim in payload["claims"]
                ]
            }
        )


def test_claim_centric_processor_injects_host_owned_subject_and_span() -> None:
    article = _article("甲辰科技完成A轮融资。")
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(_channel(), article, [], [])

    assert len(events) == 1
    event = events[0]
    assert event.canonical_company == "甲辰科技"
    assert event.evidence_quotes == ("甲辰科技完成A轮融资。",)
    assert event.subject_entity_id.startswith("ae_")
    assert event.claim_ids[0].startswith("ac_")
    assert event.span_ids[0].startswith("as_")
    assert processor.last_audit["strict_claim_contract_ready"] is True


def test_claim_centric_processor_accepts_isolated_prompt_experiment_config() -> None:
    class CapturingRunner(AcceptingRunner):
        def __init__(self) -> None:
            super().__init__()
            self.prompt = ""
            self.system_prompt = ""

        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            self.prompt = prompt
            self.system_prompt = system_prompt
            return super().run(
                prompt,
                session_id=session_id,
                system_prompt=system_prompt,
            )

    runner = CapturingRunner()
    few_shot = {"input": {"claims": []}, "output": {"decisions": []}}
    processor = ClaimCentricSemanticProcessor(
        runner,
        model_identity="minimax/test",
        system_prompt="custom-system",
        few_shot=few_shot,
        prompt_version="experiment-r1-a",
        contract_version="experiment-contract",
    )

    events = processor.process(
        _channel(), _article("甲辰科技完成A轮融资。"), [], []
    )

    assert events[0].prompt_version == "experiment-r1-a"
    assert processor.cache_key == (
        "experiment-r1-a|minimax/test|experiment-contract"
    )
    assert runner.system_prompt == "custom-system"
    assert '"contract_version":"experiment-contract"' in runner.prompt
    assert json.dumps(
        few_shot, ensure_ascii=False, separators=(",", ":")
    ) in runner.prompt


def test_semantic_wrapper_passes_isolated_claim_prompt_config() -> None:
    processor = MiniMaxSemanticProcessor(
        None,
        claim_centric_v27=True,
        claim_prompt_config={"prompt_version": "experiment-wrapper-v1"},
    )

    assert processor.cache_key.endswith("|rules-only|v2-shadow")


def test_semantic_wrapper_rejects_unknown_claim_prompt_config() -> None:
    with pytest.raises(ValueError, match="unsupported claim prompt config"):
        MiniMaxSemanticProcessor(
            None,
            claim_centric_v27=True,
            claim_prompt_config={"unexpected": "value"},
        )


def test_claim_processor_does_not_repair_provider_infrastructure_failure() -> None:
    class OfflineRunner:
        def __init__(self) -> None:
            self.calls = 0

        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del prompt, session_id, system_prompt
            self.calls += 1
            raise ConnectionError("provider request failed")

    runner = OfflineRunner()
    processor = ClaimCentricSemanticProcessor(
        runner, model_identity="minimax/test"
    )
    events = processor.process(
        _channel(),
        _article(
            "甲辰科技完成A轮融资。乙巳机器人签署合作协议。"
            "丙午智能发布机器人产品。"
        ),
        [],
        [],
    )

    assert events == []
    assert runner.calls == 1
    assert processor.last_audit["batch_statuses"] == ["infrastructure_failed"]
    assert processor.last_audit["infrastructure_errors"]
    assert len(processor.last_audit["failed_claim_ids"]) == (
        processor.last_audit["candidate_count"]
    )


def test_claim_centric_processor_repairs_once_then_accepts() -> None:
    runner = AcceptingRunner(fail_first=True)
    processor = ClaimCentricSemanticProcessor(runner, model_identity="minimax/test")

    events = processor.process(
        _channel(), _article("甲辰科技完成A轮融资。"), [], []
    )

    assert events
    assert runner.calls == 2
    assert processor.last_audit["batch_statuses"] == ["repaired"]


def test_narrow_repair_ignores_repeated_already_valid_decisions() -> None:
    class RepeatingRepairRunner:
        def __init__(self) -> None:
            self.first_decisions = []

        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del session_id, system_prompt
            if prompt.startswith("修复上一份JSON"):
                repaired = []
                for decision in self.first_decisions:
                    value = dict(decision)
                    value["funding_round"] = ""
                    repaired.append(value)
                return json.dumps({"decisions": repaired}, ensure_ascii=False)
            payload = _input_payload(prompt)
            decisions = []
            for claim in payload["claims"]:
                decisions.append(
                    {
                        "claim_id": claim["claim_id"],
                        "decision": "accept",
                        "subject_entity_id": claim["allowed_subject_entity_ids"][0],
                        "event_type": claim["event_type_hint"],
                        "event_status": claim["event_status_hint"],
                        "funding_round": (
                            "C轮" if claim["event_type_hint"] == "funding" else ""
                        ),
                        "funding_amount": "",
                        "cumulative_funding_amount": "",
                        "investors": [],
                        "industry_tags": [],
                        "confidence": "high",
                    }
                )
            self.first_decisions = decisions
            return json.dumps({"decisions": decisions}, ensure_ascii=False)

    processor = ClaimCentricSemanticProcessor(
        RepeatingRepairRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(),
        _article("甲辰科技完成A轮融资。甲辰科技签署战略合作协议。"),
        [],
        [],
    )

    assert len(events) == 2
    assert processor.last_audit["failed_claim_ids"] == []
    assert processor.last_audit["strict_claim_contract_ready"] is True


def test_rejected_claim_is_terminal_without_event() -> None:
    processor = ClaimCentricSemanticProcessor(
        RejectingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(), _article("甲辰科技完成A轮融资。"), [], []
    )

    assert events == []
    assert processor.last_audit["rejected_claim_ids"]
    assert processor.last_audit["failed_claim_ids"] == []
    assert processor.last_audit["strict_claim_contract_ready"] is True


def test_two_failed_outputs_fail_closed_per_batch() -> None:
    class BrokenRunner:
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del prompt, session_id, system_prompt
            return "{}"

    processor = ClaimCentricSemanticProcessor(
        BrokenRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(), _article("甲辰科技完成A轮融资。"), [], []
    )

    assert events == []
    assert processor.last_audit["failed_claim_ids"]
    assert processor.last_audit["strict_claim_contract_ready"] is False
    assert processor.last_audit["status"] == "partial"


def test_minimax_processor_delegates_only_when_v27_flag_is_enabled() -> None:
    processor = MiniMaxSemanticProcessor(
        AcceptingRunner(),
        strict_claim_contract=True,
        claim_centric_v27=True,
    )

    events = processor.process(
        _channel(), _article("甲辰科技完成A轮融资。"), []
    )

    assert events[0].subject_entity_id.startswith("ae_")
    assert processor.cache_key.startswith("aggregate-semantic-v27-claim-centric-r5|")
    assert processor.last_audit["claim_contract_version"] == (
        "v5-open-action-ledger"
    )
    assert processor.last_audit["index_content_hash"] == "index-hash"
    assert processor.last_audit["article_content_hash"] == "body-hash"


def test_v27_restores_citations_from_immutable_source_whitespace() -> None:
    source_body = "甲辰科技完成\nA轮融资。"
    scoped_body = "甲辰科技完成 A轮融资。"
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article(scoped_body),
        [],
        [],
        source_body=source_body,
    )

    assert events
    assert all(
        quote in source_body for event in events for quote in event.evidence_quotes
    )
    assert any("\n" in quote for event in events for quote in event.evidence_quotes)


def test_common_minimax_status_aliases_are_normalized_by_host() -> None:
    class PlannedRunner(AcceptingRunner):
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            payload = _input_payload(prompt)
            claim = payload["claims"][0]
            return json.dumps(
                {
                    "decisions": [
                        {
                            "claim_id": claim["claim_id"],
                            "decision": "accept",
                            "subject_entity_id": claim[
                                "allowed_subject_entity_ids"
                            ][0],
                            "event_type": claim["event_type_hint"],
                            "event_status": "planned",
                            "funding_round": "",
                            "funding_amount": "",
                            "cumulative_funding_amount": "",
                            "investors": [],
                            "industry_tags": [],
                            "confidence": "high",
                        }
                    ]
                }
            )

    processor = ClaimCentricSemanticProcessor(
        PlannedRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(), _article("甲辰科技拟投建芯片生产基地。"), [], []
    )

    assert events[0].event_status == "target"


def test_host_locked_event_type_rename_triggers_one_repair() -> None:
    class RenamingRunner(AcceptingRunner):
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del session_id, system_prompt
            self.calls += 1
            if prompt.startswith("修复上一份JSON"):
                embedded = prompt.split("input=", 1)[1].split(
                    "\nprior_output=", 1
                )[0]
                payload = _input_payload(embedded)
            else:
                payload = _input_payload(prompt)
            claim = payload["claims"][0]
            return json.dumps(
                {
                    "decisions": [
                        {
                            "claim_id": claim["claim_id"],
                            "decision": "accept",
                            "subject_entity_id": claim[
                                "allowed_subject_entity_ids"
                            ][0],
                            "event_type": (
                                claim["event_type_hint"]
                                if self.calls > 1
                                else "regulatory_or_clinical"
                            ),
                            "event_status": claim["event_status_hint"],
                            "funding_round": "",
                            "funding_amount": "",
                            "cumulative_funding_amount": "",
                            "investors": [],
                            "industry_tags": [],
                            "confidence": "high",
                        }
                    ]
                }
            )

    runner = RenamingRunner()
    processor = ClaimCentricSemanticProcessor(runner, model_identity="minimax/test")
    events = processor.process(
        _channel(), _article("甲辰科技拟启动IPO。"), [], []
    )

    assert events[0].event_type == "ipo_or_listing"
    assert runner.calls == 2


def test_event_type_mismatch_rejection_is_repaired_as_protocol_error() -> None:
    class MismatchThenAcceptRunner(AcceptingRunner):
        def __init__(self, reason_code: str) -> None:
            super().__init__()
            self.reason_code = reason_code

        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            self.calls += 1
            if prompt.startswith("修复上一份JSON"):
                embedded = prompt.split("input=", 1)[1].split(
                    "\nprior_output=", 1
                )[0]
                payload = _input_payload(embedded)
                return json.dumps(
                    {
                        "decisions": [
                            {
                                "claim_id": claim["claim_id"],
                                "decision": "accept",
                                "subject_entity_id": claim[
                                    "allowed_subject_entity_ids"
                                ][0],
                                "event_type": claim["event_type_hint"],
                                "event_status": claim["event_status_hint"],
                                "funding_round": "",
                                "funding_amount": "",
                                "cumulative_funding_amount": "",
                                "investors": [],
                                "industry_tags": [],
                                "confidence": "high",
                            }
                            for claim in payload["claims"]
                        ]
                    }
                )
            payload = _input_payload(prompt)
            return json.dumps(
                {
                    "decisions": [
                            {
                                "claim_id": claim["claim_id"],
                                "decision": "reject",
                                "reason_code": self.reason_code,
                            }
                            for claim in payload["claims"]
                        ]
                }
            )

    for reason_code in (
        "event_type_mismatch",
        "event_type_mismatch_subject_or_action",
    ):
        runner = MismatchThenAcceptRunner(reason_code)
        processor = ClaimCentricSemanticProcessor(
            runner, model_identity="minimax/test"
        )
        events = processor.process(
            _channel(),
            _article(
                "交大昂立：收到《行政处罚事先告知书》，"
                "8月4日起实施其他风险警示。"
            ),
            [],
            [],
        )

        expected = {"ipo_or_listing", "regulatory_or_clinical"}
        assert {event.event_type for event in events} == expected
        assert runner.calls == 2
        assert processor.last_audit["strict_claim_contract_ready"] is True


def test_routine_trading_resume_is_suppressed_beside_control_change() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article("贝肯能源：控股股东将变更为极宁科技，8月3日复牌。"),
        [],
        [],
    )

    assert [event.event_type for event in events] == ["merger_acquisition"]
    assert events[0].event_status == "target"


def test_persistent_locked_type_rejection_uses_audited_host_fallback() -> None:
    class PersistentMismatchRunner:
        def __init__(self) -> None:
            self.calls = 0

        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del session_id, system_prompt
            self.calls += 1
            if prompt.startswith("修复上一份JSON"):
                embedded = prompt.split("input=", 1)[1].split(
                    "\nprior_output=", 1
                )[0]
                payload = _input_payload(embedded)
            else:
                payload = _input_payload(prompt)
            return json.dumps(
                {
                    "decisions": [
                        {
                            "claim_id": claim["claim_id"],
                            "decision": "reject",
                            "reason_code": (
                                "event_type_mismatch"
                                if self.calls == 1
                                else "action_text_not_company_initiated"
                            ),
                        }
                        for claim in payload["claims"]
                    ]
                }
            )

    runner = PersistentMismatchRunner()
    processor = ClaimCentricSemanticProcessor(runner, model_identity="minimax/test")
    events = processor.process(
        _channel(),
        _article(
            "交大昂立：收到《行政处罚事先告知书》，"
            "8月4日起实施其他风险警示。"
        ),
        [],
        [],
    )

    assert {event.event_type for event in events} == {"ipo_or_listing"}
    assert runner.calls == 2
    assert len(processor.last_audit["host_fallback_claim_ids"]) == 1
    assert processor.last_audit["batch_statuses"] == ["host_fallback"]
    assert processor.last_audit["strict_claim_contract_ready"] is True


def test_persistent_nonmandatory_type_mismatch_fails_closed() -> None:
    class MismatchRunner:
        def __init__(self) -> None:
            self.calls = 0

        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del session_id, system_prompt
            self.calls += 1
            if prompt.startswith("修复上一份JSON"):
                embedded = prompt.split("input=", 1)[1].split(
                    "\nprior_output=", 1
                )[0]
                payload = _input_payload(embedded)
            else:
                payload = _input_payload(prompt)
            return json.dumps(
                {
                    "decisions": [
                        {
                            "claim_id": claim["claim_id"],
                            "decision": "reject",
                            "reason_code": "event_type_mismatch_in_span",
                        }
                        for claim in payload["claims"]
                    ]
                }
            )

    runner = MismatchRunner()
    processor = ClaimCentricSemanticProcessor(runner, model_identity="minimax/test")
    events = processor.process(
        _channel(), _article("甲辰科技投建芯片生产基地。"), [], []
    )

    assert events == []
    assert runner.calls == 2
    assert processor.last_audit["failed_claim_ids"]
    assert processor.last_audit["host_fallback_claim_ids"] == []


def test_rejection_reason_synonym_is_normalized_to_bounded_taxonomy() -> None:
    class HistoricalRunner:
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del session_id, system_prompt
            payload = _input_payload(prompt)
            return json.dumps(
                {
                    "decisions": [
                        {
                            "claim_id": claim["claim_id"],
                            "decision": "reject",
                            "reason_code": "historical_context_only",
                        }
                        for claim in payload["claims"]
                    ]
                }
            )

    processor = ClaimCentricSemanticProcessor(
        HistoricalRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(), _article("甲辰科技完成A轮融资。"), [], []
    )

    assert events == []
    assert processor.last_audit["rejection_reason_counts"] == {
        "historical_or_background": 1
    }


def test_distinct_same_type_events_are_not_merged_across_spans() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article("甲辰科技发布A芯片。甲辰科技发布B芯片。"),
        [],
        [],
    )

    assert len(events) == 2
    assert {event.event_summary for event in events} == {
        "甲辰科技发布A芯片。",
        "甲辰科技发布B芯片。",
    }


def test_repeated_same_action_is_deduplicated_across_spans() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article(
            "7月27日，甲辰科技发布工业决策大脑。"
            "近期，甲辰科技发布工业决策大脑。"
        ),
        [],
        [],
    )

    assert len(events) == 1
    assert len(events[0].evidence_quotes) == 2


def test_repeated_versioned_product_is_deduplicated_across_phrasings() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article(
            "MiniMax发布旗舰模型MiniMax H3。"
            "不仅如此，MiniMax H3还是一个正式开源模型。"
        ),
        [],
        [],
    )

    assert len(events) == 1
    assert len(events[0].evidence_quotes) == 2


def test_repeated_quoted_product_is_deduplicated_across_summary_and_detail() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article(
            "阿里云发布全新产品“千问办公”。"
            "在发布会上，阿里云正式上线产品“千问办公”。"
        ),
        [],
        [],
    )

    assert len(events) == 1
    assert len(events[0].evidence_quotes) == 2


def test_repeated_mixed_script_product_is_deduplicated() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article("甲辰科技发布产品纳米Work。随后，甲辰科技正式上线产品纳米Work。"),
        [],
        [],
    )

    assert len(events) == 1
    assert len(events[0].evidence_quotes) == 2


def test_generic_endpoint_action_uses_evidence_product_for_dedupe() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article(
            "阿里巴巴上线享造Agent。"
            "随后，阿里巴巴表示享造Agent已上线App和PC端。"
        ),
        [],
        [],
    )

    assert len(events) == 1
    assert len(events[0].evidence_quotes) == 2


def test_scale_production_is_capacity_not_duplicate_technical_event() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article("芯擎科技：已实现智能驾驶芯片的规模化量产。"),
        [],
        [],
    )

    assert [event.event_type for event in events] == ["factory_or_capacity"]


def test_funding_subject_is_recipient_not_investor() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article("英伟达将向甲辰科技投资1亿元。"),
        [],
        [],
    )

    assert len(events) == 1
    assert events[0].canonical_company == "甲辰科技"
    assert "英伟达" in events[0].investors


def test_two_versions_in_one_span_are_not_merged() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article("xAI表示Grok 4.6将于8月发布，数周后推出Grok 4.7。"),
        [],
        [],
    )

    assert len(events) == 2


def test_two_versions_keep_atomic_identity_across_headline_and_detail() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )

    events = processor.process(
        _channel(),
        _article(
            "xAI称Grok 4.6将于8月发布，数周后推出Grok 4.7。"
            "xAI表示Grok 4.6预计将于8月发布。"
            "xAI称Grok 4.7将在Grok4.6发布数周后推出。"
        ),
        [],
        [],
    )

    assert len(events) == 2
    assert all(len(event.evidence_quotes) == 2 for event in events)


def test_undisclosed_funding_amount_is_normalized_to_blank() -> None:
    class UndisclosedRunner(AcceptingRunner):
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            payload = json.loads(
                super().run(
                    prompt, session_id=session_id, system_prompt=system_prompt
                )
            )
            for decision in payload["decisions"]:
                decision["funding_amount"] = "未披露"
            return json.dumps(payload, ensure_ascii=False)

    processor = ClaimCentricSemanticProcessor(
        UndisclosedRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(), _article("甲辰科技完成一笔大额融资。"), [], []
    )

    assert len(events) == 1
    assert events[0].funding_amount == ""
    assert processor.last_audit["failed_claim_ids"] == []


def test_host_mandatory_invalid_optional_fields_fall_back_to_locked_projection() -> None:
    class TwiceInvalidRunner(AcceptingRunner):
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            payload = json.loads(
                super().run(
                    prompt, session_id=session_id, system_prompt=system_prompt
                )
            )
            for decision in payload["decisions"]:
                decision["funding_amount"] = "未在原文出现的金额"
                decision["investors"] = ["未在原文出现的投资方"]
            return json.dumps(payload, ensure_ascii=False)

    processor = ClaimCentricSemanticProcessor(
        TwiceInvalidRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(), _article("近日，甲辰科技宣布完成一笔大额融资。"), [], []
    )

    assert len(events) == 1
    assert events[0].canonical_company == "甲辰科技"
    assert events[0].funding_amount == ""
    assert events[0].investors == ()
    assert processor.last_audit["failed_claim_ids"] == []
    assert processor.last_audit["host_fallback_claim_ids"]


def test_multi_round_host_hints_do_not_require_verbatim_model_round() -> None:
    class AmountRunner(AcceptingRunner):
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            payload = json.loads(
                super().run(
                    prompt, session_id=session_id, system_prompt=system_prompt
                )
            )
            for decision in payload["decisions"]:
                decision["funding_amount"] = "数亿元"
            return json.dumps(payload, ensure_ascii=False)

    processor = ClaimCentricSemanticProcessor(
        AmountRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(),
        _article("鹿明机器人宣布完成数亿元A1及A2轮融资。"),
        [],
        [],
    )

    assert {event.funding_round for event in events} == {"A1轮", "A2轮"}
    assert processor.last_audit["failed_claim_ids"] == []


def test_funding_overview_is_dropped_when_detailed_announcement_exists() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(),
        _article(
            "在投资端，甲辰科技完成超10亿元融资。"
            "近日，甲辰科技宣布完成A轮融资，融资金额超过10亿元。"
        ),
        [],
        [],
    )

    funding = [event for event in events if event.event_type == "funding"]
    assert len(funding) == 1
    assert funding[0].funding_round == "A轮"


def test_cumulative_round_background_is_not_a_second_funding_event() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(),
        _article(
            "近日，德塔智能完成近5亿元天使++轮融资。"
            "这是公司成立半年内完成的第六轮融资。"
        ),
        [],
        [],
    )

    funding = [event for event in events if event.event_type == "funding"]
    assert len(funding) == 1


def test_customer_validation_suppresses_same_span_technical_duplicate() -> None:
    processor = ClaimCentricSemanticProcessor(
        AcceptingRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(),
        _article(
            "甲辰科技的首款产品已经进入首批数百台交付与用户复现阶段。"
        ),
        [],
        [],
    )

    assert [event.event_type for event in events] == ["customer_validation"]
    assert processor.last_audit["accepted_claim_ids"] == [
        claim_id
        for event in events
        for claim_id in event.claim_ids
    ]
    assert len(processor.last_audit["suppressed_claim_ids"]) == 1


def test_one_bad_claim_does_not_discard_valid_peers() -> None:
    class OneBadRunner:
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del session_id, system_prompt
            if prompt.startswith("修复上一份JSON"):
                embedded = prompt.split("input=", 1)[1].split(
                    "\nprior_output=", 1
                )[0]
                payload = _input_payload(embedded)
            else:
                payload = _input_payload(prompt)
            decisions = []
            for position, claim in enumerate(payload["claims"]):
                decisions.append(
                    {
                        "claim_id": claim["claim_id"],
                        "decision": "accept",
                        "subject_entity_id": (
                            "ae_invalid"
                            if position == 0
                            else claim["allowed_subject_entity_ids"][0]
                        ),
                        "event_type": claim["event_type_hint"],
                        "event_status": claim["event_status_hint"],
                        "funding_round": "",
                        "funding_amount": "",
                        "cumulative_funding_amount": "",
                        "investors": [],
                        "industry_tags": [],
                        "confidence": "high",
                    }
                )
            return json.dumps({"decisions": decisions}, ensure_ascii=False)

    processor = ClaimCentricSemanticProcessor(
        OneBadRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(),
        _article(
            "甲辰科技完成A轮融资。"
            "乙巳机器人签署战略合作协议。"
            "丙午智能发布机器人产品。"
        ),
        [],
        [],
    )

    assert len(events) == 2
    assert len(processor.last_audit["failed_claim_ids"]) == 1
    assert len(processor.last_audit["accepted_claim_ids"]) == 2
    assert processor.last_audit["batch_statuses"] == ["partial"]


def test_open_action_can_be_classified_only_inside_bounded_taxonomy() -> None:
    class OpenActionRunner:
        def run(
            self, prompt: str, *, session_id: str, system_prompt: str = ""
        ) -> str:
            del session_id, system_prompt
            payload = _input_payload(prompt)
            return json.dumps(
                {
                    "decisions": [
                        {
                            "claim_id": claim["claim_id"],
                            "decision": "accept",
                            "subject_entity_id": claim[
                                "allowed_subject_entity_ids"
                            ][0],
                            "event_type": claim["allowed_event_types"][0],
                            "event_status": claim["event_status_hint"],
                            "funding_round": "",
                            "funding_amount": "",
                            "cumulative_funding_amount": "",
                            "investors": [],
                            "industry_tags": [],
                            "confidence": "medium",
                        }
                        for claim in payload["claims"]
                    ]
                },
                ensure_ascii=False,
            )

    processor = ClaimCentricSemanticProcessor(
        OpenActionRunner(), model_identity="minimax/test"
    )
    events = processor.process(
        _channel(),
        _article("强脑科技带来了脑控训练套件和两项全球首发能力。"),
        [],
        [],
    )

    assert [event.event_type for event in events] == ["technical_milestone"]



def _technical_event(claim_id: str, action_text: str) -> SemanticEvent:
    return SemanticEvent(
        source_id="source",
        source_article_id="1",
        canonical_url="https://example.invalid/1",
        company_mentions=("\u7532\u516c\u53f8",),
        canonical_company="\u7532\u516c\u53f8",
        event_type="technical_milestone",
        event_date="2026-08-01",
        industry_tags=("semiconductor",),
        event_summary=action_text,
        evidence_quotes=(action_text,),
        claim_ids=(claim_id,),
        span_ids=(f"span-{claim_id}",),
        subject_entity_id="ae-company",
    )


def _technical_ledger(*actions: tuple[str, str]) -> ActionSpanLedger:
    return ActionSpanLedger(
        version="test",
        source_id="source",
        source_article_id="1",
        document_type="single_company_flash",
        spans=(),
        claims=tuple(
            AtomicClaim(
                claim_id=claim_id,
                span_id=f"span-{claim_id}",
                event_type_hint="technical_milestone",
                event_status_hint="completed",
                action_text=action_text,
                action_char_start=0,
                action_char_end=len(action_text),
                allowed_subject_entity_ids=("ae-company",),
                primary_subject_entity_id="ae-company",
            )
            for claim_id, action_text in actions
        ),
    )


def test_repeated_uppercase_product_token_is_deduplicated():
    actions = (
        ("ac-1", "\u7532\u516c\u53f8\u53d1\u5e03OLED TDDI\u82af\u7247ICNA3611"),
        ("ac-2", "\u56fd\u4ea7\u9996\u9898OLED TDDI\u82af\u7247\u91cf\u4ea7"),
    )
    ledger = _technical_ledger(*actions)
    events = _merge_events(
        [_technical_event(claim_id, action) for claim_id, action in actions],
        ledger,
    )

    assert len(events) == 1
    assert set(events[0].claim_ids) == {"ac-1", "ac-2"}
    assert "TDDI" in _technical_product_keys(events[0], ledger.claims_by_id())


def test_distinct_numeric_product_codes_remain_separate():
    actions = (
        ("ac-1", "\u7532\u516c\u53f8\u53d1\u5e03OLED TDDI\u82af\u7247ICNA3611"),
        ("ac-2", "\u7532\u516c\u53f8\u53d1\u5e03OLED TDDI\u82af\u7247ICNA3622"),
    )
    ledger = _technical_ledger(*actions)
    events = _merge_events(
        [_technical_event(claim_id, action) for claim_id, action in actions],
        ledger,
    )

    assert len(events) == 2


def _operational_event(
    event_type: str,
    event_status: str,
    claim_id: str,
    *,
    span_id: str = "shared-span",
) -> SemanticEvent:
    return SemanticEvent(
        source_id="source",
        source_article_id="1",
        canonical_url="https://example.invalid/1",
        company_mentions=("甲公司",),
        canonical_company="甲公司",
        event_type=event_type,
        event_date="2026-08-01",
        industry_tags=("embodied_intelligence",),
        event_summary="资金将重点投向技术研发及高端人才引进",
        evidence_quotes=("资金将重点投向技术研发及高端人才引进",),
        claim_ids=(claim_id,),
        span_ids=(span_id,),
        subject_entity_id="ae-company",
        event_status=event_status,
    )


def _operational_ledger(*claim_ids: str) -> ActionSpanLedger:
    return ActionSpanLedger(
        version="test",
        source_id="source",
        source_article_id="1",
        document_type="single_company_flash",
        spans=(),
        claims=tuple(
            AtomicClaim(
                claim_id=claim_id,
                span_id="shared-span",
                event_type_hint="research_or_ip",
                event_status_hint="target",
                action_text="技术研发",
                action_char_start=0,
                action_char_end=4,
                allowed_subject_entity_ids=("ae-company",),
                primary_subject_entity_id="ae-company",
            )
            for claim_id in claim_ids
        ),
    )


def test_same_funding_use_span_collapses_duplicate_operational_events() -> None:
    events = _merge_events(
        [
            _operational_event("research_or_ip", "completed", "ac-r"),
            _operational_event("research_or_ip", "target", "ac-r2"),
            _operational_event("workforce_cluster", "completed", "ac-w"),
        ],
        _operational_ledger("ac-r", "ac-r2", "ac-w"),
    )

    research = [event for event in events if event.event_type == "research_or_ip"]
    workforce = [
        event for event in events if event.event_type == "workforce_cluster"
    ]
    assert len(research) == 1
    assert research[0].event_status == "target"
    assert set(research[0].claim_ids) == {"ac-r", "ac-r2"}
    assert len(workforce) == 1
    assert workforce[0].event_status == "completed"
