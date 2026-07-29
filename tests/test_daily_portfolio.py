from ht_lead_radar.daily_portfolio import combine_sector_reports


def _report(direction, run_id, companies):
    return {
        "schema_version": 2,
        "manifest": {
            "as_of": "2026-07-29",
            "direction": direction,
            "run_id": run_id,
            "source_summary": {
                "runs": [{"source_id": f"{direction}-source"}],
                "failures": [],
                "normalization_exclusions": [],
                "adjacent_watchlist": [],
                "metaso_budget": {"used_points": 3},
            },
        },
        "leads": [
            {"company": company, "score": score, "evidence": []}
            for company, score in companies
        ],
    }


def test_portfolio_round_robins_sectors_and_deduplicates_companies():
    portfolio = combine_sector_reports(
        [
            _report("具身智能", "r1", [("甲", 90), ("共同公司", 80), ("乙", 70)]),
            _report("半导体", "r2", [("丙", 95), ("共同公司", 85), ("丁", 60)]),
        ],
        target_count=4,
    )

    assert [item["company"] for item in portfolio["leads"]] == [
        "丙",
        "甲",
        "共同公司",
        "丁",
    ]
    assert len({item["company"] for item in portfolio["leads"]}) == 4
    assert portfolio["manifest"]["direction"] == "硬科技组合"
    assert portfolio["manifest"]["portfolio"]["selected_company_count"] == 4
    summary = portfolio["manifest"]["source_summary"]
    assert len(summary["runs"]) == 2
    assert {item["portfolio_sector"] for item in summary["runs"]} == {
        "具身智能", "半导体"
    }


def test_portfolio_manifest_is_independent_and_records_child_manifests():
    portfolio = combine_sector_reports(
        [
            _report("具身智能", "r1", [("甲", 90)]),
            _report("半导体", "r2", [("乙", 80)]),
        ],
        target_count=2,
    )

    manifest = portfolio["manifest"]
    assert manifest["direction"] == "硬科技组合"
    assert manifest["mode"] == "balanced-hardtech-portfolio"
    assert manifest["request_plan"]["directions"] == ["具身智能", "半导体"]
    assert len(manifest["portfolio"]["child_manifests"]) == 2
    assert manifest["policy"]["company_official_daily_discovery"] is False
