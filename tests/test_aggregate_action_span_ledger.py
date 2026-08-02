from __future__ import annotations

import pytest

from ht_lead_radar.aggregate_adapters.action_span_ledger import (
    ActionSpan,
    AtomicClaim,
    build_action_span_ledger,
    _suppress_enterprise_duplicate_claims,
)
from ht_lead_radar.aggregate_adapters.entity_ledger import (
    build_article_entity_ledger,
)
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex


def _article(body: str, *, title: str = "") -> CleanArticle:
    return CleanArticle(
        index=SourceArticleIndex(
            source_id="source",
            source_article_id="article-1",
            channel="news",
            canonical_url="https://example.invalid/article-1",
            title=title,
            published_at="2026-08-01T00:00:00+08:00",
            discovered_at="2026-08-01T01:00:00+08:00",
            cursor_value="1",
            listing_page="https://example.invalid",
            listing_position=1,
            content_hash="index-hash",
            discovery_method="exact",
        ),
        clean_body=body,
        content_hash="body-hash",
    )


def _build(
    body: str,
    *,
    title: str = "",
    subject_hints: tuple[str, ...] = (),
):
    article = _article(body, title=title)
    candidates = [
        {"subject_hint": subject, "quote": f"{subject}完成发布"}
        for subject in subject_hints
    ]
    entities = build_article_entity_ledger(article, candidates, [])
    return article, build_action_span_ledger(article, entities)


def test_action_span_is_exact_immutable_source_text() -> None:
    body = "无关前言。甲辰科技完成A轮融资。后续说明。"
    article, ledger = _build(body)

    claim = next(item for item in ledger.claims if item.event_type_hint == "funding")
    span = ledger.spans_by_id()[claim.span_id]

    assert span.text == "甲辰科技完成A轮融资。"
    assert article.clean_body[span.char_start : span.char_end] == span.text
    assert article.clean_body[
        claim.action_char_start : claim.action_char_end
    ] == claim.action_text


def test_action_claim_uses_only_grounded_entity_ids() -> None:
    body = "甲辰科技与乙巳机器人签署战略合作协议。"
    _, ledger = _build(body)

    claims = [item for item in ledger.claims if item.event_type_hint == "partnership"]
    assert len(claims) == 2
    assert all(len(claim.allowed_subject_entity_ids) == 1 for claim in claims)
    assert all(claim.primary_subject_entity_id for claim in claims)


def test_planned_action_gets_target_status() -> None:
    _, ledger = _build("甲辰科技拟1亿元投建芯片生产基地。")

    claim = next(
        item for item in ledger.claims if item.event_type_hint == "factory_or_capacity"
    )
    assert claim.event_status_hint == "target"


def test_planned_financing_start_is_target_not_started() -> None:
    _, ledger = _build("甲辰科技计划尽快启动市场化融资。")

    claim = next(item for item in ledger.claims if item.event_type_hint == "funding")
    assert claim.event_status_hint == "target"


def test_financing_continuation_after_clause_is_not_host_mandatory() -> None:
    _, ledger = _build(
        "甲辰科技宣布完成A轮融资。融资完成后，公司将持续推进产品研发。"
    )

    continuation = next(
        claim
        for claim in ledger.claims
        if claim.event_type_hint == "funding"
        and claim.action_text == "融资完成"
    )
    assert continuation.host_mandatory is False


def test_platform_application_is_target_status() -> None:
    _, ledger = _build(
        "武汉超导科技发布产品。下一步将同时推进市级、国家级产业创新平台申报。"
    )

    claim = next(
        item
        for item in ledger.claims
        if "产业创新平台申报" in item.action_text
    )
    assert claim.event_status_hint == "target"


def test_funding_use_clauses_keep_workforce_research_and_buildout_signals() -> None:
    _, ledger = _build(
        "德塔智能完成近5亿元融资。本轮融资将主要用于基础模型持续迭代、"
        "数据闭环建设以及核心研发团队扩充，推动真实工业场景工程化验证。"
    )

    typed = {
        claim.event_type_hint: claim
        for claim in ledger.claims
        if claim.event_type_hint
        in {"workforce_cluster", "research_or_ip"}
    }
    assert {"workforce_cluster", "research_or_ip"} <= set(typed)
    assert all(claim.host_mandatory for claim in typed.values())


