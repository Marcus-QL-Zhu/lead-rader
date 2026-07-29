from scripts.assess_snapshot_training_readiness import assess


def test_provisional_search_labels_do_not_count_as_replayable_labels():
    historical = {
        "rows": [
            {
                "company": "历史公司",
                "split": "train",
                "label": "positive",
            }
        ]
    }
    snapshot = {
        "counts": {"evidence_companies": 1, "event_types": 1},
        "job_label_candidates": [
            {
                "company": "测试公司",
                "split": "test",
                "label_status": "verified_search_snapshot_candidate",
            }
        ],
    }
    result = assess(
        historical=historical,
        snapshot=snapshot,
        thresholds={
            "train_positive_companies": 1,
            "calibration_positive_companies": 0,
            "test_positive_companies": 1,
            "strict_precursor_evidence_companies": 1,
            "event_types": 1,
            "replayable_job_labels": 1,
        },
    )
    assert result["provisional_positive_companies"]["test"] == 1
    assert result["strict_historical_positive_companies"]["test"] == 0
    assert result["checks"]["replayable_job_labels"]["passed"] is False
    assert result["ready"] is False
