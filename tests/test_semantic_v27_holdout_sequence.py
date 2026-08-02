from __future__ import annotations

from scripts.evaluate_semantic_v27_holdout_sequence import evaluate_sequence
from scripts.select_semantic_v27_holdout import select_holdout
from tests.test_evaluate_semantic_v27_development import _prediction
from tests.test_evaluate_semantic_v25_final import _case


def test_holdout_selection_is_reproducible_and_unique() -> None:
    split = {
        "dataset_version": "split-v1",
        "holdout_keys": ["a", "b", "c", "d", "e"],
    }
    first = select_holdout(split, prompt_config_sha256="hash")
    second = select_holdout(split, prompt_config_sha256="hash")

    assert first == second
    assert len(first["selected_keys"]) == 3
    assert len(set(first["selected_keys"])) == 3


def test_holdout_sequence_requires_each_case_to_pass() -> None:
    keys = ["source:1", "source:2", "source:3"]
    gold_cases = []
    results = []
    for key in keys:
        case = _case()
        case["key"] = key
        gold_cases.append(case)
        result = _prediction()["results"][0]
        result = {**result, "key": key}
        results.append(result)
    prediction = {
        **_prediction(),
        "purpose": "opened-semantic-v27-prompt-loop-holdout",
        "selected_keys": keys,
        "results": results,
    }
    manifest = {
        "required_consecutive_passes": 3,
        "selected_keys": keys,
    }

    evaluated = evaluate_sequence(
        {"dataset_version": "v1", "cases": gold_cases},
        prediction,
        manifest,
    )

    assert evaluated["passed"] is True
    assert evaluated["final_consecutive_passes"] == 3
