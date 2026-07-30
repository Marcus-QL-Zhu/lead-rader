from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.source_packs import load_source_packs


def test_all_dedicated_channels_are_enabled_in_generic_source_pack():
    dedicated = DedicatedAdapterRegistry.defaults().source_ids
    selection = load_source_packs().select("任意硬科技方向")
    scheduled = {source.id for source in selection.sources}

    assert dedicated <= scheduled
