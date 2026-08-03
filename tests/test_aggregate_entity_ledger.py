from __future__ import annotations

from dataclasses import replace

from ht_lead_radar.aggregate_adapters.entity_ledger import (
    bind_candidate_subjects,
    build_article_entity_ledger,
    _iter_english_context_entities,
)
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex


def _article(body: str, title: str = "") -> CleanArticle:
    return CleanArticle(
        index=SourceArticleIndex(
            source_id="source",
            source_article_id="1",
            channel="news",
            canonical_url="https://example.invalid/1",
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


def test_ledger_binds_explicit_alias_to_legal_company() -> None:
    body = "北京层浪生物科技有限公司（以下简称“层浪生物”）完成融资。"
    ledger = build_article_entity_ledger(_article(body), [], [])

    entity = ledger.entity_for_name("层浪生物")
    assert entity is not None
    assert entity.canonical_name == "北京层浪生物科技有限公司"
    assert entity.operating_subject_eligible is True


def test_ledger_binds_enterprise_short_name_to_legal_company() -> None:
    body = (
        "杭州甲辰电子科技有限公司（企业简称：甲辰）完成战略融资。"
        "甲辰已启动临床试验。"
    )
    ledger = build_article_entity_ledger(_article(body), [], [])

    alias = ledger.entity_for_name("甲辰")
    assert alias is not None
    assert alias.canonical_name == "杭州甲辰电子科技有限公司"


def test_ledger_discovers_company_suffix_surfaces_without_action_left_parse() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "国产EDA企业代表芯和半导体在现场联合乙巳科技股份有限公司发布合作声明。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("芯和半导体") is not None
    assert ledger.entity_for_name("乙巳科技股份有限公司") is not None


def test_ledger_binds_inline_bilingual_company_surfaces() -> None:
    ledger = build_article_entity_ledger(
        _article("逐际动力LimX Dynamics宣布完成Pre-IPO轮融资。"), [], []
    )

    combined = ledger.entity_for_name("逐际动力LimX Dynamics")
    assert combined is not None
    assert ledger.entity_for_name("逐际动力") == combined
    assert ledger.entity_for_name("LimX Dynamics") == combined


def test_legal_company_allows_editorial_whitespace_inside_chinese_name() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "中 美瑞康核酸技术（南通）研究院有限公司（以下简称“中 美瑞康”）"
            "宣布完成新一轮融资。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("中 美瑞康核酸技术（南通）研究院有限公司") is not None


def test_parenthetical_english_alias_keeps_spaced_chinese_legal_owner() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "中 美瑞康核酸技术（南通）研究院有限公司（以下简称“中 美瑞康”）"
            "披露最新进展。中 美瑞康（RactigenTherapeutics）宣布完成融资。"
        ),
        [],
        [],
    )

    legal = ledger.entity_for_name(
        "中 美瑞康核酸技术（南通）研究院有限公司"
    )
    assert legal is not None
    assert ledger.entity_for_name("RactigenTherapeutics") == legal


def test_executive_role_does_not_absorb_deputy_prefix_into_company() -> None:
    ledger = build_article_entity_ledger(
        _article("另一位是优必选副总裁焦某，他介绍了工业机器人部署。"), [], []
    )

    assert ledger.entity_for_name("优必选") is not None
    assert ledger.entity_for_name("优必选副") is None


def test_appointment_prefix_and_financial_fragments_are_not_companies() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "建议宋爱平不再担任深圳市特发集团有限公司监事。"
            "本次定增发行价格不低于定价基准日。"
            "产能爬坡和批量交付正在推进。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("深圳市特发集团有限公司") is not None
    eligible = {entity.canonical_name for entity in ledger.eligible()}
    assert eligible.isdisjoint(
        {
            "建议宋爱平不再担任深圳市特发集团有限公司",
            "本次",
            "发行价格不低于定价基准日",
            "产能爬坡",
        }
    )


def test_action_objects_inside_long_bullets_are_not_company_anchors() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "4、携手国内头部高校落地协同攻关项目，打造跨尺度体系。"
            "该技术向下穿透至细胞、基因维度完成生理机理映射，"
            "团队已提前开展研究。"
        ),
        [],
        [],
    )

    eligible = {entity.canonical_name for entity in ledger.eligible()}
    assert eligible.isdisjoint(
        {"携手国内头部高校", "基因维度", "团队已提前", "搭建"}
    )


