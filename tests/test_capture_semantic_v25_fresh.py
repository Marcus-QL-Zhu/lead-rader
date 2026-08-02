from pathlib import Path

import pytest

from ht_lead_radar.source_packs import SourcePackError
from scripts.capture_semantic_v25_fresh import _filtered_registry


def test_filtered_registry_keeps_only_requested_sources() -> None:
    registry = _filtered_registry(
        Path("config/source-packs.json"),
        ["nbd-vcpe-weekly", "fusion-industry-media", "nbd-vcpe-weekly"],
    )

    assert [source.id for source in registry.sources] == [
        "nbd-vcpe-weekly",
        "fusion-industry-media",
    ]
    assert registry.get_pack("generic-cn").source_ids == (
        "nbd-vcpe-weekly",
        "fusion-industry-media",
    )


def test_filtered_registry_rejects_unknown_source() -> None:
    with pytest.raises(SourcePackError, match="unknown source ids"):
        _filtered_registry(Path("config/source-packs.json"), ["missing-source"])
