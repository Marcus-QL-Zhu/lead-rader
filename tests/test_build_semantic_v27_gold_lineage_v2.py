from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.build_semantic_v27_gold_lineage_v2 import (
    DATASET_VERSION,
    PARENT_DATASET_VERSION,
    build,
)


def _parent() -> dict:
    evidence = {
        "text": (
            "由清科控股、投资界主办，吴中金控集团联合主办的"
            "“2026投资界SuperLink大会”将启幕。"
        ),
        "char_start": 0,
        "char_end": 49,
    }
    return {
        "dataset_version": PARENT_DATASET_VERSION,
        "cases": [
            {
                "key": "pedaily-investment-news:564315",
                "annotation": {
                    "gold_events": [
                        {
                            "canonical_company": company,
                            "event_type": "partnership",
                            "evidence_span": deepcopy(evidence),
                        }
                        for company in ("清科控股", "吴中金控集团")
                    ],
                    "review_exclusions": [],
                    "lineage_changes": [],
                },
            },
            {
                "key": "other:1",
                "annotation": {
                    "gold_events": [{"canonical_company": "甲科技"}],
                },
            },
        ],
    }


def test_build_removes_only_out_of_scope_financial_hosting_events() -> None:
    result = build(_parent(), parent_file="parent.json", parent_sha256="abc")

    assert result["dataset_version"] == DATASET_VERSION
    assert result["lineage"]["removed_event_count"] == 2
    assert result["lineage"]["final_event_count"] == 1
    target = result["cases"][0]["annotation"]
    assert target["gold_events"] == []
    assert len(target["review_exclusions"]) == 2
    assert {
        row["reason_code"] for row in target["lineage_changes"]
    } == {"non_hardtech_financial_hosting_subject"}
    assert result["cases"][1]["annotation"]["gold_events"] == [
        {"canonical_company": "甲科技"}
    ]


def test_build_rejects_unexpected_parent_version() -> None:
    parent = _parent()
    parent["dataset_version"] = "wrong"
    with pytest.raises(ValueError, match="unexpected parent"):
        build(parent, parent_file="parent.json", parent_sha256="abc")