def test_platform_buildout_application_is_typed_and_host_locked() -> None:
    _, ledger = _build(
        "武汉超导完成种子轮融资，资金将重点投向技术研发、产业化平台建设及高端人才引进。"
        "下一步推进市级、国家级产业创新平台申报。"
    )

    typed = {
        claim.event_type_hint: claim
        for claim in ledger.claims
        if claim.event_type_hint
        in {"workforce_cluster", "research_or_ip", "project_buildout"}
    }
    assert {"workforce_cluster", "research_or_ip", "project_buildout"} <= set(typed)
    assert typed["project_buildout"].event_status_hint == "target"
    assert typed["project_buildout"].host_mandatory


def test_named_ai_employee_rollout_has_typed_event_family() -> None:
    _, ledger = _build(
        "瓴羊科技发布AgentOne，四名AI员工正式上岗，覆盖AI销售、AI客服、AI运营和AI营销四个场景。"
    )

    claim = next(
        item for item in ledger.claims if "覆盖AI销售" in item.action_text
    )
    assert "enterprise_system" in claim.allowed_event_types


def test_enterprise_rollout_headline_and_body_are_one_claim() -> None:
    claims = (
        AtomicClaim(
            claim_id="headline",
            span_id="headline-span",
            event_type_hint="open_action",
            event_status_hint="completed",
            action_text="覆盖销售、客服、运营、营销四大场景",
            action_char_start=20,
            action_char_end=40,
            allowed_subject_entity_ids=("ae-company",),
            primary_subject_entity_id="ae-company",
            allowed_event_types=("enterprise_system",),
        ),
        AtomicClaim(
            claim_id="body",
            span_id="body-span",
            event_type_hint="open_action",
            event_status_hint="completed",
            action_text="覆盖AI销售、AI客服、AI运营和AI营销四个场景",
            action_char_start=80,
            action_char_end=110,
            allowed_subject_entity_ids=("ae-company",),
            primary_subject_entity_id="ae-company",
            allowed_event_types=("enterprise_system",),
        ),
    )
    spans = {
        "headline-span": ActionSpan(
            span_id="headline-span",
            unit_id="u",
            char_start=0,
            char_end=60,
            text="瓴羊AgentOne headline",
        ),
        "body-span": ActionSpan(
            span_id="body-span",
            unit_id="u",
            char_start=60,
            char_end=140,
            text="瓴羊AgentOne body",
        ),
    }
    kept = _suppress_enterprise_duplicate_claims(claims, spans)
    assert [claim.claim_id for claim in kept] == ["body"]


def test_digest_order_and_global_expansion_are_not_lost() -> None:
    _, ledger = _build(
        "美的集团一月内新增欧洲订单共20万台。"
        "智谷天厨融资完成后，公司将持续推进物理AI研发，并加速全球化落地。",
        subject_hints=("美的集团", "智谷天厨"),
    )

    assert any(
        claim.event_type_hint == "major_order"
        and "新增欧洲订单" in claim.action_text
        for claim in ledger.claims
    )
    expansion = next(
        claim
        for claim in ledger.claims
        if claim.event_type_hint == "global_expansion"
    )
    assert expansion.host_mandatory


def test_product_owner_connector_is_not_company_name() -> None:
    article = _article(
        "场景单位：晶泰科技是一家科学智能生物医药企业。"
        "为此，晶泰科技通过参与IEEE的WBCD双臂机器人挑战赛，推出专属生命科学赛道。"
    )
    entities = build_article_entity_ledger(article, [], [])

    assert entities.entity_for_name("晶泰科技") is not None
    assert entities.entity_for_name("晶泰科技通过参") is None


def test_future_controlling_shareholder_change_is_target() -> None:
    _, ledger = _build("贝肯能源：控股股东将变更为极宁科技，8月3日复牌。")

    claim = next(
        item for item in ledger.claims
        if item.event_type_hint == "merger_acquisition"
    )
    assert claim.event_status_hint == "target"


def test_claim_batches_never_exceed_three_and_validate_argument() -> None:
    body = (
        "甲辰科技完成A轮融资。"
        "乙巳机器人签署战略合作协议。"
        "丙午智能发布机器人产品。"
        "丁未科技拟投建芯片生产基地。"
    )
    _, ledger = _build(body)

    batches = ledger.batches()
    assert batches
    assert all(1 <= len(batch) <= 3 for batch in batches)
    with pytest.raises(ValueError):
        ledger.batches(4)


def test_legacy_candidate_ids_are_audit_only_provenance() -> None:
    body = "甲辰科技完成A轮融资。"
    article = _article(body)
    entities = build_article_entity_ledger(article, [], [])
    ledger = build_action_span_ledger(
        article,
        entities,
        [{"id": "c_old", "quote": body, "event_type": "funding"}],
    )

    funding = next(item for item in ledger.claims if item.event_type_hint == "funding")
    assert funding.legacy_candidate_ids == ("c_old",)