def test_ledger_keeps_suffixless_brands_and_rejects_fragments() -> None:
    body = (
        "DeepSeek-V4正式版API已上线公测。"
        "机器人公司白犀牛完成B轮融资。"
        "人工智能应用赛道紧扣AI从技术突破迈向落地。"
    )
    ledger = build_article_entity_ledger(
        _article(body, "DeepSeek-V4正式版来了"),
        [],
        [],
    )

    assert ledger.entity_for_name("DeepSeek").operating_subject_eligible is True
    assert ledger.entity_for_name("白犀牛").operating_subject_eligible is True
    assert ledger.entity_for_name("人工智能应用赛道紧扣AI从") is None


def test_candidate_binding_uses_only_ledger_entities_in_quote() -> None:
    body = "甲辰科技与乙巳机器人签署合作协议。"
    candidate = {
        "id": "c_1",
        "claim_id": "c_1",
        "required_claim_ids": ["c_1"],
        "quote": body,
        "subject_hint": "甲辰科技",
    }
    ledger = build_article_entity_ledger(_article(body), [candidate], [])

    bound = bind_candidate_subjects([candidate], ledger)[0]

    assert len(bound["allowed_subject_entity_ids"]) == 2
    assert bound["primary_subject_entity_id"] == ledger.entity_for_name(
        "甲辰科技"
    ).entity_id


def test_entity_ids_are_stable_when_candidate_order_changes() -> None:
    body = "甲辰科技完成A轮融资。乙巳机器人完成B轮融资。"
    candidates = [
        {"quote": body[:13], "subject_hint": "甲辰科技"},
        {"quote": body[13:], "subject_hint": "乙巳机器人"},
    ]

    first = build_article_entity_ledger(_article(body), candidates, [])
    second = build_article_entity_ledger(_article(body), reversed(candidates), [])

    assert {
        item.canonical_name: item.entity_id for item in first.entities
    } == {item.canonical_name: item.entity_id for item in second.entities}


def test_action_verb_is_not_absorbed_into_entity_name() -> None:
    ledger = build_article_entity_ledger(
        _article("谷歌DeepMind宣布推出新一代机器人模型。"), [], []
    )

    assert ledger.entity_for_name("谷歌DeepMind") is not None
    assert ledger.entity_for_name("谷歌DeepMind宣布") is None


def test_placeholder_company_and_vc_lineage_are_not_operating_subjects() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "L4自动驾驶公司完成一笔新融资。"
            "公开资料显示，毅达资本由江苏高科技投资集团内部改革组建。"
            "近日，毅达资本宣布董事长退休。"
        ),
        [],
        [],
    )

    placeholder = ledger.entity_for_name("L4自动驾驶公司")
    assert placeholder is None or placeholder.operating_subject_eligible is False
    vc = ledger.entity_for_name("毅达资本")
    assert vc is not None
    assert vc.operating_subject_eligible is False
    assert ledger.entity_for_name("毅达资本由江苏高科技投资集团") is None


def test_listed_tickers_are_high_confidence_company_surfaces() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "①今日共有2只新股申购，为创业板的超纯应材（301717）、"
            "科创板的国仪公司（688828）。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("超纯应材").operating_subject_eligible is True
    assert ledger.entity_for_name("国仪公司").operating_subject_eligible is True
    assert ledger.entity_for_name("创业板") is None
    assert ledger.entity_for_name("科创板") is None


def test_direct_subject_cleanup_removes_trailing_adverbs() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "7月31日，字节跳动今天发布公告，宣布推出视频模型。"
            "华大九天还发布了两款EDA产品。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("字节跳动").operating_subject_eligible is True
    assert ledger.entity_for_name("华大九天").operating_subject_eligible is True
    assert ledger.entity_for_name("字节跳动今天") is None
    assert ledger.entity_for_name("华大九天还") is None


def test_soft_comma_lookback_retains_descriptor_grounded_subjects() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "其中，融资额最高的为AI无人机防御平台开发商Spur Intelligence，"
            "宣布完成2亿美元种子轮融资。近日，国内大型无人运输机企业"
            "华鹰航空宣布完成A轮融资。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("Spur Intelligence").operating_subject_eligible is True
    assert ledger.entity_for_name("华鹰航空").operating_subject_eligible is True


