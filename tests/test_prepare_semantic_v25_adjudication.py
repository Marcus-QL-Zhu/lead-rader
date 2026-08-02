from __future__ import annotations

from copy import deepcopy

from scripts.prepare_semantic_v25_adjudication import build_adjudication
from tests.test_semantic_gold import _case


def _packet(case: dict) -> dict:
    return {
        "dataset_version": "v1",
        "instructions": "guide.md",
        "source_manifest_sha256": "manifest",
        "source_bundle_sha256": "bundle",
        "cases": [case],
    }


def test_build_adjudication_preserves_agreed_case() -> None:
    case = _case()

    result = build_adjudication(_packet(case), _packet(deepcopy(case)))

    assert result["status"] == "complete"
    assert result["cases"][0]["annotation"]["annotation_status"] == "complete"
    assert result["cases"][0]["adjudication_audit"]["resolution"] == (
        "primary_agreement"
    )


def test_build_adjudication_blanks_only_disagreed_case() -> None:
    first = _case()
    second = deepcopy(first)
    second["annotation"]["candidate_dispositions"][0] = {
        "claim_id": "claim-1",
        "disposition": "rejected",
        "reason_code": "historical_background",
    }
    second["annotation"]["gold_events"] = []

    result = build_adjudication(_packet(first), _packet(second))

    case = result["cases"][0]
    assert result["status"] == "awaiting_adjudication"
    assert case["annotation"]["annotation_status"] == "unlabelled"
    assert set(case["primary_annotations"]) == {"annotator_a", "annotator_b"}