def test_policy_prose_without_company_entity_creates_no_claims() -> None:
    _, ledger = _build("现将有关事项通知如下：加快建设人工智能产业基地。")
    assert ledger.claims == ()


def test_action_word_inside_company_name_is_not_an_event() -> None:
    _, ledger = _build("电投产融：山东莱阳核电项目获核准。")

    assert not any(
        claim.event_type_hint == "factory_or_capacity"
        for claim in ledger.claims
    )
    assert any(
        claim.event_type_hint == "regulatory_or_clinical"
        for claim in ledger.claims
    )


def test_stale_funding_is_excluded_even_with_completion_verb() -> None:
    _, ledger = _build("甲辰科技于去年8月完成10亿元B轮融资。")

    assert not any(claim.event_type_hint == "funding" for claim in ledger.claims)


def test_stale_qualifier_before_clause_delimiter_is_excluded() -> None:
    _, ledger = _build(
        "甲辰科技发布产品。去年10月，甲辰科技成立研究院；"
        "今年5月，甲辰科技启用研究院。"
    )

    assert not any(
        claim.event_type_hint == "new_site_or_entity"
        and "成立研究院" in claim.action_text
        for claim in ledger.claims
    )
    assert any(
        claim.event_type_hint == "new_site_or_entity"
        and "启用研究院" in claim.action_text
        for claim in ledger.claims
    )


def test_scene_unit_vendor_selection_is_customer_validation() -> None:
    _, ledger = _build(
        "场景单位：晶泰科技是一家科学智能生物医药企业。"
        "为此，晶泰科技通过参与IEEE的WBCD双臂机器人挑战赛，推出专属生命科学赛道，"
        "选定技术方中科院工业人工智能研究所IAII-IICS战队，"
        "在20分钟内连续完成9轮高精度实验操作。"
    )

    assert any(
        claim.event_type_hint == "customer_validation"
        and ("选定技术方" in claim.action_text or "完成9轮" in claim.action_text)
        for claim in ledger.claims
    )


def test_product_release_and_future_adaptation_are_two_claims() -> None:
    _, ledger = _build(
        "甲辰科技推出龍鹰二号芯片，计划于2027年第一季度开启适配。"
    )

    claims = [
        claim
        for claim in ledger.claims
        if claim.event_type_hint == "technical_milestone"
    ]
    assert {claim.event_status_hint for claim in claims} == {
        "completed",
        "target",
    }
    assert any(claim.host_mandatory for claim in claims)


def test_dominant_article_company_is_primary_partnership_subject() -> None:
    _, ledger = _build(
        "康宁发布玻璃产品。康宁推进材料创新。"
        "京东方与康宁签署合作备忘录。"
    )

    claims = [
        item for item in ledger.claims if item.event_type_hint == "partnership"
    ]
    entities = build_article_entity_ledger(
        _article(
            "康宁发布玻璃产品。康宁推进材料创新。"
            "京东方与康宁签署合作备忘录。"
        ),
        [],
        [],
    )
    assert {claim.primary_subject_entity_id for claim in claims} == {
        entities.entity_for_name("康宁").entity_id,
        entities.entity_for_name("京东方").entity_id,
    }


def test_capacity_word_inside_capability_phrase_is_not_an_event() -> None:
    _, ledger = _build(
        "层浪生物具备仪器与试剂全链条自研自产能力，"
        "同时兼具商业化落地能力。"
    )

    assert not any(
        claim.event_type_hint == "factory_or_capacity"
        for claim in ledger.claims
    )


def test_future_spend_commitment_is_host_mandatory_order_signal() -> None:
    _, ledger = _build("Meta承诺近7000亿美元的未来支出。")

    claim = next(
        item for item in ledger.claims if item.event_type_hint == "major_order"
    )
    assert claim.host_mandatory is True


def test_measured_internal_customer_validation_is_host_mandatory() -> None:
    _, ledger = _build(
        "美团全场景AI Agent平台CatPaw全新上线。"
        "目前，CatPaw已在美团内部覆盖9万名员工，并在多个真实业务场景中完成验证。"
    )

    claim = next(
        item for item in ledger.claims if item.event_type_hint == "customer_validation"
    )
    assert claim.host_mandatory is True