def test_contextual_bilingual_parentheses_create_aliases() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "RoboParty（萝博派对）宣布完成融资。"
            "深朴智能（Simple AI）宣布完成融资。"
        ),
        [],
        [],
    )

    robo_en = ledger.entity_for_name("RoboParty")
    robo_zh = ledger.entity_for_name("萝博派对")
    simple_zh = ledger.entity_for_name("深朴智能")
    simple_en = ledger.entity_for_name("Simple AI")
    assert robo_en is not None and robo_zh is not None
    assert robo_en.entity_id == robo_zh.entity_id
    assert simple_zh is not None and simple_en is not None
    assert simple_zh.entity_id == simple_en.entity_id
    assert robo_en.operating_subject_eligible is True
    assert simple_zh.operating_subject_eligible is True


def test_action_front_multi_subjects_are_split() -> None:
    ledger = build_article_entity_ledger(
        _article("NAVER、Brookfield与英伟达宣布扩建人工智能工厂。"),
        [],
        [],
    )

    assert ledger.entity_for_name("NAVER").operating_subject_eligible is True
    assert ledger.entity_for_name("Brookfield").operating_subject_eligible is False
    assert ledger.entity_for_name("英伟达").operating_subject_eligible is True


def test_relational_noise_is_not_a_lead_and_semantic_scope_is_preserved() -> None:
    participants = build_article_entity_ledger(
        _article("NAVER、Brookfield与英伟达宣布扩建人工智能工厂。"), [], []
    )
    fund = build_article_entity_ledger(
        _article("SevenX完成数千万美元基金首关。"), [], []
    )
    career = build_article_entity_ledger(
        _article("由前地平线副总裁张玉峰创立的无界动力完成融资。"), [], []
    )
    venue_ledger = build_article_entity_ledger(
        _article("承办单位上海马桥人工智能创新试验区建设发展有限公司。"), [], []
    )

    assert participants.entity_for_name("NAVER").operating_subject_eligible is True
    assert participants.entity_for_name("Brookfield").operating_subject_eligible is False
    assert fund.entity_for_name("SevenX").operating_subject_eligible is False
    assert career.entity_for_name("前地平线").operating_subject_eligible is False
    venue = venue_ledger.entity_for_name("上海马桥人工智能创新试验区建设发展有限公司")
    assert venue is None or venue.operating_subject_eligible is False


def test_generic_lab_token_is_not_a_lead_and_location_brand_merges() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "星能玄光完成5亿元融资。公开资料显示，合肥星能玄光科技有限责任公司"
            "专注于核聚变装置工程化。RoboParty获得开发者labs的积极反馈。"
        ),
        [],
        [],
    )

    star = ledger.entity_for_name("星能玄光")
    assert star is not None
    assert star.canonical_name == "合肥星能玄光科技有限责任公司"
    assert "星能玄光" in star.aliases
    assert ledger.entity_for_name("labs") is None


def test_title_bulletin_and_ticker_surface_are_eligible() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "怡亚通（002183）公告，公司拟公开挂牌转让股权。",
            "怡亚通：拟公开挂牌转让参股公司股权",
        ),
        [],
        [],
    )

    # Preserve the listed company for semantic recall, but keep passive equity
    # transfers out of the proactive lead scope.
    assert ledger.entity_for_name("怡亚通").operating_subject_eligible is True
    assert ledger.entity_for_name("怡亚通").lead_scope_eligible is False


def test_enumerative_consortium_prefix_is_not_part_of_legal_name() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "西物院牵头，联合荣信汇科电气股份有限公司、"
            "中国核工业二三建设有限公司组成联合体，正式签署项目合同。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("荣信汇科电气股份有限公司") is not None
    assert ledger.entity_for_name("联合荣信汇科电气股份有限公司") is None


def test_organization_roles_surface_company_entities() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "三星电子会长与OpenAI首席执行官会面。"
            "Thinking Machines Lab联合创始人宣布退出。"
        ),
        [],
        [],
    )

    for name in ("三星电子", "OpenAI", "Thinking Machines Lab"):
        assert ledger.entity_for_name(name).operating_subject_eligible is True


def test_entity_lookup_uses_unique_bidirectional_suffix_aliases() -> None:
    ledger = build_article_entity_ledger(_article("甲辰宣布完成投资。"), [], [])

    entity = ledger.entity_for_name("甲辰集团")
    assert entity is not None
    assert entity.canonical_name == "甲辰"


