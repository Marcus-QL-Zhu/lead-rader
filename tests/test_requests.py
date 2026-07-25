import json

import pytest

from ht_lead_radar.requests import (
    OpportunityMode,
    OpportunityRequestPlanner,
    build_industry_map,
    plan_opportunity_request,
)


def test_market_scan_example_is_parsed_with_approved_defaults():
    plan = plan_opportunity_request("最近脑机接口行业有哪些公司可能要招总监以上职位？")

    assert plan.request.mode is OpportunityMode.MARKET_SCAN
    assert plan.request.industry_topic == "脑机接口"
    assert plan.request.target_seniority == "director_plus"
    assert plan.request.geography.code == "CN_MAINLAND_HIRING_MARKET"
    assert plan.request.geography.explicit is False
    assert plan.request.time_policy.lookback_days == 180
    assert plan.request.time_policy.recency_boost_days == 90
    assert plan.can_execute_now is True


def test_candidate_float_example_extracts_ephemeral_profile_and_asks_one_question_first():
    plan = plan_opportunity_request(
        "我现在手上有一个数据采集总监的候选人，哪些公司可能会要他？"
    )

    assert plan.request.mode is OpportunityMode.CANDIDATE_FLOAT
    assert plan.request.target_role == "数据采集总监"
    assert plan.request.candidate_profile is not None
    assert plan.request.candidate_profile.role_title == "数据采集总监"
    assert plan.request.candidate_profile.persistence_policy == "runtime_only_not_persisted"
    assert "数据闭环建设" in plan.request.candidate_profile.core_capabilities
    assert plan.request.industry_topic == "数据采集"
    assert plan.clarification.next_question.question_id == "candidate_business_context"
    assert plan.clarification.can_execute_exploratory is True
    assert plan.can_execute_now is True
    assert plan.request.deep_research_requested is True


def test_float_questions_are_progressive_not_a_required_form():
    plan = plan_opportunity_request("我有一个供应链总监候选人，哪些公司会要她？")

    assert plan.clarification.next_question.answer_field == "candidate.core_business_context"
    assert [question.answer_field for question in plan.clarification.deferred_questions] == [
        "candidate.geography_preferences",
        "candidate.leadership_scope",
    ]
    assert all(question.blocking is False for question in (
        plan.clarification.next_question,
        *plan.clarification.deferred_questions,
    ))


def test_missing_candidate_role_is_blocking():
    plan = plan_opportunity_request("我手上有一个候选人，帮我反向找公司")

    assert plan.request.mode is OpportunityMode.CANDIDATE_FLOAT
    assert plan.request.candidate_profile.role_title is None
    assert plan.clarification.next_question.question_id == "candidate_role"
    assert plan.clarification.can_execute_exploratory is False
    assert plan.can_execute_now is False


def test_missing_market_topic_is_blocking_and_does_not_invent_a_map():
    plan = plan_opportunity_request("最近有哪些公司可能要招总监？")

    assert plan.request.mode is OpportunityMode.MARKET_SCAN
    assert plan.request.industry_topic is None
    assert plan.industry_map is None
    assert plan.clarification.next_question.blocking is True
    assert plan.can_execute_now is False


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("脑机接口", "脑机接口"),
        ("芯片", "半导体"),
        ("商业航天", "商业航天"),
        ("可控核聚变", "核聚变"),
        ("人形机器人", "具身智能"),
    ],
)
def test_richer_known_industry_maps_have_all_layers_and_query_terms(topic, expected):
    industry_map = build_industry_map(topic)

    assert industry_map.canonical_topic == expected
    assert industry_map.map_kind == "curated_template"
    assert industry_map.core
    assert industry_map.direct_upstream
    assert industry_map.direct_downstream
    assert industry_map.adjacent
    assert len(industry_map.query_terms) >= 10
    assert {"融资", "扩产", "订单"}.issubset(industry_map.signal_terms)


def test_arbitrary_chinese_industry_gets_a_generated_four_layer_map():
    plan = plan_opportunity_request("最近合成生物学行业有哪些公司可能要招总监以上职位？")

    industry_map = plan.industry_map
    assert industry_map.canonical_topic == "合成生物学"
    assert industry_map.map_kind == "generated_generic_template"
    assert all("合成生物学" in item for item in industry_map.core)
    assert all("合成生物学" in item for item in industry_map.direct_upstream)
    assert all("合成生物学" in item for item in industry_map.direct_downstream)
    assert all("合成生物学" in item for item in industry_map.adjacent)
    assert plan.discovery_queries