def test_manufacturing_investment_commitment_is_target_capacity_signal() -> None:
    _, ledger = _build(
        "康宁发布玻璃产品。2026年，康宁承诺在中国新增5亿美元投资，"
        "深化光通信布局，进一步夯实制造根基。"
    )

    capacity = next(
        item
        for item in ledger.claims
        if item.event_type_hint == "factory_or_capacity"
    )
    order = next(
        item for item in ledger.claims if item.event_type_hint == "major_order"
    )
    assert capacity.event_status_hint == "target"
    assert capacity.host_mandatory is True
    assert order.host_mandatory is False


def test_explicit_investment_and_irrevocable_contract_are_host_mandatory() -> None:
    _, ledger = _build(
        "英伟达将向NAVER投资10亿美元。"
        "Meta已签署不可撤销合同承诺3493亿美元。"
    )

    assert any(
        claim.event_type_hint == "funding" and claim.host_mandatory
        for claim in ledger.claims
    )
    assert any(
        claim.event_type_hint == "major_order" and claim.host_mandatory
        for claim in ledger.claims
    )


def test_completed_financing_and_signed_joint_contract_are_host_mandatory() -> None:
    _, ledger = _build(
        "日前，影眸科技宣布完成新一轮数亿元人民币融资。"
        "荣信汇科电气股份有限公司与中国核电工程有限公司组成联合体，"
        "正式签署边缘局域模电源系统现金合同。"
    )

    assert any(
        claim.event_type_hint == "funding" and claim.host_mandatory
        for claim in ledger.claims
    )
    orders = [
        claim for claim in ledger.claims if claim.event_type_hint == "major_order"
    ]
    assert len(orders) >= 2
    assert all(claim.host_mandatory for claim in orders)


def test_chained_coreference_keeps_funding_recipient_grounded_in_span() -> None:
    body = (
        "英伟达与Safe Superintelligence达成长期合作。"
        "这笔合作将帮助这家初创企业获取算力。"
        "两家企业随后披露，英伟达完成了一笔大额投资。"
    )
    article = _article(body)
    entities = build_article_entity_ledger(article, [], [])
    ledger = build_action_span_ledger(article, entities)
    claim = next(
        item for item in ledger.claims if item.event_type_hint == "funding"
    )
    span = ledger.spans_by_id()[claim.span_id]

    safe = entities.entity_for_name("Safe Superintelligence")
    assert safe is not None
    assert safe.entity_id in claim.allowed_subject_entity_ids
    assert "Safe Superintelligence" not in span.text
    assert claim.host_mandatory is True


def test_image_credit_company_is_not_bound_as_action_subject() -> None:
    _, ledger = _build(
        "中电科电子装备集团有限公司/供图 本报讯："
        "集成电路产业园建设项目突破正负零节点。"
    )

    assert ledger.claims == ()


@pytest.mark.parametrize(
    ("body", "event_type"),
    [
        ("甲辰科技上线企业智能工作台。", "technical_milestone"),
        ("甲辰科技完成基础设施重构。", "technical_milestone"),
        ("甲辰科技组建机器人事业部。", "new_site_or_entity"),
        (
            "甲辰科技与乙巳智能会面，磋商人工智能合作方案。",
            "partnership",
        ),
        (
            "甲辰科技首款产品进入首批交付与用户复现阶段。",
            "customer_validation",
        ),
        ("超纯应材今日启动新股申购。", "ipo_or_listing"),
        ("英伟达将向甲辰科技投资10亿美元。", "funding"),
        ("翁荔宣布重返甲辰科技公司。", "executive_change"),
    ],
)
def test_compositional_action_grammar_recalls_operational_phrases(
    body: str,
    event_type: str,
) -> None:
    _, ledger = _build(body)

    assert any(claim.event_type_hint == event_type for claim in ledger.claims)


def test_exit_this_company_resolves_to_contextual_company_not_destination() -> None:
    body = (
        "Thinking Machines Lab联合创始人翁荔宣布退出这家她和OpenAI"
        "前首席技术官共同创立的公司。"
    )
    article = _article(body)
    entities = build_article_entity_ledger(article, [], [])
    ledger = build_action_span_ledger(article, entities)

    claim = next(
        item
        for item in ledger.claims
        if item.event_type_hint == "executive_change"
        and item.action_text.startswith("退出这家")
    )
    primary = entities.by_id()[claim.primary_subject_entity_id]
    assert primary.canonical_name == "Thinking Machines Lab"
    assert claim.host_mandatory is True