def test_reporting_and_adverb_fragments_remain_ineligible() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "马斯克表示公司将快速推出产品。快速发布新版本。陆续上线服务。"
            "数周后推出工具。官方发布公告。点评：官方发布新品。"
        ),
        [],
        [],
    )
    eligible_names = {entity.canonical_name for entity in ledger.eligible()}

    assert eligible_names.isdisjoint(
        {"马斯克", "快速", "陆续", "数周后", "官方", "点评"}
    )


def test_generic_organization_actions_surface_both_sides() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "甲辰科技组建事业部。甲辰科技与乙巳智能会面磋商。"
            "甲辰科技产品进入交付阶段。英伟达向甲辰科技投资。"
        ),
        [],
        [],
    )

    for name in ("甲辰科技", "乙巳智能", "英伟达"):
        assert ledger.entity_for_name(name).operating_subject_eligible is True


def test_internal_company_reference_is_available_as_operating_subject() -> None:
    ledger = build_article_entity_ledger(
        _article("该实验室于今年5月在元戎启行内部成立，目前已完成基础设施重构。"),
        [],
        [],
    )

    entity = ledger.entity_for_name("元戎启行")
    assert entity is not None
    assert entity.operating_subject_eligible is True


def test_chinese_digit_brand_alias_merges_with_numeric_group_name() -> None:
    ledger = build_article_entity_ledger(
        _article("三六零发布智能体平台。360集团创始人介绍新品。"),
        [],
        [],
    )

    assert ledger.entity_for_name("三六零") == ledger.entity_for_name("360集团")


def test_article_local_product_owner_aliases_do_not_become_companies() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "xAI面向Grok推出Build模型。Grok Build模型正在测试。"
            "阿里云Qoder上线Qoder Voice产品。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("Grok").canonical_name == "xAI"
    assert ledger.entity_for_name("Qoder").canonical_name == "阿里云"


def test_generic_chinese_descriptor_prefers_attached_latin_owner() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "游戏引擎公司Unity中国在上海发布团结引擎2.0，"
            "并推出游戏开发AI Agent团结Codely。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("Unity中国").canonical_name == "Unity中国"
    assert ledger.entity_for_name("游戏引擎公司") is None


def test_department_and_official_product_references_bind_to_parent_company() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "字节已组建豆包办公部门。"
            "据腾讯官方微信公众号，企业微信AI助理近日开启内测。"
        ),
        [],
        [],
    )

    assert ledger.entity_for_name("豆包").canonical_name == "字节"
    assert ledger.entity_for_name("微信").canonical_name == "腾讯"


def test_app_surface_and_executive_person_fragment_are_not_operating_companies() -> None:
    ledger = build_article_entity_ledger(
        _article("灵光APP上线新功能。三星电子会长李在镕与甲辰科技会面磋商合作。"),
        [],
        [],
    )
    eligible = {entity.canonical_name for entity in ledger.eligible()}

    assert "灵光APP" not in eligible
    assert "三星李在镕" not in eligible


def test_capability_and_prediction_fragments_are_not_operating_companies() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "灵光APP上线闪应用‘一键部署’功能，可通过灵光提供的部署Skill发布。"
            "Gemini4有望年底发布，谷歌表示我们目前正在训练Gemini4。"
        ),
        [],
        [],
    )
    eligible = {entity.canonical_name for entity in ledger.eligible()}

    assert "部署" not in eligible
    assert "Gemini4有望年底" not in eligible


def test_english_capital_firm_and_collective_are_not_operating_companies() -> None:
    ledger = build_article_entity_ledger(
        _article(
            "GHO Capital宣布合并。中方联合体正式签署合同。"
            "荣信汇科电气股份有限公司正式签署合同。"
        ),
        [],
        [],
    )
    eligible = {entity.canonical_name for entity in ledger.eligible()}

    assert "GHOCapital" not in eligible
    assert "中方联合体" not in eligible
    assert "荣信汇科电气股份有限公司" in eligible


def test_bilingual_company_after_descriptor_is_not_swallowed_by_prose() -> None:
    body = (
        "品善生物近期与全球知名的生命科学公司"
        "Repligen（瑞普利金）围绕过滤产品达成战略合作。"
    )
    ledger = build_article_entity_ledger(
        _article(body, title="品善生物与Repligen达成合作"),
        [],
        [],
    )

    repligen = ledger.entity_for_name("Repligen")
    assert repligen is not None
    assert repligen.canonical_name == "Repligen"
    assert repligen.operating_subject_eligible is True
    assert ledger.entity_for_name("瑞普利金").entity_id == repligen.entity_id


