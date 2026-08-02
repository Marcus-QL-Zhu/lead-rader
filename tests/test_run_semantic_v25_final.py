from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_semantic_v25_final import _contract_sha256, _output_lock


def test_final_runner_contract_hash_is_stable_and_content_addressed() -> None:
    first = _contract_sha256()
    second = _contract_sha256()

    assert first == second
    assert len(first) == 64


def test_final_runner_output_lock_rejects_concurrent_writer(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "prediction.json"

    with _output_lock(output):
        with pytest.raises(RuntimeError, match="already locked"):
            with _output_lock(output):
                raise AssertionError("unreachable")

    assert not output.with_suffix(".json.lock").exists()