def test_explicit_return_to_company_is_mandatory_but_after_clause_is_not_event() -> None:
    _, target_ledger = _build(
        "OpenAI发布模型。OpenAI发言人证实，翁荔将重返OpenAI公司。"
    )
    target = next(
        item
        for item in target_ledger.claims
        if item.event_type_hint == "executive_change"
    )
    assert target.event_status_hint == "target"
    assert target.host_mandatory is True

    _, consequence_ledger = _build(
        "OpenAI发布模型。OpenAI表示，翁荔重返OpenAI公司后将从事模型研究。"
    )
    assert not any(
        item.event_type_hint == "executive_change"
        for item in consequence_ledger.claims
    )


@pytest.mark.parametrize(
    "body",
    [
        "甲辰科技发布年度报告。",
        "甲辰科技此前完成基础设施重构。",
    ],
)
def test_broad_action_grammar_does_not_turn_editorial_or_history_into_event(
    body: str,
) -> None:
    _, ledger = _build(body)

    assert not any(
        claim.event_type_hint == "technical_milestone"
        for claim in ledger.claims
    )


def test_bilateral_negotiation_fans_out_to_output_capable_claims() -> None:
    _, ledger = _build(
        "甲辰科技与乙巳智能负责人会面，磋商人工智能领域合作方案。"
    )

    claims = [
        claim for claim in ledger.claims if claim.event_type_hint == "partnership"
    ]
    assert len(claims) == 2
    assert all(len(claim.allowed_subject_entity_ids) == 1 for claim in claims)
    assert all(
        claim.primary_subject_entity_id == claim.allowed_subject_entity_ids[0]
        for claim in claims
    )


@pytest.mark.parametrize(
    ("body", "expected_rounds"),
    [
        ("甲辰科技完成A轮及A+轮融资。", {"A轮", "A+轮"}),
        ("甲辰科技完成A1及A2轮融资。", {"A1轮", "A2轮"}),
    ],
)
def test_multi_round_funding_is_split_into_output_capable_atomic_claims(
    body: str,
    expected_rounds: set[str],
) -> None:
    _, ledger = _build(body)

    funding = [claim for claim in ledger.claims if claim.event_type_hint == "funding"]
    assert {claim.funding_round_hint for claim in funding} == expected_rounds


def test_two_listed_companies_in_one_subscription_notice_fan_out() -> None:
    _, ledger = _build(
        "今日共有2只新股申购，为创业板的超纯应材（301717）、"
        "科创板的国仪公司（688828）。"
    )

    claims = [claim for claim in ledger.claims if claim.event_type_hint == "ipo_or_listing"]
    assert len(claims) == 2
    assert all(len(claim.allowed_subject_entity_ids) == 1 for claim in claims)


def test_current_action_is_not_dropped_because_it_mentions_a_replaced_service() -> None:
    _, ledger = _build(
        "此外，OpenAI在API中推出Fast模式，取代此前的Priority Processing服务。"
    )

    assert any(
        claim.event_type_hint == "technical_milestone"
        and "API" in claim.action_text
        and claim.host_mandatory
        for claim in ledger.claims
    )


def test_product_release_stage_description_is_not_a_second_launch() -> None:
    _, ledger = _build(
        "OpenAI已发布Codex工具。OpenAI表示，该产品目前仍处于早期发布阶段。"
    )
    technical = [
        claim for claim in ledger.claims if claim.event_type_hint == "technical_milestone"
    ]

    assert len(technical) == 1
    assert "发布Codex" in technical[0].action_text


def test_lease_commitment_and_guarantee_discussion_are_not_new_sites() -> None:
    _, ledger = _build(
        "Meta发布公告。Meta还有尚未开始执行的租赁承诺。"
        "英伟达发布公告。OpenAI发布公告。"
        "英伟达正在商讨为OpenAI租赁数据中心提供担保。"
    )

    assert not any(
        claim.event_type_hint == "new_site_or_entity"
        for claim in ledger.claims
    )


def test_prior_year_listing_and_dfi_registration_are_not_current_new_events() -> None:
    _, ledger = _build(
        "2025年3月，蜜雪冰城在港交所挂牌上市。"
        "亦庄国投获得债务融资工具DFI注册批文。"
    )

    assert not any(
        claim.event_type_hint in {"ipo_or_listing", "new_site_or_entity", "funding"}
        for claim in ledger.claims
    )


def test_quantified_capacity_expansion_and_direct_partnership_are_host_mandatory() -> None:
    _, ledger = _build(
        "NAVER宣布人工智能工厂基础设施扩建计划，"
        "GAK数据中心的AI工厂部署规模将从55兆瓦扩大至200兆瓦。"
        "华虹宏力表示，面向未来将继续深化与中微的战略合作。"
    )

    assert any(
        claim.event_type_hint == "factory_or_capacity"
        and claim.host_mandatory
        and claim.event_status_hint == "target"
        for claim in ledger.claims
    )
    assert any(
        claim.event_type_hint == "partnership" and claim.host_mandatory
        for claim in ledger.claims
    )


