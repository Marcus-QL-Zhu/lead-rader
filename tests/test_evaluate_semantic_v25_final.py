from __future__ import annotations

from scripts.evaluate_semantic_v25_final import _company, evaluate


def test_company_normalization_treats_chinese_digit_brand_as_numeric_alias() -> None:
    assert _company("三六零") == _company("360集团")


def _case() -> dict:
    body = "甲公司宣布完成A轮融资。"
    return {
        "key": "source:1",
        "clean_body": body,
        "document_type": "single_company_flash",
        "candidates": [
            {"candidate_id": "candidate-1", "required_claim_ids": ["c_1"]},
            {"candidate_id": "candidate-2", "required_claim_ids": ["c_2"]},
        ],
        "annotation": {
            "annotation_status": "complete",
            "article_notes": "",
            "candidate_dispositions": [
                {
                    "claim_id": "c_1",
                    "disposition": "accepted",
                    "reason_code": "current_company_event",
                },
                {
                    "claim_id": "c_2",
                    "disposition": "rejected",
                    "reason_code": "historical_background",
                },
            ],
            "gold_events": [
                {
                    "canonical_company": "甲公司",
                    "event_type": "funding",
                    "event_status": "completed",
                    "importance": "strong",
                    "claim_ids": ["c_1"],
                    "evidence_span": {
                        "char_start": 0,
                        "char_end": len(body),
                        "text": body,
                    },
                }
            ],
        },
    }


def test_evaluator_matches_claim_company_type_status_and_candidate_dispositions() -> None:
    gold = {
        "dataset_version": "v1",
        "cases": [_case()],
    }
    prediction = {
        "dataset_version": "v1",
        "status": "complete",
        "summary": {
            "uncited_model_event_count": 0,
            "bad_claim_pair_event_count": 0,
            "strict_ready_article_count": 1,
        },
        "results": [
            {
                "key": "source:1",
                "events": [
                    {
                        "canonical_company": "甲公司",
                        "event_type": "funding",
                        "event_status": "completed",
                        "claim_ids": ["c_1"],
                        "evidence_quotes": ["甲公司宣布完成A轮融资。"],
                    }
                ],
                "audit": {
                    "model_accepted_candidate_ids": ["c_1"],
                    "deterministic_rejected_candidate_ids": ["c_2"],
                },
            }
        ],
    }

    result = evaluate(gold, prediction)

    assert result["overall"]["exact_match_count"] == 1
    assert result["overall"]["strong_current_recall"] == 1.0
    assert result["candidate_dispositions"]["candidate_accuracy"] == 1.0
    assert result["passed"] is True


def test_evaluator_never_matches_events_across_articles() -> None:
    first = _case()
    second = _case()
    second["key"] = "source:2"
    gold = {"dataset_version": "v1", "cases": [first, second]}
    prediction = {
        "dataset_version": "v1",
        "status": "complete",
        "summary": {
            "uncited_model_event_count": 0,
            "bad_claim_pair_event_count": 0,
            "strict_ready_article_count": 2,
        },
        "results": [
            {
                "key": "source:1",
                "events": [],
                "audit": {
                    "model_accepted_candidate_ids": [],
                    "deterministic_rejected_claim_ids": ["c_1", "c_2"],
                },
            },
            {
                "key": "source:2",
                "events": [
                    {
                        "canonical_company": "甲公司",
                        "event_type": "funding",
                        "event_status": "completed",
                        "claim_ids": ["c_1"],
                        "evidence_quotes": ["甲公司宣布完成A轮融资。"],
                    },
                    {
                        "canonical_company": "甲公司",
                        "event_type": "funding",
                        "event_status": "completed",
                        "claim_ids": ["c_1"],
                        "evidence_quotes": ["甲公司宣布完成A轮融资。"],
                    },
                ],
                "audit": {
                    "model_accepted_candidate_ids": ["c_1"],
                    "deterministic_rejected_claim_ids": ["c_2"],
                },
            },
        ],
    }

    result = evaluate(gold, prediction)

    assert result["overall"]["exact_match_count"] == 1
    assert result["overall"]["unsupported_predicted_event_count"] == 1
    assert result["overall"]["exact_recall"] == 0.5
    assert result["passed"] is False


