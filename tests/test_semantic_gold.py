from __future__ import annotations

from ht_lead_radar.semantic_gold import (
    compare_primary_annotations,
    validate_gold_case,
)


def _case() -> dict:
    body = "Company Alpha completed funding."
    return {
        "key": "source:1",
        "clean_body": body,
        "candidates": [{"required_claim_ids": ["claim-1"]}],
        "annotation": {
            "annotation_status": "complete",
            "candidate_dispositions": [
                {
                    "claim_id": "claim-1",
                    "disposition": "accepted",
                    "reason_code": "current_completed_event",
                }
            ],
            "gold_events": [
                {
                    "canonical_company": "Company Alpha",
                    "event_type": "funding",
                    "event_status": "completed",
                    "importance": "strong",
                    "claim_ids": ["claim-1"],
                    "evidence_span": {
                        "text": body,
                        "char_start": 0,
                        "char_end": len(body),
                    },
                }
            ],
        },
    }


def test_valid_gold_case_requires_exact_span_and_complete_claim_disposition() -> None:
    assert validate_gold_case(_case()) == []


def test_gold_case_fails_when_evidence_is_not_an_exact_span() -> None:
    case = _case()
    case["annotation"]["gold_events"][0]["evidence_span"]["text"] = "invented"

    assert "gold_event_non_exact_span" in validate_gold_case(case)


def test_primary_comparison_reports_event_disagreement() -> None:
    first = {"cases": [_case()]}
    changed = _case()
    changed["annotation"]["gold_events"][0]["event_status"] = "started"
    second = {"cases": [changed]}

    result = compare_primary_annotations(first, second)

    assert result["disagreement_keys"] == ["source:1"]


def test_primary_comparison_reports_candidate_disposition_disagreement() -> None:
    first_case = _case()
    first_case["annotation"]["candidate_dispositions"][0] = {
        "claim_id": "claim-1",
        "disposition": "ambiguous",
        "reason_code": "unclear_currentness",
    }
    first_case["annotation"]["gold_events"] = []
    second_case = _case()
    second_case["annotation"]["candidate_dispositions"][0] = {
        "claim_id": "claim-1",
        "disposition": "rejected",
        "reason_code": "historical_background",
    }
    second_case["annotation"]["gold_events"] = []

    result = compare_primary_annotations(
        {"cases": [first_case]},
        {"cases": [second_case]},
    )

    assert result["disagreement_keys"] == ["source:1"]