def test_explanatory_relative_launch_is_not_a_current_action() -> None:
    _, ledger = _build(
        "九科信息介绍元枢纽理念，九科信息推出的bit-Agent正是其具象化。"
    )

    assert not any(
        claim.event_type_hint == "technical_milestone"
        for claim in ledger.claims
    )


def test_long_feature_does_not_relabel_explicit_ineligible_counterpart() -> None:
    body = (
        "\u6708\u4e4b\u6692\u9762\u53d1\u5e03Kimi\u6a21\u578b\u3002"
        "Anthropic\u63a8\u51faClaude Community Ambassadors\uff0c"
        "\u9f13\u52b1\u6210\u5458\u7ec4\u7ec7\u7ebf\u4e0b\u6d3b\u52a8\u3002"
        + "\u8fd9\u662f\u7528\u4e8e\u6d4b\u8bd5\u957f\u6587\u7ae0\u8def\u7531\u7684\u80cc\u666f\u6587\u672c\u3002"
        * 120
    )
    article = _article(body, title="\u6708\u4e4b\u6692\u9762\u957f\u6587")
    entities = build_article_entity_ledger(article, [], [])
    ledger = build_action_span_ledger(article, entities)

    foreign_claims = [
        claim
        for claim in ledger.claims
        if "Claude Community Ambassadors" in claim.action_text
    ]
    assert foreign_claims == []


def test_product_future_release_uses_previous_company_context() -> None:
    _, ledger = _build(
        "xAI发布Build模型。马斯克称，"
        "Grok 4.7将在Grok4.6发布数周后推出。"
    )

    technical = [
        claim for claim in ledger.claims if claim.event_type_hint == "technical_milestone"
    ]
    assert any("Grok 4.7" in claim.action_text for claim in technical)


def test_comma_separated_technical_launches_become_two_atomic_claims() -> None:
    _, ledger = _build(
        "Harness发布自主软件交付智能体，Anthropic推出科研一体化工作台。"
    )

    technical = [
        claim for claim in ledger.claims if claim.event_type_hint == "technical_milestone"
    ]
    assert len(technical) >= 2
    assert any("自主软件交付智能体" in claim.action_text for claim in technical)
    assert any("科研一体化工作台" in claim.action_text for claim in technical)


def test_cross_unit_coreference_can_bind_previous_operating_company() -> None:
    _, ledger = _build(
        "华虹宏力今日宣布设备合作计划。\n"
        "面向未来，将继续深化与中微的战略合作。"
    )

    assert any(claim.event_type_hint == "partnership" for claim in ledger.claims)


def test_object_before_full_new_launch_word_is_a_technical_action() -> None:
    _, ledger = _build("美团全场景AI Agent平台CatPaw全新上线。")

    assert any(claim.event_type_hint == "technical_milestone" for claim in ledger.claims)


def test_api_service_future_integration_is_an_independent_action() -> None:
    _, ledger = _build(
        "字节跳动推出Seedance模型，API服务也将于近期接入火山方舟。"
    )

    technical = [
        claim for claim in ledger.claims if claim.event_type_hint == "technical_milestone"
    ]
    assert len(technical) >= 2
    assert any(
        claim.host_mandatory and "API服务" in claim.action_text
        for claim in technical
    )


def test_current_training_quote_inherits_previous_speaker_company() -> None:
    article = _article(
        "谷歌CEO在财报会上介绍Gemini进展。"
        "他继续说道：我们目前正在训练 Gemini4，并投入了大量资源。"
    )
    entities = build_article_entity_ledger(article, [], [])
    ledger = build_action_span_ledger(article, entities)

    claim = next(
        item
        for item in ledger.claims
        if item.event_type_hint == "technical_milestone"
        and "正在训练" in item.action_text
    )
    assert entities.by_id()[claim.primary_subject_entity_id].canonical_name == "谷歌"
    assert claim.host_mandatory is True


def test_current_infrastructure_rebuild_is_host_mandatory() -> None:
    _, ledger = _build(
        "元戎启行内部实验室目前已完成基础设施重构等前期工作。"
    )
    claim = next(
        item
        for item in ledger.claims
        if item.event_type_hint == "technical_milestone"
        and "基础设施重构" in item.action_text
    )
    assert claim.host_mandatory is True


