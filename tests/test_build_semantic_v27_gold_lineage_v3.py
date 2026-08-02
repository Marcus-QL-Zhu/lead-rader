from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.build_semantic_v27_gold_lineage_v3 import (
    DATASET_VERSION,
    PARENT_DATASET_VERSION,
    build,
)


def _parent() -> dict:
    media_event = {
        "canonical_company": "北京华夏视觉科技集团有限公司",
        "event_type": "partnership",
        "evidence_span": {"text": "全资子公司拟共同投资基金", "char_start": 0, "char_end": 12},
    }
    return {
        "dataset_version": PARENT_DATASET_VERSION,
        "cases": [
            {
                "key": "nbd-vcpe-weekly:4482544",
                "annotation": {
                    "gold_events": [
                        deepcopy(media_event),
                        {"canonical_company": "曦诺未来", "event_type": "funding"},
                    ],
                    "review_exclusions": [],
                    "lineage_changes": [],
                },
                "adjudication_audit": {
                    "parent_event_count": 3,
                    "kept_parent_event_count": 2,
                    "dropped_parent_event_count": 1,
                    "added_atomic_event_count": 0,
                },
            },
            {
                "key": "pedaily-investment-news:564315",
                "annotation": {"gold_events": []},
                "adjudication_audit": {
                    "parent_event_count": 2,
                    "kept_parent_event_count": 2,
                    "dropped_parent_event_count": 0,
                    "added_atomic_event_count": 0,
                },
            },
        ],
    }


def test_build_removes_only_media_subsidiary_event_and_refreshes_audits() -> None:
    result = build(_parent(), parent_file="parent.json", parent_sha256="abc")

    assert result["dataset_version"] == DATASET_VERSION
    assert result["lineage"]["removed_event_count"] == 1
    assert result["lineage"]["final_event_count"] == 1
    target = result["cases"][0]
    assert [row["canonical_company"] for row in target["annotation"]["gold_events"]] == [
        "曦诺未来"
    ]
    assert target["adjudication_audit"]["kept_parent_event_count"] == 1
    assert target["adjudication_audit"]["dropped_parent_event_count"] == 2
    stale = result["cases"][1]["adjudication_audit"]
    assert stale["kept_parent_event_count"] == 0
    assert stale["dropped_parent_event_count"] == 2


def test_build_rejects_unexpected_parent_version() -> None:
    parent = _parent()
    parent["dataset_version"] = "wrong"
    with pytest.raises(ValueError, match="unexpected parent"):
        build(parent, parent_file="parent.json", parent_sha256="abc")