def test_explicit_geography_overrides_china_mainland_default():
    local = plan_opportunity_request("上海脑机接口行业有哪些公司可能要招总监？")
    global_plan = plan_opportunity_request("全球商业航天行业有哪些公司可能要招总监？")

    assert local.request.geography.code == "CN_MAINLAND_LOCAL_HIRING_MARKET"
    assert local.request.geography.locations == ("上海",)
    assert local.request.geography.explicit is True
    assert global_plan.request.geography.code == "GLOBAL"
    assert global_plan.request.geography.locations == ("全球",)


def test_explicit_time_window_changes_lookback_but_never_boosts_beyond_window():
    short = plan_opportunity_request("过去30天脑机接口行业有哪些公司可能要招总监？")
    long = plan_opportunity_request("过去一年核聚变行业有哪些公司可能要招总监？")

    assert short.request.time_policy.lookback_days == 30
    assert short.request.time_policy.recency_boost_days == 30
    assert long.request.time_policy.lookback_days == 365
    assert long.request.time_policy.recency_boost_days == 90


def test_top20_and_both_non_relaxable_hard_gates_are_in_every_plan():
    plan = plan_opportunity_request("最近半导体行业有哪些公司可能要招总监？")

    assert plan.result_policy.target_company_count == 20
    assert plan.result_policy.lower_soft_threshold_to_fill is True
    assert plan.result_policy.fabricate_to_fill is False
    assert plan.hard_gates.director_plus_role_hypothesis_required is True
    assert plan.hard_gates.pre_job_upstream_signal_required is True
    assert plan.hard_gates.job_ad_only_excluded_from_main_ranking is True
    assert plan.hard_gates.relaxable_for_target_count is False


def test_float_with_material_context_can_skip_the_first_context_question():
    plan = plan_opportunity_request(
        "我手上有一个数据采集总监候选人，负责自动驾驶路采，管理50人团队，"
        "只看上海，哪些公司可能会要他？"
    )

    profile = plan.request.candidate_profile
    assert "自动驾驶" in profile.industry_experience
    assert "管理50人团队" in profile.leadership_scope
    assert profile.geography_preferences == ("上海",)
    assert plan.clarification.next_question is None
    assert plan.can_execute_now is True


def test_execution_plan_has_shared_pipeline_and_float_only_deep_research():
    market = plan_opportunity_request("脑机接口行业有哪些公司可能要招总监？")
    float_plan = plan_opportunity_request("我有一个数据采集总监候选人，哪些公司会要他？")

    shared = {
        "interpret",
        "map_industry",
        "select_sources",
        "collect",
        "normalize_eventize",
        "apply_geography",
        "infer_roles_and_gate",
        "score",
        "basic_research",
        "rank_publish",
    }
    assert shared.issubset({stage.stage_id for stage in market.stages})
    assert "float_match_and_deep_research" not in {stage.stage_id for stage in market.stages}
    assert "float_match_and_deep_research" in {stage.stage_id for stage in float_plan.stages}


def test_plan_serializes_to_json_compatible_dict_with_explicit_mode():
    plan = OpportunityRequestPlanner().plan(
        "最近脑机接口行业有哪些公司可能要招总监以上职位？"
    )

    payload = plan.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False)
    assert payload["request"]["mode"] == "MARKET_SCAN"
    assert payload["result_policy"]["target_company_count"] == 20
    assert payload["industry_map"]["core"]
    assert "脑机接口" in encoded


def test_candidate_context_is_not_echoed_or_assigned_a_persistent_identifier():
    context = "张三，电话13800138000，负责具身智能真机数采，管理80人团队，只看上海"
    plan = plan_opportunity_request(
        "我有一个数据采集总监候选人，哪些公司会要他？",
        candidate_context=context,
    )
    payload = json.dumps(plan.to_dict(), ensure_ascii=False)

    assert "张三" not in payload
    assert "13800138000" not in payload
    assert "candidate_id" not in payload
    assert plan.request.candidate_profile.persistence_policy == "runtime_only_not_persisted"

def test_candidate_context_can_start_with_role_title_without_candidate_suffix():
    plan = plan_opportunity_request(
        "我有一位候选人，请反向分析哪些公司需要他",
        candidate_context="数据采集总监，负责多源采集体系和50人团队",
    )

    assert plan.request.mode is OpportunityMode.CANDIDATE_FLOAT
    assert plan.request.candidate_profile is not None
    assert plan.request.candidate_profile.role_title == "数据采集总监"
    assert (
        plan.clarification.next_question is None
        or plan.clarification.next_question.question_id != "candidate_role"

    )
