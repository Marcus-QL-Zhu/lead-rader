from __future__ import annotations

from argparse import Namespace

import pytest

from scripts.run_semantic_v27_prompt_loop import _keys
from scripts.semantic_v27_prompt_variants import build_variant


def test_prompt_variants_extend_parent_and_remain_distinct() -> None:
    first = build_variant(round_number=1, variant="a")
    second = build_variant(round_number=2, variant="b", parent=first)

    assert first["prompt_version"].endswith("r1-a")
    assert second["prompt_version"].endswith("r2-b")
    assert second["lineage"]["parent_prompt_version"] == first["prompt_version"]
    assert len(second["few_shot"]["examples"]) == 3
    assert first["system_prompt"] in second["system_prompt"]


def test_prompt_variant_rejects_invalid_coordinates() -> None:
    with pytest.raises(ValueError):
        build_variant(round_number=6, variant="a")
    with pytest.raises(ValueError):
        build_variant(round_number=4, variant="a")
    with pytest.raises(ValueError):
        build_variant(round_number=1, variant="z")


def test_prompt_loop_selects_frozen_round_or_explicit_keys() -> None:
    split = {"rounds": [{"round": 1, "training_keys": ["a", "b", "c"]}]}
    assert _keys(Namespace(key=None, round=1), split) == ["a", "b", "c"]
    assert _keys(Namespace(key=["x", "x", "y"], round=1), split) == ["x", "y"]
