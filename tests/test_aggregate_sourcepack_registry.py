from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.daily_topics import DEFAULT_DIRECTIONS
from ht_lead_radar.source_packs import load_source_packs


def test_all_dedicated_channels_are_enabled_in_generic_source_pack():
    dedicated = DedicatedAdapterRegistry.defaults().source_ids
    selection = load_source_packs().select("|".join(DEFAULT_DIRECTIONS))
    scheduled = {source.id for source in selection.sources}

    assert dedicated <= scheduled
