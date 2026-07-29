from scripts.assess_dataset_readiness import assess


def row(
    company_id: str,
    split: str,
    role_family: str,
    *,
    label: str = "positive",
    observability: str = "partial",
):
    return {
        "company_id": company_id,
        "split": split,
        "role_family": role_family,
        "label": label,
        "observability": observability,
    }


def test_readiness_fails_when_test_has_no_positive_labels():
    result = assess(
        {
            "rows": [
                row("train-1", "train", "engineering"),
                row("cal-1", "calibration", "engineering"),
            ]
        },
        {
            "train_positive_companies": 1,
            "calibration_positive_companies": 1,
            "test_positive_companies": 1,
            "positive_role_families": 1,
            "replayable_negative_rows": 0,
        },
    )
    assert result["ready"] is False
    assert result["checks"]["test_positive_companies"]["actual"] == 0
    assert result["headline_metrics_allowed"] is False


def test_readiness_separates_propensity_negative_gate():
    rows = [
        row("train-1", "train", "engineering"),
        row("cal-1", "calibration", "engineering"),
        row("test-1", "test", "engineering"),
        row(
            "test-2",
            "test",
            "engineering",
            label="negative",
            observability="replayable",
        ),
    ]
    result = assess(
        {"rows": rows},
        {
            "train_positive_companies": 1,
            "calibration_positive_companies": 1,
            "test_positive_companies": 1,
            "positive_role_families": 1,
            "replayable_negative_rows": 1,
        },
    )
    assert result["ready"] is True
    assert result["propensity_training_allowed"] is True