def test_interview_speaker_scope_binds_only_introduced_company_role() -> None:
    body = (
        "另一位是优必选副总裁、研究院院长焦继超，欢迎。"
        "王昊：行业仍需观察。"
        "焦继超：包括我们在内，已经开始规模化部署工业人形机器人。"
        "王昊：这是产业趋势。"
    )
    ledger = build_article_entity_ledger(
        _article(body, title="产业评论"),
        [],
        [],
    )
    company = ledger.entity_for_name("优必选")
    assert company is not None and company.operating_subject_eligible
    quote_start = body.index("包括我们")
    quote_end = body.index("王昊：这是")
    assert ledger.contextual_subject_ids(quote_start, quote_end) == (
        company.entity_id,
    )


def test_canonical_boundaries_strip_parent_prose_modals_and_conjunctions() -> None:
    body = (
        "北京中科原动力科技有限公司宣布完成融资，本轮由奇瑞控股集团旗下"
        "安徽奇瑞智能科技有限公司投资。"
        "中微公司公告称，全资子公司中微临港拟与智微资本共同出资。"
        "国产EDA企业代表芯和半导体在现场联合诺瓦星云科技股份有限公司"
        "发布战略合作声明。"
    )
    ledger = build_article_entity_ledger(_article(body), [], [])

    assert ledger.entity_for_name("安徽奇瑞智能科技有限公司").canonical_name == (
        "安徽奇瑞智能科技有限公司"
    )
    assert ledger.entity_for_name("中微临港").canonical_name == "中微临港"
    assert ledger.entity_for_name("芯和半导体").canonical_name == "芯和半导体"
    assert ledger.entity_for_name("奇瑞控股集团旗下安徽奇瑞智能科技有限公司") is None
    assert ledger.entity_for_name("中微临港拟与") is None
    assert ledger.entity_for_name("芯和半导体与") is None


def test_defined_technical_concept_cannot_self_seed_as_company() -> None:
    body = (
        "研究团队最近提出名为“三元智能”的崭新概念。"
        "三元智能将脑机接口、AI和具身智能融合。"
    )
    ledger = build_article_entity_ledger(_article(body), [], [])
    concept = ledger.entity_for_name("三元智能")

    assert concept is not None
    assert concept.operating_subject_eligible is False


def test_media_company_and_explicit_wholly_owned_child_are_out_of_scope() -> None:
    body = (
        "视觉(中国)文化发展股份有限公司发布公告称，其全资子公司"
        "北京华夏视觉科技集团有限公司拟出资参投基金。"
        "分众传媒发布公告称，其全资子公司上海分众鸿意信息技术有限公司"
        "拟作为LP出资。"
    )
    ledger = build_article_entity_ledger(_article(body), [], [])

    for name in (
        "视觉(中国)文化发展股份有限公司",
        "北京华夏视觉科技集团有限公司",
        "分众传媒",
        "上海分众鸿意信息技术有限公司",
    ):
        entity = ledger.entity_for_name(name)
        assert entity is not None
        assert entity.operating_subject_eligible is False


def test_industrial_parent_and_investing_child_remain_in_scope() -> None:
    body = (
        "中微公司发布公告称，其全资子公司中微临港拟出资参投基金。"
        "中微公司是半导体设备企业，中微临港已完成本轮投资。"
    )
    ledger = build_article_entity_ledger(_article(body), [], [])

    assert ledger.entity_for_name("中微公司").operating_subject_eligible is True
    assert ledger.entity_for_name("中微临港").operating_subject_eligible is True


def test_adapter_metadata_company_overrides_editorial_fragment() -> None:
    legal = "\u4e0a\u6d77\u6c49\u79be\u751f\u7269\u65b0\u6750\u6599\u79d1\u6280\u6709\u9650\u516c\u53f8"
    article = replace(
        _article(
            f"{legal}\u5ba3\u5e03\u5b8c\u6210\u6570\u5343\u4e07\u5143\u6218\u7565\u878d\u8d44\u3002\u6c49\u79be\u751f\u7269\u5df2\u5148\u540e\u5b8c\u6210\u591a\u9879\u4ea7\u4e1a\u5408\u4f5c\u3002"
        ),
        structured_data={
            "company": legal,
            "company_mentions": [legal, "\u6c49\u79be\u751f\u7269"],
        },
    )

    ledger = build_article_entity_ledger(article, [], [])

    eligible = ledger.eligible()
    assert [entity.canonical_name for entity in eligible] == [legal]
    assert not any("\u5df2\u5148\u540e" in entity.canonical_name for entity in eligible)
    assert "adapter_metadata_company" in ledger.entity_for_name(legal).discovery_sources


