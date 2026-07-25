from datetime import date

from ht_lead_radar.float_matching import rank_candidate_float
from ht_lead_radar.models import CompanyLead, Evidence, OutreachRoute, ScoreComponent
from ht_lead_radar.requests import CandidateProfile


def candidate(
    *,
    role_title="数据采集总监",
    seniority="director_plus",
    capabilities=("数据采集战略", "数据闭环建设"),
    industries=("自动驾驶",),
    leadership=("管理50人团队",),
    geography=("上海",),
    desired=(),
    exclusions=(),
    inferred=(),
    missing=(),
):
    return CandidateProfile(
        role_title=role_title,
        seniority=seniority,
        core_capabilities=capabilities,
        industry_experience=industries,
        leadership_scope=leadership,
        geography_preferences=geography,
        desired_directions=desired,
        exclusions=exclusions,
        inferred_fields=inferred,
        missing_critical_fields=missing,
    )


def lead(
    company,
    *,
    direction="自动驾驶数据闭环",
    role="数据平台与采集总监",
    stage="pre_ad",
    score=80,
    people=(),
    organizations=(),
    routes=(),
    gates=None,
    event_date="2026-07-20",
):
    evidence = [
        Evidence(
            company=company,
            event_type="data_or_model",
            phase="build_organize",
            event_date=event_date,
            title=f"{company}建设真机数据采集与训练闭环",
            snippet="公司扩建数据平台并启动多源采集体系",
            source_url=f"https://{company}.example/news",
            source_name="company",
            source_grade="A",
            people=people,
            organizations=organizations,
            event_id=f"{company}-event-1",
        ),
        Evidence(
            company=company,
            event_type="funding",
            phase="strategy_capital",
            event_date=event_date,
            title=f"{company}完成融资",
            snippet="资金用于研发团队与产品交付",
            source_url=f"https://investor.example/{company}",
            source_name="investor",
            source_grade="B",
            organizations=organizations,
            event_id=f"{company}-event-2",
        ),
    ]
    return CompanyLead(
        company=company,
        direction=direction,
        score=score,
        confidence_grade="B",
        timing_stage=stage,
        target_roles=[role],
        hiring_thesis=f"{company}需要搭建数据采集平台与数据闭环团队",
        evidence=evidence,
        outreach_routes=list(routes),
        gates=gates if gates is not None else {
            "director_plus": True,
            "has_upstream_signal": True,
        },
        score_components=[
            ScoreComponent("need", "需求信号", 26, "两个独立上游事件"),
            ScoreComponent("role", "总监级岗位逻辑", 23, role),
            ScoreComponent("commercial", "商业化强度", 4, "融资"),
            ScoreComponent("confidence", "证据可信度", 10, "两个独立域名"),
            ScoreComponent("timing", "介入时机", 15 if stage == "pre_ad" else 5, stage),
        ],
        mainland_relevance="上海研发团队，中国大陆招聘市场相关",
        basic_research={
            "public_people_in_evidence": list(people),
            "external_investors_or_institutions": list(organizations),
        },
    )


def test_float_has_exactly_four_explainable_dimensions_and_keeps_market_score():
    source = lead("甲公司", people=("创始人王某",), organizations=("示例资本",))
    result = rank_candidate_float(
        candidate(),
        [source],
        as_of=date(2026, 7, 25),
    )[0]

    assert [item.key for item in result.score_components] == [
        "company_need",
        "candidate_match",
        "timing",
        "public_relationship_researchability",
    ]
    assert [item.max_points for item in result.score_components] == [35, 35, 20, 10]
    assert result.float_score == sum(item.points for item in result.score_components)
    assert result.market_scan_score == source.score
    assert source.score == 80  # Float ranking must not overwrite Market Scan.


def test_stronger_candidate_match_ranks_above_unrelated_role():
    matching = lead("匹配公司")
    unrelated = lead(
        "不匹配公司",
        direction="半导体工厂",
        role="晶圆制造与供应链总监",
    )

    results = rank_candidate_float(
        candidate(),
        [unrelated, matching],
        as_of=date(2026, 7, 25),
    )

    assert [item.company for item in results] == ["匹配公司", "不匹配公司"]
    matching_fit = next(
        item for item in results[0].score_components if item.key == "candidate_match"
    )
    unrelated_fit = next(
        item for item in results[1].score_components if item.key == "candidate_match"
    )
    assert matching_fit.points > unrelated_fit.points


