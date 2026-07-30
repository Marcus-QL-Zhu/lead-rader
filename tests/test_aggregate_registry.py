from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry


def test_default_registry_contains_all_first_wave_adapters_and_channels():
    registry = DedicatedAdapterRegistry.defaults()

    assert registry.adapter_ids == (
        "cls",
        "cyzone",
        "jazzyear",
        "kr36",
        "lieyun",
        "miit",
        "pedaily",
        "stcn",
        "vbdata",
        "zhidx",
    )
    assert registry.source_ids == frozenset(
        {
            "36kr-financing-flash",
            "pedaily-vcpe-events",
            "pedaily-investment-news",
            "cyzone-financing",
            "cyzone-latest",
            "lieyunpro-archives",
            "vbdata-funding",
            "jazzyear-latest",
            "zhidx-financing",
            "cls-telegraph",
            "stcn-flash",
            "miit-science-files",
        }
    )

    for source_id in registry.source_ids:
        assert registry.for_source(source_id) is not None