def test_adapter_metadata_company_prevents_m_and_a_transaction_subject() -> None:
    article = replace(
        _article(
            "\u534e\u6da6\u53cc\u9e64\u62df\u4ee5\u73b0\u91d1\u6536\u8d2d\u5229\u5c14\u5316\u5b66\u0023\u0023\u80a1\u4efd\uff0c\u4ea4\u6613\u5b8c\u6210\u540e\u5c06\u5b9e\u73b0\u63a7\u5236\u3002"
        ),
        structured_data={
            "company": "\u534e\u6da6\u53cc\u9e64",
            "company_mentions": ["\u534e\u6da6\u53cc\u9e64"],
        },
    )

    ledger = build_article_entity_ledger(article, [], [])

    assert [entity.canonical_name for entity in ledger.eligible()] == ["\u534e\u6da6\u53cc\u9e64"]
    transaction = ledger.entity_for_name("\u4ea4\u6613")
    assert transaction is None or not transaction.operating_subject_eligible


def test_policy_issuer_metadata_is_not_promoted_to_operating_company() -> None:
    article = replace(
        _article("\u5de5\u4e1a\u548c\u4fe1\u606f\u5316\u90e8\u53d1\u5e03\u672a\u6765\u4ea7\u4e1a\u521b\u65b0\u4efb\u52a1\u7533\u62a5\u901a\u77e5\u3002"),
        index=replace(
            _article("").index,
            source_id="miit-science-files",
            source_article_id="policy-1",
        ),
        structured_data={
            "company": "\u5de5\u4e1a\u548c\u4fe1\u606f\u5316\u90e8",
            "company_mentions": ["\u5de5\u4e1a\u548c\u4fe1\u606f\u5316\u90e8"],
            "issuing_authority": "\u5de5\u4e1a\u548c\u4fe1\u606f\u5316\u90e8",
        },
    )

    ledger = build_article_entity_ledger(article, [], [])

    assert ledger.eligible() == ()



def test_long_feature_rejects_model_and_example_companies_as_subjects() -> None:
    body = (
        "2.8\u4e07\u4ebf\u53c2\u6570\u7684\u6700\u5927\u5f00\u6e90\u6a21\u578bKimi K3\u53d1\u5e03\u540e\uff0c"
        "\u636e\u62a5\u9053\uff0c\u8be5\u6a21\u578b\u7684\u5f00\u53d1\u5546\u6708\u4e4b\u6697\u9762\u4e5f\u987a\u52bf\u5373\u5c06\u542f\u52a8\u65b0\u4e00\u8f6e\u878d\u8d44\u3002"
        "\u4e00\u4e9b\u6d77\u5916\u5934\u90e8AI\u516c\u53f8\u5df2\u7ecf\u610f\u8bc6\u5230\u8fd9\u79cd\u53d8\u5316\u3002"
        "Anthropic\u63a8\u51faClaude Community Ambassadors\uff0cOpenAI\u4e5f\u56f4\u7ed5Codex\u5efa\u7acb\u5f00\u53d1\u8005\u793e\u533a\u3002"
        + "\u5c3e\u58f0\u3002" * 1000
    )
    article = _article(body, title="\u4f30\u503c3500\u4ebf\uff0c\u4e2d\u56fd\u6a21\u578b\u5168\u7403\u53d1\u5c55")

    ledger = build_article_entity_ledger(article, [], [])

    assert [entity.canonical_name for entity in ledger.eligible()] == ["\u6708\u4e4b\u6697\u9762"]
    eligible_names = {entity.canonical_name for entity in ledger.eligible()}
    assert "K3" not in eligible_names
    assert "Anthropic" not in eligible_names

def test_english_context_scanner_is_bounded_on_long_ascii_runs() -> None:
    for body in (
        "OpenAI今天发布新模型",
        "OpenAI在中国中发布在中国中",
        "OpenAI在中国市场上发布在教育场景中",
    ):
        assert list(_iter_english_context_entities(body))[0][0] == "OpenAI"
    matches = list(_iter_english_context_entities("A" * 20000 + "发布融资"))
    assert len(matches) <= 1
    assert all(end - start <= 80 for _, start, end in matches)
