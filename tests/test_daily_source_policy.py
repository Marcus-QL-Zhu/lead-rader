import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_company_official_sources_are_never_enabled():
    payload = json.loads(
        (ROOT / "config" / "fixed-sources.json").read_text(encoding="utf-8")
    )

    assert not [
        source
        for source in payload["sources"]
        if source.get("company") and source.get("enabled")
    ]
    assert not any(
        source.get("id") == "linkerbot-official"
        for source in payload["pending_sources"]
    )


def test_all_dedicated_aggregate_channels_are_registered_in_source_packs():
    from ht_lead_radar.aggregate_adapters.registry import (
        DedicatedAdapterRegistry,
    )

    payload = json.loads(
        (ROOT / "config" / "source-packs.json").read_text(encoding="utf-8")
    )
    configured = {source["id"] for source in payload["sources"]}

    assert DedicatedAdapterRegistry.defaults().source_ids <= configured