def test_missing_candidate_information_is_never_reported_as_a_fact_or_selling_point():
    incomplete = candidate(
        role_title=None,
        seniority="unknown",
        capabilities=(),
        industries=(),
        leadership=(),
        geography=(),
        missing=(
            "role_title",
            "core_business_context",
            "candidate_geography_preference",
            "leadership_scope",
        ),
    )

    result = rank_candidate_float(
        incomplete,
        [lead("甲公司")],
        as_of=date(2026, 7, 25),
    )[0]

    assert result.candidate_selling_points == ("当前候选人事实不足，暂不形成正向卖点结论",)
    assert any("当前职位" in item for item in result.missing_information)
    assert any("团队规模" in item for item in result.missing_information)
    assert all("管理" not in item for item in result.candidate_selling_points)


def test_role_derived_capabilities_are_explicitly_marked_as_unverified_inferences():
    inferred_profile = candidate(
        inferred=("core_capabilities_from_role_title",),
        leadership=(),
        missing=("leadership_scope",),
    )

    result = rank_candidate_float(
        inferred_profile,
        [lead("甲公司")],
        as_of=date(2026, 7, 25),
    )[0]

    assert any("待核实推断能力" in item for item in result.candidate_selling_points)
    assert any("不是候选人已确认事实" in item for item in result.risks_or_conflicts_to_verify)
    assert any("尚未由候选人经历" in item for item in result.missing_information)


def test_exclusion_conflict_is_disclosed_and_reduces_fit():
    base = candidate(exclusions=())
    excluded = candidate(exclusions=("自动驾驶",))
    company = lead("甲公司")

    base_result = rank_candidate_float(base, [company], as_of=date(2026, 7, 25))[0]
    excluded_result = rank_candidate_float(excluded, [company], as_of=date(2026, 7, 25))[0]
    base_fit = next(item for item in base_result.score_components if item.key == "candidate_match")
    excluded_fit = next(item for item in excluded_result.score_components if item.key == "candidate_match")

    assert excluded_fit.points < base_fit.points
    assert any("排除项" in item for item in excluded_result.risks_or_conflicts_to_verify)
    assert any("排除项" in item for item in excluded_result.evidence_that_would_change_ranking)


def test_public_relationship_score_uses_only_observed_people_institutions_and_routes():
    route = OutreachRoute(
        kind="投资人",
        target="某合伙人",
        path="公开投资关系",
        evidence_url="https://fund.example/deal",
        grade="B",
        note="基金官网披露",
    )
    rich = lead(
        "关系丰富公司",
        people=("创始人王某",),
        organizations=("示例资本",),
        routes=(route,),
    )
    sparse = lead("关系稀疏公司")

    results = rank_candidate_float(candidate(), [sparse, rich], as_of=date(2026, 7, 25))
    by_name = {item.company: item for item in results}
    rich_score = next(
        item for item in by_name["关系丰富公司"].score_components
        if item.key == "public_relationship_researchability"
    )
    sparse_score = next(
        item for item in by_name["关系稀疏公司"].score_components
        if item.key == "public_relationship_researchability"
    )

    assert rich_score.points > sparse_score.points
    assert any("公开证据中出现可研究人物" in reason for reason in rich_score.reasons)
    assert any("尚未出现具名" in risk for risk in by_name["关系稀疏公司"].risks_or_conflicts_to_verify)


def test_float_is_analysis_only_ephemeral_and_always_requires_deep_research():
    result = rank_candidate_float(
        candidate(),
        [lead("甲公司")],
        as_of=date(2026, 7, 25),
    )[0]
    payload = result.to_dict()

    assert payload["deep_research_required"] is True
    assert payload["analysis_only"] is True
    assert payload["candidate_profile_persisted"] is False
    assert payload["outreach_generated"] is False
    assert "candidate" not in payload
    assert "candidate_profile" not in payload
    assert result.evidence_that_would_change_ranking


def test_limit_is_hard_capped_at_twenty_and_failed_gates_are_not_ranked():
    leads = [lead(f"公司{i:02d}") for i in range(25)]
    leads.append(lead(
        "门槛失败公司",
        gates={"director_plus": False, "has_upstream_signal": True},
    ))
    leads.append(lead(
        "ad-window-failed-company",
        gates={
            "director_plus": True,
            "has_upstream_signal": True,
            "upstream_precedes_job_ad": False,
        },
    ))

    results = rank_candidate_float(
        candidate(),
        leads,
        limit=100,
        as_of=date(2026, 7, 25),
    )

    assert len(results) == 20
    assert [item.rank for item in results] == list(range(1, 21))
    assert "门槛失败公司" not in {item.company for item in results}
    assert "ad-window-failed-company" not in {item.company for item in results}
