"""Development-only calibration for the historical role-family ranker.

This module deliberately accepts only train and calibration rows.  The frozen
test partition and holdout-v15 are outside the calibration loop.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .historical_training import (
    HistoricalTrainingRow,
    LogisticModel,
    RuleRoleModel,
    evaluate_role_ranker,
    fit_logistic_regression,
)


DEVELOPMENT_SPLITS = frozenset({"train", "calibration"})
FEATURE_POLICIES = frozenset({"full", "portable"})
PRIMARY_METRICS = (
    "top1_accuracy",
    "precision_at_20",
    "macro_f1_top1",
)


def _portable_feature(name: str) -> bool:
    """Keep transferable evidence/role features and remove source identities."""

    if name.startswith("source_group:"):
        return name == "source_group:independent_count"
    return not name.startswith("company_type:")


def apply_feature_policy(
    rows: Sequence[HistoricalTrainingRow],
    policy: str,
) -> tuple[HistoricalTrainingRow, ...]:
    if policy not in FEATURE_POLICIES:
        raise ValueError(f"unsupported feature policy: {policy}")
    if policy == "full":
        return tuple(rows)
    return tuple(
        replace(
            row,
            features={
                name: value
                for name, value in row.features.items()
                if _portable_feature(name)
            },
        )
        for row in rows
    )


@dataclass(frozen=True)
class BlendedRoleModel:
    """Blend a learned ranker with the transparent signal-to-role prior."""

    logistic: LogisticModel
    learned_weight: float
    feature_policy: str
    rule: RuleRoleModel = RuleRoleModel()

    def __post_init__(self) -> None:
        if not 0.0 <= self.learned_weight <= 1.0:
            raise ValueError("learned_weight must be between 0 and 1")
        if self.feature_policy not in FEATURE_POLICIES:
            raise ValueError("unsupported feature policy")

    def score(self, features: Mapping[str, float]) -> float:
        if self.feature_policy == "portable":
            model_features = {
                name: value
                for name, value in features.items()
                if _portable_feature(name)
            }
        else:
            model_features = features
        learned = self.logistic.score(model_features)
        prior = self.rule.score(model_features)
        return self.learned_weight * learned + (1.0 - self.learned_weight) * prior

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "logistic_rule_blend",
            "feature_policy": self.feature_policy,
            "learned_weight": self.learned_weight,
            "logistic": self.logistic.to_dict(),
        }


def _metric_improvements(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    tolerance: float = 1e-12,
) -> tuple[str, ...]:
    return tuple(
        metric
        for metric in PRIMARY_METRICS
        if candidate.get(metric) is not None
        and baseline.get(metric) is not None
        and float(candidate[metric]) > float(baseline[metric]) + tolerance
    )


def _largest_slice_regression(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> float:
    candidate_slices = candidate.get("company_type_slices") or {}
    baseline_slices = baseline.get("company_type_slices") or {}
    regressions = [
        float(baseline_values["top1_accuracy"])
        - float(candidate_slices[company_type]["top1_accuracy"])
        for company_type, baseline_values in baseline_slices.items()
        if company_type in candidate_slices
    ]
    return max(regressions, default=0.0)


def candidate_passes_gate(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    minimum_improved_metrics: int = 2,
    maximum_slice_regression: float = 0.125,
) -> tuple[bool, dict[str, Any]]:
    improvements = _metric_improvements(candidate, baseline)
    slice_regression = _largest_slice_regression(candidate, baseline)
    details = {
        "improved_primary_metrics": list(improvements),
        "improved_primary_metric_count": len(improvements),
        "largest_company_type_top1_regression": slice_regression,
        "minimum_improved_metrics": minimum_improved_metrics,
        "maximum_slice_regression": maximum_slice_regression,
    }
    return (
        len(improvements) >= minimum_improved_metrics
        and slice_regression <= maximum_slice_regression,
        details,
    )


def _selection_key(item: Mapping[str, Any]) -> tuple[float, ...]:
    metrics = item["metrics"]
    gate = item["gate"]
    return (
        float(gate["improved_primary_metric_count"]),
        float(metrics.get("top1_accuracy") or 0.0),
        float(metrics.get("macro_f1_top1") or 0.0),
        float(metrics.get("precision_at_20") or 0.0),
        float(metrics.get("mean_reciprocal_rank") or 0.0),
        -float(metrics.get("brier_score") or 1.0),
    )


def run_calibration(
    rows: Sequence[HistoricalTrainingRow],
    *,
    l2_values: Sequence[float],
    learned_weights: Sequence[float],
    feature_policies: Sequence[str],
    iterations: int = 800,
    learning_rate: float = 0.05,
    top_k: int = 5,
    maximum_slice_regression: float = 0.125,
) -> dict[str, Any]:
    """Run a fixed grid without reading or evaluating test labels."""

    development_rows = tuple(
        row for row in rows if row.split in DEVELOPMENT_SPLITS
    )
    if not development_rows:
        raise ValueError("no train/calibration rows available")
    if any(policy not in FEATURE_POLICIES for policy in feature_policies):
        raise ValueError("unsupported feature policy in grid")

    baseline_rows = apply_feature_policy(development_rows, "full")
    baseline_model = fit_logistic_regression(
        baseline_rows,
        l2=0.1,
        learning_rate=learning_rate,
        iterations=iterations,
    )
    baseline_metrics = evaluate_role_ranker(
        baseline_model,
        baseline_rows,
        split="calibration",
        top_k=top_k,
    )
    rule_metrics = evaluate_role_ranker(
        RuleRoleModel(),
        baseline_rows,
        split="calibration",
        top_k=top_k,
    )

    candidates: list[dict[str, Any]] = []
    models: dict[str, BlendedRoleModel] = {}
    for policy in feature_policies:
        policy_rows = apply_feature_policy(development_rows, policy)
        for l2 in l2_values:
            logistic = fit_logistic_regression(
                policy_rows,
                l2=float(l2),
                learning_rate=learning_rate,
                iterations=iterations,
            )
            for learned_weight in learned_weights:
                candidate_id = (
                    f"{policy}-l2-{float(l2):g}-learned-"
                    f"{float(learned_weight):g}"
                )
                model = BlendedRoleModel(
                    logistic=logistic,
                    learned_weight=float(learned_weight),
                    feature_policy=policy,
                )
                metrics = evaluate_role_ranker(
                    model,
                    policy_rows,
                    split="calibration",
                    top_k=top_k,
                )
                passed, gate = candidate_passes_gate(
                    metrics,
                    baseline_metrics,
                    maximum_slice_regression=maximum_slice_regression,
                )
                candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "feature_policy": policy,
                        "l2": float(l2),
                        "learned_weight": float(learned_weight),
                        "metrics": metrics,
                        "passed_gate": passed,
                        "gate": gate,
                    }
                )
                models[candidate_id] = model

    passing = [item for item in candidates if item["passed_gate"]]
    selected = max(passing, key=_selection_key) if passing else None
    selected_id = selected["candidate_id"] if selected else None
    return {
        "scope": {
            "development_splits": sorted(DEVELOPMENT_SPLITS),
            "test_labels_accessed": False,
            "holdout_v15_accessed_for_model_selection": False,
            "propensity_model_trained": False,
        },
        "baseline": {
            "candidate_id": "frozen-current-logistic",
            "feature_policy": "full",
            "l2": 0.1,
            "learned_weight": 1.0,
            "metrics": baseline_metrics,
        },
        "rule_baseline": rule_metrics,
        "candidates": candidates,
        "decision": {
            "promoted": selected is not None,
            "selected_candidate_id": selected_id,
            "reason": (
                "candidate_passed_preregistered_gate"
                if selected
                else "no_candidate_passed_preregistered_gate"
            ),
        },
        "selected_model": models[selected_id].to_dict() if selected_id else None,
    }


__all__ = [
    "BlendedRoleModel",
    "DEVELOPMENT_SPLITS",
    "FEATURE_POLICIES",
    "PRIMARY_METRICS",
    "apply_feature_policy",
    "candidate_passes_gate",
    "run_calibration",
]