def test_repeated_aggregator_headline_prefers_detailed_action_claim() -> None:
    _, ledger = _build(
        "（示例来源） OpenAI发布开源版Codex Security CLI "
        "7月29日消息，OpenAI已发布开源版Codex Security CLI。"
    )

    technical = [
        claim for claim in ledger.claims if claim.event_type_hint == "technical_milestone"
    ]
    assert len(technical) == 1
    assert technical[0].action_char_start > 20


def test_future_api_and_partnership_negotiation_have_locked_statuses() -> None:
    _, ledger = _build(
        "字节跳动推出Seedance模型，API服务也将于近期接入火山方舟。"
        "三星电子与OpenAI会面，磋商人工智能合作方案。"
    )

    assert any(
        claim.event_type_hint == "technical_milestone"
        and "API服务" in claim.action_text
        and claim.event_status_hint == "target"
        for claim in ledger.claims
    )
    assert all(
        claim.event_status_hint == "started"
        for claim in ledger.claims
        if claim.event_type_hint == "partnership"
    )


def test_two_future_product_versions_remain_two_atomic_claims() -> None:
    _, ledger = _build(
        "xAI表示Grok 4.6将于8月发布，数周后推出Grok 4.7。"
    )
    technical = [
        claim for claim in ledger.claims if claim.event_type_hint == "technical_milestone"
    ]

    assert any("Grok 4.6" in claim.action_text for claim in technical)
    assert any("Grok 4.7" in claim.action_text for claim in technical)


def test_long_financing_feature_inherits_unique_company_for_coreference() -> None:
    body = (
        "甲辰科技完成C轮融资，并介绍最新进展。" * 4
        + "目前，公司的产品与服务已覆盖全国1500余家医疗机构。"
        + "第二，加快现有生产基地的产能拓展和批量交付。"
        + "尾声。" * 500
    )
    _, ledger = _build(body, title="对话甲辰科技：完成C轮融资后的产业化计划")

    assert any(
        claim.event_type_hint == "customer_validation" and "1500" in claim.action_text
        for claim in ledger.claims
    )
    assert any(
        claim.event_type_hint == "factory_or_capacity" and "产能拓展" in claim.action_text
        for claim in ledger.claims
    )


def test_joint_release_and_joint_hosting_fan_out_partnership_claims() -> None:
    _, release = _build("甲辰半导体联合乙巳科技发布战略合作声明。")
    _, hosting = _build("大会由甲辰控股主办，乙巳集团联合主办。")

    release_claims = [
        claim for claim in release.claims if claim.event_type_hint == "partnership"
    ]
    hosting_claims = [
        claim for claim in hosting.claims if claim.event_type_hint == "partnership"
    ]
    assert len(release_claims) == 2
    assert len(hosting_claims) == 2


def test_clinical_capacity_and_customer_vocabulary_is_recalled() -> None:
    _, ledger = _build(
        "甲辰生物已启动人体临床试验。"
        "目前，公司通过了ISO 13485体系认证并完成FDA DMF备案。"
        "此外，公司在海外开展产品验证和示范。"
        "第二，加快数据手套产能拓展和批量交付。"
    )

    kinds = {claim.event_type_hint for claim in ledger.claims}
    assert "regulatory_or_clinical" in kinds
    assert "customer_validation" in kinds
    assert "factory_or_capacity" in kinds


def test_financial_and_disclosure_phrases_do_not_create_operating_claims() -> None:
    _, ledger = _build(
        "甲辰科技发布公告，签署借款合同并将注册资本提升至两亿元。"
    )

    kinds = {claim.event_type_hint for claim in ledger.claims}
    assert "technical_milestone" not in kinds
    assert "major_order" not in kinds
    assert "new_site_or_entity" not in kinds


def test_unlocked_operating_predicates_create_bounded_open_actions() -> None:
    _, ledger = _build(
        "强脑科技带来了脑控训练套件和两项全球首发能力。"
        "优必选已经开始规模化部署工业人形机器人。"
    )

    open_claims = [
        claim for claim in ledger.claims if claim.event_type_hint == "open_action"
    ]
    assert any(
        "technical_milestone" in claim.allowed_event_types
        for claim in open_claims
    )
    assert any(
        "customer_validation" in claim.allowed_event_types
        for claim in open_claims
    )
    assert all(claim.host_mandatory is False for claim in open_claims)


