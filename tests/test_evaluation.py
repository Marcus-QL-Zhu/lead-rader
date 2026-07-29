from ht_lead_radar.evaluation import evaluate_acceptance
from ht_lead_radar.role_inference import roles_for
from ht_lead_radar.source_pack_collector import _EVENT_SIGNAL_COMPATIBILITY


def _report(cutoff: str, offset: int, company_type: str):
    families = [f"family-{index}" for index in range(8)]
    titles = [f"赛道{offset + index}具体岗位总监" for index in range(10)]
    role_keys = [f"family-{index % 8}:role-{offset + index}" for index in range(10)]
    matches = [
        {
            "status": "family_match" if index < 2 else "not_observed",
            "actual_job_id": f"job-{cutoff}-{index}" if index < 2 else "",
            "predicted_title": titles[index],
            "predicted_family": families[index % 8],
            "canonical_role_key": role_keys[index],
            "company_type": company_type,
        }
        for index in range(10)
    ]
    return {
        "manifest": {
            "cutoff": cutoff,
            "workforce_precursors_enabled": False,
            "prediction_inputs_exclude_job_ads": True,
            "snapshot_schema_version": 2,
        },
        "counts": {
            "predictions": 10,
            "role_matches": 2,
            "company_only_matches": 0,
            "not_observed": 8,
            "distinct_predicted_titles": 10,
            "distinct_predicted_role_families": 8,
            "distinct_canonical_role_keys": 10,
        },
        "distinct_predicted_titles": titles,
        "distinct_predicted_role_families": families,
        "distinct_canonical_role_keys": role_keys,
        "verified_company_types": [company_type],
        "matches": matches,
    }


def test_count_based_acceptance_requires_stability_diversity_and_three_company_types():
    result = evaluate_acceptance(
        [
            _report("2026-03-01", 0, "startup_private"),
            _report("2026-04-01", 10, "listed"),
            _report("2026-05-01", 20, "foreign"),
        ]
    )
    assert result["passed"] is True
    assert result["counts"] == {
        "historical_cutoffs": 3,
        "distinct_predicted_titles": 30,
        "distinct_predicted_role_families": 8,
        "distinct_canonical_role_keys": 30,
        "distinct_matched_jobs": 6,
        "verified_company_types": 3,
    }
    assert "percentage" not in str(result).lower()


def test_acceptance_fails_when_any_cutoff_has_no_stable_hits():
    reports = [
        _report("2026-03-01", 0, "startup_private"),
        _report("2026-04-01", 10, "listed"),
        _report("2026-05-01", 20, "foreign"),
    ]
    reports[1]["counts"]["role_matches"] = 1
    reports[1]["counts"]["not_observed"] = 9
    reports[1]["matches"][0]["status"] = "not_observed"
    reports[1]["matches"][0]["actual_job_id"] = ""
    result = evaluate_acceptance(reports)
    assert result["passed"] is False
    assert result["gates"]["stable_role_generation"] is False


def test_signal_to_role_surface_can_express_more_than_thirty_titles():
    signal_types = (
        "executive_change",
        "merger_acquisition",
        "joint_venture_or_spinout",
        "ipo_or_listing",
        "new_site_or_entity",
        "factory_or_capacity",
        "project_buildout",
        "project_call",
        "eia_or_permit",
        "procurement_intention",
        "procurement_tender",
        "major_order",
        "customer_validation",
        "funding",
        "global_expansion",
        "channel_expansion",
        "technical_milestone",
        "data_or_model",
        "regulatory_or_clinical",
        "research_or_ip",
        "enterprise_system",
        "partnership",
        "policy_or_standard",
    )
    titles = {
        role
        for signal_type in signal_types
        for role in roles_for(
            "半导体",
            (signal_type,),
            limit=3,
            company_context="初创民营企业",
        )
    }
    assert len(titles) >= 30


def test_fixed_source_compatibility_covers_new_signal_families():
    for signal_type in (
        "executive_change",
        "merger_acquisition",
        "joint_venture_or_spinout",
        "ipo_or_listing",
        "new_site_or_entity",
        "project_call",
        "customer_validation",
        "channel_expansion",
        "research_or_ip",
        "enterprise_system",
        "workforce_cluster",
    ):
        assert _EVENT_SIGNAL_COMPATIBILITY[signal_type]


def test_company_archetypes_change_generic_role_hypotheses():
    startup = roles_for(
        "工业自动化",
        ("funding",),
        company_context="民营初创公司完成A轮融资",
    )
    listed = roles_for(
        "工业自动化",
        ("executive_change",),
        company_context="上市公司证券代码688001任命新总裁",
    )
    foreign = roles_for(
        "工业自动化",
        ("executive_change",),
        company_context="跨国公司任命新的中国区总裁",
    )
    assert "商业化副总裁" in startup
    assert "经营管理总监" in listed
    assert "中国区战略总监" in foreign
