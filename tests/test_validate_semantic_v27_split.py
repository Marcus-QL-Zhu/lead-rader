from __future__ import annotations

from scripts.validate_semantic_v27_split import validate_split


def _gold() -> dict:
    return {
        "cases": [
            {
                "key": f"s:{index}",
                "annotation": {
                    "gold_events": (
                        [{"canonical_company": f"公司{index}"}]
                        if index <= 18
                        else []
                    )
                },
            }
            for index in range(1, 31)
        ]
    }


def _split() -> dict:
    return {
        "source_bundle_sha256": "bundle",
        "source_gold_sha256": "gold",
        "rounds": [
            {
                "round": index,
                "training_keys": [
                    f"s:{(index - 1) * 3 + offset}" for offset in range(1, 4)
                ],
            }
            for index in range(1, 4)
        ],
        "holdout_keys": [f"s:{index}" for index in range(16, 21)],
        "reserve_keys": [
            *[f"s:{index}" for index in range(10, 16)],
            *[f"s:{index}" for index in range(21, 31)],
        ],
        "constraints": {"maximum_rounds": 3},
    }


def test_split_validator_accepts_disjoint_complete_partition() -> None:
    result = validate_split(
        _split(), _gold(), bundle_sha256="bundle", gold_sha256="gold"
    )

    assert result["status"] == "PASS"
    assert result["training_holdout_company_overlap"] == []


def test_split_validator_detects_company_leakage() -> None:
    gold = _gold()
    gold["cases"][15]["annotation"]["gold_events"] = [
        {"canonical_company": "公司1"}
    ]
    result = validate_split(
        _split(), gold, bundle_sha256="bundle", gold_sha256="gold"
    )

    assert result["status"] == "FAIL"
    assert result["gates"]["training_holdout_company_isolated"] is False
