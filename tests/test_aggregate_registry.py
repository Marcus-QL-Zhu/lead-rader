from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry


def test_default_registry_contains_all_first_wave_adapters_and_channels():
    registry = DedicatedAdapterRegistry.defaults()

    assert registry.adapter_ids == (
        "beijing_etown_major_projects",
        "caict_market_analysis",
        "ccid_report_commentary",
        "cena",
        "cls",
        "cnstock_company_channel",
        "cyzone",
        "fusion_industry_media",
        "iter_china",
        "jazzyear",
        "jazzyear_research",
        "jiqizhixin",
        "kr36",
        "lieyun",
        "miit",
        "nbd-vcpe-weekly",
        "pedaily",
        "shanghai_fgw_annual_plan",
        "shenzhen_sasac_appointments",
        "shenzhen_semiconductor_association",
        "stcn",
        "suzhou_robot_association",
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
            "cnstock-company-channel",
            "stcn-flash",
            "miit-science-files",
            "iter-china-news",
            "fusion-industry-media",
            "suzhou-robot-association",
            "beijing-etown-major-projects",
            "shenzhen-semiconductor-association",
            "shenzhen-sasac-appointments",
            "caict-mobile-market-analysis",
            "ccid-report-commentary",
            "cena-industry-analysis",
            "jiqizhixin-industry-analysis",
            "jazzyear-research",
            "shanghai-fgw-annual-plan",
            "nbd-vcpe-weekly",
        }
    )

    for source_id in registry.source_ids:
        assert registry.for_source(source_id) is not None
