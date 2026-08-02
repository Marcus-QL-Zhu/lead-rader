from __future__ import annotations

from scripts.evaluate_semantic_v27_development import (
    _evaluation_keys,
    evaluate_development,
    evaluate_packet,
)
from tests.test_evaluate_semantic_v25_final import _case


def _prediction() -> dict:
    body = "甲公司宣布完成A轮融资。"
    return {
        "dataset_version": "v1",
        "purpose": "opened-formal-v1-development-error-set-only",
        "status": "complete",
        "results": [
            {
                "key": "source:1",
                "events": [
                    {
                        "canonical_company": "甲公司",
                        "event_type": "funding",
                        "event_status": "completed",
                        "subject_entity_id": "ae_1",
                        "claim_ids": ["ac_1"],
                        "span_ids": ["as_1"],
                        "evidence_quotes": [body],
                    }
                ],
                "audit": {
                    "candidate_count": 1,
                    "accepted_claim_ids": ["ac_1"],
                    "rejected_claim_ids": [],
                    "failed_claim_ids": [],
                    "host_fallback_claim_ids": [],
                    "strict_claim_contract_ready": True,
                },
            }
        ],
    }


def test_v27_development_evaluator_ignores_legacy_candidate_ids() -> None:
    gold = {"dataset_version": "v1", "cases": [_case()]}
    result = evaluate_development(gold, _prediction())

    assert result["passed"] is True
    assert result["legacy_candidate_dispositions"]["status"] == "not_applicable"
    assert result["claim_contract"]["accepted_claim_count"] == 1


def test_v27_development_evaluator_fails_unsupported_event() -> None:
    gold = {"dataset_version": "v1", "cases": [_case()]}
    prediction = _prediction()
    prediction["results"][0]["events"].append(
        {
            **prediction["results"][0]["events"][0],
            "event_type": "partnership",
        }
    )

    result = evaluate_development(gold, prediction)

    assert result["passed"] is False
    assert result["gates"]["no_unsupported_predicted_events"] is False


def test_v27_true_negative_holdout_case_passes_metric_gates() -> None:
    import copy

    gold = {"dataset_version": "v1", "cases": [_case()]}
    gold = copy.deepcopy(gold)
    gold["cases"][0]["candidates"] = []
    gold["cases"][0]["annotation"]["gold_events"] = []
    gold["cases"][0]["annotation"]["candidate_dispositions"] = []
    prediction = _prediction()
    prediction["results"][0]["events"] = []
    prediction["results"][0]["audit"]["candidate_count"] = 0
    prediction["results"][0]["audit"]["accepted_claim_ids"] = []
    prediction["results"][0]["audit"]["strict_claim_contract_ready"] = True

    result = evaluate_packet(gold, prediction)

    assert result["passed"] is True
    assert result["gates"]["company_subject_precision_at_least_98pct"] is True
    assert result["gates"]["strong_current_recall_at_least_90pct"] is True
    assert result["gates"]["status_accuracy_at_least_90pct"] is True


def test_v27_frozen_reserve_purpose_is_preserved() -> None:
    gold = {"dataset_version": "v1", "cases": [_case()]}
    prediction = _prediction()
    prediction["purpose"] = "reserve-v1-one-time-prevalidation"

    result = evaluate_packet(gold, prediction)

    assert result["passed"] is True
    assert result["purpose"] == "reserve-v1-one-time-prevalidation"


def test_v27_second_opened_development_purpose_is_supported() -> None:
    gold = {"dataset_version": "v1", "cases": [_case()]}
    prediction = _prediction()
    prediction["purpose"] = "opened-semantic-v27-development-v2"

    result = evaluate_packet(gold, prediction)

    assert result["purpose"] == (
        "opened-development-evaluation-only-not-independent-test"
    )


def test_v27_prompt_loop_purposes_are_supported() -> None:
    gold = {"dataset_version": "v1", "cases": [_case()]}
    for purpose, expected in (
        (
            "opened-semantic-v27-prompt-loop-training",
            "opened-development-prompt-loop-training-only",
        ),
        (
            "opened-semantic-v27-prompt-loop-holdout",
            "opened-development-company-isolated-holdout",
        ),
    ):
        prediction = _prediction()
        prediction["purpose"] = purpose
        assert evaluate_packet(gold, prediction)["purpose"] == expected


def test_evaluator_uses_frozen_prediction_keys_unless_explicitly_overridden() -> None:
    prediction = {"selected_keys": ["a", "b", "a"]}

    assert _evaluation_keys(prediction, None) == ["a", "b"]
    assert _evaluation_keys(prediction, ["c", "c"]) == ["c"]
