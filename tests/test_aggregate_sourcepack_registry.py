from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.daily_topics import DEFAULT_DIRECTIONS
from ht_lead_radar.source_packs import load_source_packs


def test_all_dedicated_channels_are_scheduled_or_explicitly_disabled():
    dedicated = DedicatedAdapterRegistry.defaults().source_ids
    selection = load_source_packs().select("|".join(DEFAULT_DIRECTIONS))
    scheduled = {source.id for source in selection.sources}
    disabled = {source.id for source in selection.disabled_sources}

    assert dedicated <= scheduled | disabled
    assert dedicated - scheduled == {"caict-mobile-market-analysis"}