def test_commentary_router_does_not_suppress_current_conference_action() -> None:
    article = _article(
        "从产业发展来看，行业仍在演进。"
        "本届世界人工智能大会，强脑科技也带来了脑控机器人训练平台和两项全球首发技术。",
        title="专家观点：机器人产业进入关键期",
    )
    entities = build_article_entity_ledger(article, [], [])
    ledger = build_action_span_ledger(article, entities)
    company = entities.entity_for_name("强脑科技")

    assert company is not None
    assert any(
        company.entity_id in claim.allowed_subject_entity_ids
        and "technical_milestone" in claim.allowed_event_types
        for claim in ledger.claims
    )


def test_quote_coreference_can_inherit_previous_speaker_company() -> None:
    article = _article(
        "另一位是优必选副总裁焦某，他介绍产业进展。"
        "包括我们在内，其实我们已经开始规模化部署工业人形机器人。",
        title="专家观点：机器人应用",
    )
    entities = build_article_entity_ledger(article, [], [])
    ledger = build_action_span_ledger(article, entities)
    company = entities.entity_for_name("优必选")

    assert company is not None
    assert any(
        company.entity_id in claim.allowed_subject_entity_ids
        and "customer_validation" in claim.allowed_event_types
        for claim in ledger.claims
    )


def test_roadmap_patterns_cover_headquarters_registration_launch_and_exploration() -> None:
    _, ledger = _build(
        "格式塔完成4.2亿天使+轮融资。"
        "格式塔上海总部也正式启用。"
        "其第一代产品预计将在年底正式发布，并计划在1-2年内完成国内注册上市。"
        "据悉，格式塔第一代自研Foundation Model将在年底亮相。"
        "格式塔亦在探索建立一个Foundation Model。",
        title="格式塔完成融资并披露产品路线图",
    )

    assert any(claim.event_type_hint == "funding" for claim in ledger.claims)
    assert any(
        claim.event_type_hint == "new_site_or_entity"
        and "总部" in claim.action_text
        for claim in ledger.claims
    )
    assert any(
        claim.event_type_hint == "regulatory_or_clinical"
        and "注册上市" in claim.action_text
        for claim in ledger.claims
    )
    assert any("亮相" in claim.action_text for claim in ledger.claims)
    assert any(
        claim.event_type_hint == "open_action"
        and "探索" in claim.action_text
        and "technical_milestone" in claim.allowed_event_types
        for claim in ledger.claims
    )


def test_atomic_claims_split_parallel_clinical_customer_and_result_actions() -> None:
    _, ledger = _build(
        "暖芯迦科技已启动视网膜接口产品人体临床试验，"
        "其视皮层路径产品也将在今年进入前瞻性临床试验。"
        "中科原动力科技完成融资。"
        "中科原动力科技已经在大田场景实现商业化应用，并在海外开展产品验证和示范。"
        "中美瑞康生物发布技术进展。"
        "中美瑞康生物早期临床数据显示实现67%完全缓解率，已获FDA快速通道资格。"
        "品善生物科技完成融资。"
        "品善生物科技获得了头部客户认可，实现了国内最大规模的商业化生产案例。",
        title="多家公司披露最新进展",
    )

    clinical = [
        claim
        for claim in ledger.claims
        if claim.event_type_hint == "regulatory_or_clinical"
    ]
    customer = [
        claim
        for claim in ledger.claims
        if claim.event_type_hint == "customer_validation"
    ]
    assert any(
        "启动" in claim.action_text and "人体临床试验" in claim.action_text
        for claim in clinical
    )
    assert any("进入前瞻性临床" in claim.action_text for claim in clinical)
    assert any("67%完全缓解率" in claim.action_text for claim in clinical)
    assert any("FDA快速通道资格" in claim.action_text for claim in clinical)
    assert any("商业化应用" in claim.action_text for claim in customer)
    assert any("海外开展产品验证" in claim.action_text for claim in customer)
    assert any("客户认可" in claim.action_text for claim in customer)
    assert any("商业化生产案例" in claim.action_text for claim in customer)


def test_interview_company_role_binds_coreferential_customer_claim() -> None:
    article = _article(
        "另一位是优必选副总裁、研究院院长焦继超，欢迎。"
        "王昊：行业仍需观察。"
        "焦继超：包括我们在内，已经开始规模化部署工业人形机器人。",
        title="产业评论",
    )
    entities = build_article_entity_ledger(article, [], [])
    ledger = build_action_span_ledger(article, entities)
    company = entities.entity_for_name("优必选")
    claims = [
        claim
        for claim in ledger.claims
        if claim.event_type_hint == "customer_validation"
        and "规模化部署" in claim.action_text
    ]

    assert company is not None
    assert claims
    assert any(company.entity_id in claim.allowed_subject_entity_ids for claim in claims)
