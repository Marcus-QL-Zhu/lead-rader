from __future__ import annotations

from dataclasses import replace

from ht_lead_radar.calibration import (
    apply_feature_policy,
    candidate_passes_gate,
    run_calibration,
)
from ht_lead_radar.historical_training import HistoricalTrainingRow


def _row(
    *,
    company: str,
    split: str,
    role: str,
    label: str,
    event: str,
    company_type: str = "listed",
) -> HistoricalTrainingRow:
    features = {
        f"role:{role}": 1.0,
        f"company_type:{company_type}": 1.0,
        f"event:{event}:recency": 1.0,
        f"event_role:{event}:{role}": 1.0,
        "source_group:publisher-name": 1.0,
        "source_group:independent_count": 1.0,
    }
    return HistoricalTrainingRow(
        sample_id=f"{company}-{split}-{role}",
        company_id=company,
        company=company,
        company_type=company_type,
        split=split,
        cutoff="2025-04-30",
        horizon_end="2025-07-29",
        role_family=role,
        label=label,
        label_weight=1.0 if label == "positive" else 0.25,
        observability="replayable" if label == "positive" else "search_only",
        evidence_ids=(),
        matched_job_ids=(),
        features=features,
        row_sha256="a" * 64,
    )


def _rows() -> tuple[HistoricalTrainingRow, ...]:
    values = []
    for company, split in (
        ("train-a", "train"),
        ("train-b", "train"),
        ("calib", "calibration"),
    ):
        values.extend(
            (
                _row(
                    company=company,
                    split=split,
                    role="manufacturing",
                    label="positive",
                    event="factory_or_capacity",
                ),
                _row(
                    company=company,
                    split=split,
                    role="marketing",
                    label="contrastive_negative",
                    event="factory_or_capacity",
                ),
            )
        )
    return tuple(values)


def test_portable_policy_removes_source_and_company_identity() -> None:
    row = apply_feature_policy(_rows()[:1], "portable")[0]
    assert "company_type:listed" not in row.features
    assert "source_group:publisher-name" not in row.features
    assert row.features["source_group:independent_count"] == 1.0
    assert row.features["event_role:factory_or_capacity:manufacturing"] == 1.0


def test_gate_requires_two_metric_improvements_and_slice_stability() -> None:
    baseline = {
        "top1_accuracy": 0.2,
        "precision_at_20": 0.2,
        "macro_f1_top1": 0.1,
        "company_type_slices": {
            "listed": {"top1_accuracy": 0.2},
        },
    }
    candidate = {
        "top1_accuracy": 0.3,
        "precision_at_20": 0.3,
        "macro_f1_top1": 0.1,
        "company_type_slices": {
            "listed": {"top1_accuracy": 0.2},
        },
    }
    passed, details = candidate_passes_gate(candidate, baseline)
    assert passed is True
    assert details["improved_primary_metric_count"] == 2

    candidate["company_type_slices"]["listed"]["top1_accuracy"] = 0.0
    passed, _details = candidate_passes_gate(candidate, baseline)
    assert passed is False


def test_calibration_never_evaluates_test_rows() -> None:
    rows = list(_rows())
    poison = replace(
        rows[0],
        sample_id="test-poison",
        company_id="test-poison",
        company="test-poison",
        split="test",
        label="positive",
    )
    result = run_calibration(
        [*rows, poison],
        l2_values=(0.1,),
        learned_weights=(1.0,),
        feature_policies=("full",),
        iterations=20,
    )
    assert result["scope"]["test_labels_accessed"] is False
    assert result["scope"]["holdout_v15_accessed_for_model_selection"] is False
    assert result["baseline"]["metrics"]["split"] == "calibration"
    assert result["baseline"]["metrics"]["evaluated_company_cutoffs"] == 1