def test_evaluator_excludes_gold_ambiguous_case_from_hard_metrics() -> None:
    case = _case()
    case["annotation"]["annotation_status"] = "gold_ambiguous"
    gold = {"dataset_version": "v1", "cases": [case]}
    prediction = {
        "dataset_version": "v1",
        "status": "complete",
        "summary": {
            "uncited_model_event_count": 0,
            "bad_claim_pair_event_count": 0,
            "strict_ready_article_count": 1,
        },
        "results": [{"key": "source:1", "events": [], "audit": {}}],
    }

    result = evaluate(gold, prediction)

    assert result["eligible_gold_case_count"] == 0
    assert result["excluded_gold_ambiguous_case_count"] == 1
    assert result["overall"]["gold_event_count"] == 0


def test_evaluator_requires_atomic_funding_round_when_gold_distinguishes_it() -> None:
    case = _case()
    event_a = case["annotation"]["gold_events"][0]
    event_a["atomic_discriminator"] = "funding_round=A轮"
    event_a_plus = {
        **event_a,
        "atomic_discriminator": "funding_round=A+轮",
        "claim_ids": ["c_2"],
    }
    case["annotation"]["candidate_dispositions"][1] = {
        "claim_id": "c_2",
        "disposition": "accepted",
        "reason_code": "current_company_event",
    }
    case["annotation"]["gold_events"].append(event_a_plus)
    prediction = {
        "dataset_version": "v1",
        "status": "complete",
        "summary": {
            "uncited_model_event_count": 0,
            "bad_claim_pair_event_count": 0,
            "strict_ready_article_count": 1,
        },
        "results": [
            {
                "key": "source:1",
                "events": [
                    {
                        "canonical_company": "甲公司",
                        "event_type": "funding",
                        "event_status": "completed",
                        "claim_ids": [claim_id],
                        "evidence_quotes": ["甲公司宣布完成A轮融资。"],
                        "funding_round": "A轮",
                    }
                    for claim_id in ("c_1", "c_2")
                ],
                "audit": {"model_accepted_candidate_ids": ["c_1", "c_2"]},
            }
        ],
    }

    result = evaluate({"dataset_version": "v1", "cases": [case]}, prediction)

    assert result["overall"]["exact_match_count"] == 1
    assert result["overall"]["unsupported_predicted_event_count"] == 1
    assert result["passed"] is False


def test_subject_precision_is_independent_of_type_status_and_evidence() -> None:
    case = _case()
    prediction = {
        "dataset_version": "v1",
        "status": "complete",
        "summary": {
            "uncited_model_event_count": 0,
            "bad_claim_pair_event_count": 0,
            "strict_ready_article_count": 1,
        },
        "results": [
            {
                "key": "source:1",
                "events": [
                    {
                        "canonical_company": "甲公司",
                        "event_type": "partnership",
                        "event_status": "target",
                        "claim_ids": ["different"],
                        "evidence_quotes": ["完全不同的句子"],
                    }
                ],
                "audit": {
                    "deterministic_rejected_claim_ids": ["c_1", "c_2"]
                },
            }
        ],
    }

    result = evaluate({"dataset_version": "v1", "cases": [case]}, prediction)

    assert result["overall"]["company_subject_precision"] == 1.0
    assert result["overall"]["event_support_precision"] == 0.0
    assert result["overall"]["exact_match_count"] == 0


def test_subject_identity_uses_article_local_explicit_alias_graph() -> None:
    case = _case()
    case["clean_body"] = (
        "杭州甲辰科技有限公司（以下简称“甲辰科技”）宣布完成A轮融资。"
    )
    case["title"] = "甲辰科技完成融资"
    event = case["annotation"]["gold_events"][0]
    event["canonical_company"] = "杭州甲辰科技有限公司"
    event["evidence_span"] = {
        "char_start": 0,
        "char_end": len(case["clean_body"]),
        "text": case["clean_body"],
    }
    prediction = {
        "dataset_version": "v1",
        "status": "complete",
        "summary": {
            "uncited_model_event_count": 0,
            "bad_claim_pair_event_count": 0,
            "strict_ready_article_count": 1,
        },
        "results": [
            {
                "key": "source:1",
                "events": [
                    {
                        "canonical_company": "甲辰科技",
                        "event_type": "partnership",
                        "event_status": "target",
                        "claim_ids": ["different"],
                        "evidence_quotes": ["不同证据"],
                    }
                ],
                "audit": {
                    "deterministic_rejected_claim_ids": ["c_1", "c_2"]
                },
            }
        ],
    }

    result = evaluate({"dataset_version": "v1", "cases": [case]}, prediction)

    assert result["overall"]["company_subject_precision"] == 1.0
    assert result["overall"]["event_support_precision"] == 0.0
