"""Natural-language opportunity requests and execution planning.

This module is deliberately side-effect free.  In particular it has no file or
database writer: a ``CandidateProfile`` exists only in the returned runtime
plan and is never assigned a persistent identifier.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import re
from typing import Any, Iterable, Mapping


class OpportunityMode(str, Enum):
    """The two explicit business modes supported by the shared backend."""

    MARKET_SCAN = "MARKET_SCAN"
    CANDIDATE_FLOAT = "CANDIDATE_FLOAT"


def _primitive(value: Any) -> Any:
    """Convert nested planning objects into JSON-compatible Python values."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_primitive(item) for item in value]
    return value


class Serializable:
    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class GeographyScope(Serializable):
    code: str
    label: str
    locations: tuple[str, ...]
    explicit: bool
    inclusion_rule: str
    exclusion_rule: str


@dataclass(frozen=True)
class TimePolicy(Serializable):
    lookback_days: int = 180
    recency_boost_days: int = 90
    event_time_basis: str = "event_date_then_publish_date"


@dataclass(frozen=True)
class CandidateProfile(Serializable):
    """An ephemeral, task-local description used only for Candidate Float."""

    role_title: str | None
    seniority: str
    core_capabilities: tuple[str, ...]
    industry_experience: tuple[str, ...]
    leadership_scope: tuple[str, ...]
    geography_preferences: tuple[str, ...]
    desired_directions: tuple[str, ...]
    exclusions: tuple[str, ...]
    inferred_fields: tuple[str, ...]
    missing_critical_fields: tuple[str, ...]
    persistence_policy: str = "runtime_only_not_persisted"


@dataclass(frozen=True)
class ClarifyingQuestion(Serializable):
    question_id: str
    prompt: str
    reason: str
    blocking: bool
    answer_field: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClarificationPlan(Serializable):
    """A progressive queue: ask ``next_question`` before deferred questions."""

    can_execute_exploratory: bool
    next_question: ClarifyingQuestion | None
    deferred_questions: tuple[ClarifyingQuestion, ...]

    @property
    def required(self) -> bool:
        return self.next_question is not None


@dataclass(frozen=True)
class IndustryMap(Serializable):
    topic: str
    canonical_topic: str
    map_kind: str
    core: tuple[str, ...]
    direct_upstream: tuple[str, ...]
    direct_downstream: tuple[str, ...]
    adjacent: tuple[str, ...]
    query_terms: tuple[str, ...]
    signal_terms: tuple[str, ...]
    main_result_layers: tuple[str, ...] = ("core", "direct_upstream", "direct_downstream")
    adjacent_policy: str = "visible_watchlist_not_main_gate"
    boundary_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class HardGates(Serializable):
    director_plus_role_hypothesis_required: bool = True
    pre_job_upstream_signal_required: bool = True
    below_director_or_individual_contributor_excluded: bool = True
    job_ad_only_excluded_from_main_ranking: bool = True
    relaxable_for_target_count: bool = False


@dataclass(frozen=True)
class ResultPolicy(Serializable):
    target_company_count: int = 20
    ranking_order: str = "score_descending"
    lower_soft_threshold_to_fill: bool = True
    fabricate_to_fill: bool = False
    explain_every_score_component: bool = True
    late_job_ad_only_destination: str = "late_opportunity_appendix"
    all_daily_companies_research_depth: str = "basic"
    float_research_depth: str = "deep"


@dataclass(frozen=True)
class SourceStrategy(Serializable):
    discovery_priority: tuple[str, ...] = (
        "registered_fixed_sources",
        "reusable_sector_source_packages",
        "stable_government_regulatory_and_company_sources",
        "public_search_fallback",
    )
    metaso_role: str = "high_value_verification_only"
    prefer_primary_sources: bool = True
    preserve_source_provenance: bool = True


@dataclass(frozen=True)
class OpportunityRequest(Serializable):
    raw_text: str
    mode: OpportunityMode
    mode_confidence: str
    mode_reason: str
    industry_topic: str | None
    target_seniority: str
    target_role: str | None
    geography: GeographyScope
    time_policy: TimePolicy
    candidate_profile: CandidateProfile | None
    deep_research_requested: bool


@dataclass(frozen=True)
class ExecutionStage(Serializable):
    stage_id: str
    name: str
    purpose: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    run_if: str = "always"
    cost_tier: str = "low"


@dataclass(frozen=True)
class OpportunityExecutionPlan(Serializable):
    request: OpportunityRequest
    clarification: ClarificationPlan
    industry_map: IndustryMap | None
    hard_gates: HardGates
    result_policy: ResultPolicy
    source_strategy: SourceStrategy
    discovery_queries: tuple[str, ...]
    stages: tuple[ExecutionStage, ...]
    can_execute_now: bool
    plan_version: str = "1.0"


_MODE_FLOAT_TERMS = (
    "candidate float",
    "candidate_float",
    "float候选人",
    "float 候选人",
    "做float",
    "做 float",
    "候选人",
    "手上有一个",
    "手上有一位",
    "反向找",
    "哪些公司可能会要他",
    "哪些公司可能会要她",
    "哪些公司会要他",
    "哪些公司会要她",
)

_MODE_MARKET_TERMS = (
    "market scan",
    "market_scan",
    "行业有哪些公司",
    "产业有哪些公司",
    "赛道有哪些公司",
    "哪些公司可能要招",
    "哪些企业可能要招",
    "公司可能要招总监",
)

_KNOWN_INDUSTRIES: dict[str, dict[str, Any]] = {
    "脑机接口": {
        "aliases": ("脑机接口", "bci", "brain-computer interface", "神经接口"),
        "core": (
            "植入式脑机接口",
            "非侵入式脑机接口",
            "神经信号采集与解码",
            "脑机接口系统与设备",
        ),
        "direct_upstream": (
            "神经电极与电极阵列",
            "神经信号专用芯片与模拟前端",
            "生物相容材料",
            "脑电与神经传感器",
        ),
        "direct_downstream": (
            "神经康复与辅助沟通",
            "神经疾病诊疗",
            "医疗级人机交互",
            "脑机接口临床与产业化应用",
        ),
        "adjacent": ("消费级脑电可穿戴", "泛神经科技", "医疗AI", "普通智能穿戴"),
        "notes": ("主榜要求产品或项目与神经信号闭环存在明确业务关联。",),
    },
    "半导体": {
        "aliases": ("半导体", "芯片", "集成电路", "ic产业", "semiconductor"),
        "core": ("芯片设计", "晶圆制造", "封装测试", "半导体设备", "半导体材料"),
        "direct_upstream": (
            "EDA与半导体IP",
            "光刻胶与电子特气",
            "精密零部件与真空系统",
            "硅片与化合物半导体衬底",
        ),
        "direct_downstream": (
            "汽车电子与功率器件应用",
            "AI算力与数据中心芯片应用",
            "工业控制与消费电子",
        ),
        "adjacent": ("泛电子元器件", "PCB与模组", "整机品牌", "云计算服务"),
        "notes": ("默认覆盖设计、制造、封测、设备和材料全产业链。",),
    },
    "商业航天": {
        "aliases": ("商业航天", "民营航天", "商业太空", "commercial space", "newspace"),
        "core": ("商业火箭与发射服务", "商业卫星制造", "卫星星座运营", "在轨服务"),
        "direct_upstream": (
            "火箭发动机与推进系统",
            "航天电子与测控部件",
            "复合材料与精密制造",
            "卫星载荷与关键分系统",
        ),
        "direct_downstream": (
            "卫星通信",
            "商业遥感",
            "卫星导航增强",
            "地面站与卫星数据服务",
        ),
        "adjacent": ("传统军工航天", "无人机", "通用航空", "纯地理信息软件"),
        "notes": ("卫星应用只有在存在明确商业航天资产或采购关系时进入主榜。",),
    },
    "核聚变": {
        "aliases": ("核聚变", "可控核聚变", "聚变能源", "fusion energy", "nuclear fusion"),
        "core": ("磁约束聚变装置", "惯性约束聚变", "聚变堆工程", "商业聚变能源"),
        "direct_upstream": (
            "高温超导磁体",
            "真空与低温系统",
            "等离子体诊断与控制",
            "聚变堆材料与氚系统",
        ),
        "direct_downstream": ("聚变示范电站", "聚变工程服务", "高能工业热应用"),
        "adjacent": ("核裂变", "通用新能源", "储能", "普通超导应用"),
        "notes": ("主榜排除仅以核裂变或泛新能源为主营的企业。",),
    },
    "具身智能": {
        "aliases": (
            "具身智能",
            "人形机器人",
            "通用机器人",
            "embodied intelligence",
            "humanoid robot",
        ),
        "core": (
            "具身智能本体",
            "人形机器人",
            "机器人基础模型",
            "具身数据闭环与训练平台",
        ),
        "direct_upstream": (
            "灵巧手与末端执行器",
            "关节模组与伺服系统",
            "力触觉与视觉传感器",
            "机器人芯片与控制器",
        ),
        "direct_downstream": ("工业机器人应用", "仓储物流机器人", "商业服务机器人", "家庭机器人"),
        "adjacent": ("传统自动化集成", "智能汽车", "纯大模型应用", "消费硬件"),
        "notes": ("传统自动化公司须有明确具身智能产品、项目或团队扩张信号。",),
    },
}

_SIGNAL_TERMS = (
    "融资",
    "增资",
    "产业基金",
    "订单",
    "中标",
    "战略合作",
    "扩产",
    "量产",
    "工厂",
    "基地",
    "产线",
    "交付",
    "获批",
    "注册",
    "临床",
    "许可",
    "技术里程碑",
    "高管变动",
)

_ROLE_CAPABILITY_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("数据采集", "数据闭环"),
        ("数据采集战略", "多源数据采集体系", "数据质量与治理", "数据闭环建设"),
    ),
    (("算法", "ai"), ("算法研发管理", "模型工程化", "研发团队建设")),
    (("研发", "技术"), ("技术路线规划", "研发组织管理", "产品工程化")),
    (("制造", "量产", "运营"), ("产能规划", "量产爬坡", "制造运营管理")),
    (("供应链", "采购"), ("供应链体系建设", "供应商管理", "成本与交付管理")),
    (("销售", "商业化", "业务"), ("商业化策略", "大客户拓展", "销售组织建设")),
    (("产品",), ("产品战略", "产品组合管理", "跨部门产品交付")),
    (("人力", "hr"), ("组织发展", "人才战略", "人力资源体系建设")),
)

_BUSINESS_CONTEXT_TERMS = (
    "自动驾驶",
    "路采",
    "真机数采",
    "具身智能",
    "机器人",
    "互联网",
    "数据平台",
    "工业ai",
    "工业AI",
    "脑机接口",
    "半导体",
    "商业航天",
    "核聚变",
    "医疗",
    "消费电子",
)

_CN_LOCATIONS = (
    "中国大陆",
    "全国",
    "北京",
    "上海",
    "深圳",
    "广州",
    "杭州",
    "苏州",
    "南京",
    "成都",
    "武汉",
    "西安",
    "合肥",
    "天津",
    "重庆",
    "长三角",
    "珠三角",
    "大湾区",
)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip(" ，。；;、")
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)
    return tuple(result)


def detect_mode(text: str) -> tuple[OpportunityMode, str, str]:
    """Detect one of the two explicit modes; Float wins over market wording."""

    normalized = text.strip().lower()
    float_hits = [term for term in _MODE_FLOAT_TERMS if term.lower() in normalized]
    if float_hits:
        return (
            OpportunityMode.CANDIDATE_FLOAT,
            "high",
            f"检测到候选人反向找公司的表达：{float_hits[0]}",
        )
    market_hits = [term for term in _MODE_MARKET_TERMS if term.lower() in normalized]
    if market_hits or any(marker in normalized for marker in ("行业", "产业", "赛道", "领域")):
        reason = market_hits[0] if market_hits else "行业/产业/赛道/领域"
        return OpportunityMode.MARKET_SCAN, "high", f"检测到行业正向扫描表达：{reason}"
    return (
        OpportunityMode.MARKET_SCAN,
        "low",
        "未检测到候选人 Float 表达，暂按 Market Scan 处理并等待行业边界确认",
    )


def _known_industry(text: str) -> str | None:
    normalized = text.lower()
    matches: list[tuple[int, str]] = []
    for canonical, definition in _KNOWN_INDUSTRIES.items():
        for alias in definition["aliases"]:
            if alias.lower() in normalized:
                matches.append((len(alias), canonical))
    return max(matches, default=(0, ""))[1] or None


def extract_industry_topic(text: str) -> str | None:
    known = _known_industry(text)
    if known:
        return known
    compact = re.sub(r"\s+", "", text)
    patterns = (
        r"(?:最近|近来|过去[^，。？?]{0,8})?(?P<topic>[\u4e00-\u9fffA-Za-z0-9+.-]{2,24}?)(?:行业|产业|赛道|领域)",
        r"(?:做|从事|聚焦|关注)(?P<topic>[\u4e00-\u9fffA-Za-z0-9+.-]{2,24}?)(?:的)?(?:公司|企业)",
    )
    stop_prefixes = ("请问", "帮我看看", "帮我查", "我想了解", "有哪些")
    for pattern in patterns:
        match = re.search(pattern, compact, re.IGNORECASE)
        if not match:
            continue
        topic = match.group("topic")
        for prefix in stop_prefixes:
            topic = topic.removeprefix(prefix)
        topic = re.sub(r"^(最近|国内|中国|全球)", "", topic)
        if 2 <= len(topic) <= 24:
            return topic
    return None


def extract_geography(text: str) -> GeographyScope:
    normalized = text.lower()
    if any(term in normalized for term in ("全球", "global", "worldwide")):
        return GeographyScope(
            code="GLOBAL",
            label="全球招聘市场",
            locations=("全球",),
            explicit=True,
            inclusion_rule="纳入全球范围内与目标行业和岗位假设相关的企业。",
            exclusion_rule="排除无法形成总监级岗位假设或没有招聘前上游信号的企业。",
        )
    if any(term in normalized for term in ("大中华区", "greater china", "港澳台")):
        return GeographyScope(
            code="GREATER_CHINA_HIRING_MARKET",
            label="大中华区招聘市场",
            locations=("中国大陆", "香港", "澳门", "台湾"),
            explicit=True,
            inclusion_rule="纳入在大中华区拥有团队、研发、生产、项目或扩张动作的企业。",
            exclusion_rule="排除与大中华区招聘市场没有可解释关联的企业。",
        )
    overseas = tuple(
        location
        for location in ("新加坡", "日本", "韩国", "美国", "欧洲", "德国", "英国", "法国")
        if location.lower() in normalized
    )
    if overseas:
        return GeographyScope(
            code="EXPLICIT_MARKET",
            label=f"{'、'.join(overseas)}招聘市场",
            locations=overseas,
            explicit=True,
            inclusion_rule=f"纳入在{'、'.join(overseas)}拥有实际团队、项目或明确扩张动作的企业。",
            exclusion_rule=f"排除与{'、'.join(overseas)}招聘市场没有可解释关联的企业。",
        )
    cn_locations = tuple(location for location in _CN_LOCATIONS if location in text)
    local_locations = tuple(location for location in cn_locations if location not in ("全国", "中国大陆"))
    if local_locations:
        return GeographyScope(
            code="CN_MAINLAND_LOCAL_HIRING_MARKET",
            label=f"{'、'.join(local_locations)}招聘市场",
            locations=local_locations,
            explicit=True,
            inclusion_rule=(
                f"纳入在{'、'.join(local_locations)}拥有团队、研发、生产、项目、客户交付"
                "或明确扩张动作的中国或外资企业。"
            ),
            exclusion_rule=f"排除与{'、'.join(local_locations)}招聘市场没有可解释关联的企业。",
        )
    return GeographyScope(
        code="CN_MAINLAND_HIRING_MARKET",
        label="中国大陆招聘市场相关性",
        locations=("中国大陆",),
        explicit=False,
        inclusion_rule=(
            "纳入中国企业，以及在中国大陆拥有团队、研发、生产、项目、客户交付"
            "或明确扩张动作的外资企业。"
        ),
        exclusion_rule="排除与中国大陆招聘市场没有可解释关联的海外企业。",
    )


def extract_time_policy(text: str) -> TimePolicy:
    lookback = 180
    day_match = re.search(r"(?:最近|近|过去)\s*(\d{1,3})\s*天", text)
    month_match = re.search(r"(?:最近|近|过去)\s*(\d{1,2})\s*个?月", text)
    if day_match:
        lookback = max(1, min(730, int(day_match.group(1))))
    elif month_match:
        lookback = max(30, min(730, int(month_match.group(1)) * 30))
    elif re.search(r"(?:最近|近|过去)\s*(?:一|1)\s*年", text):
        lookback = 365
    return TimePolicy(lookback_days=lookback, recency_boost_days=min(90, lookback))


def _extract_candidate_title(text: str) -> str | None:
    seniority = r"(?:总监|副总裁|总经理|首席[\u4e00-\u9fffA-Za-z]{0,8}|负责人|VP|Head|Director|CTO|COO|CEO)"
    patterns = (
        rf"(?:手上有|现有|有|推荐)(?:一名|一位|一个)?(?P<title>[^，。；;？?]{{2,28}}?{seniority})(?:的)?候选人",
        rf"候选人[：:\s]+(?P<title>[^，。；;？?\n]{{2,28}}?{seniority})(?=[，。；;？?\n]|$)",
        rf"^\s*(?P<title>[^，。；;：:\n]{{2,28}}?{seniority})(?=[，。；;：:\n]|$)",
        rf"(?P<title>[\u4e00-\u9fffA-Za-z0-9/+.-]{{2,24}}?{seniority})(?:的)?候选人",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        title = match.group("title").strip()
        title = re.sub(r"^(?:我现在|我|目前|现在)", "", title)
        title = re.sub(r"^(?:手上有|现有|有|一名|一位|一个)+", "", title)
        if title:
            return title
    return None


def _candidate_profile(text: str, candidate_context: str | None = None) -> CandidateProfile:
    combined = f"{text}\n{candidate_context or ''}"
    title = _extract_candidate_title(text) or _extract_candidate_title(candidate_context or "")
    lowered = combined.lower()
    capabilities: list[str] = []
    inferred: list[str] = []
    if title:
        for needles, values in _ROLE_CAPABILITY_RULES:
            if any(needle.lower() in title.lower() for needle in needles):
                capabilities.extend(values)
                inferred.append("core_capabilities_from_role_title")
                break
    explicit_capability_patterns = (
        r"(?:擅长|负责|能力包括|核心能力(?:是|为)?)([^。；;\n]{2,60})",
        r"(?:从0到1|从零到一)([^。；;\n]{0,40})",
    )
    for pattern in explicit_capability_patterns:
        for match in re.finditer(pattern, combined, re.IGNORECASE):
            capabilities.append(match.group(0).strip())
    industries = [
        canonical
        for canonical, definition in _KNOWN_INDUSTRIES.items()
        if any(alias.lower() in lowered for alias in definition["aliases"])
    ]
    context_hits = [term for term in _BUSINESS_CONTEXT_TERMS if term.lower() in lowered]
    leadership: list[str] = []
    for match in re.finditer(r"(?:管理|带领|搭建|组建)(?:过)?\s*(\d{1,4})\s*人(?:的)?团队", combined):
        leadership.append(f"管理{match.group(1)}人团队")
    if re.search(r"(?:从0到1|从零到一).{0,15}(?:团队|部门|体系)", combined):
        leadership.append("从0到1组织建设")
    geography = extract_geography(combined)
    geo_preferences = geography.locations if geography.explicit else ()
    desired: list[str] = []
    for match in re.finditer(
        r"(?:希望|考虑|想去|转向|接受)([\u4e00-\u9fffA-Za-z0-9+.-]{2,20}?)(?:行业|产业|赛道|领域)",
        combined,
    ):
        desired.append(match.group(1))
    exclusions: list[str] = []
    for match in re.finditer(r"(?:不考虑|排除|不要)([^。；;\n]{2,40})", combined):
        exclusions.append(match.group(1).strip())

    missing: list[str] = []
    if not title:
        missing.append("role_title")
    if not context_hits:
        missing.append("core_business_context")
    if not geo_preferences:
        missing.append("candidate_geography_preference")
    if not leadership:
        missing.append("leadership_scope")
    return CandidateProfile(
        role_title=title,
        seniority="director_plus" if title and re.search(
            r"总监|副总裁|总经理|首席|负责人|VP|Head|Director|CTO|COO|CEO",
            title,
            re.IGNORECASE,
        ) else "unknown",
        core_capabilities=_unique(capabilities),
        industry_experience=_unique((*industries, *context_hits)),
        leadership_scope=_unique(leadership),
        geography_preferences=_unique(geo_preferences),
        desired_directions=_unique(desired),
        exclusions=_unique(exclusions),
        inferred_fields=_unique(inferred),
        missing_critical_fields=tuple(missing),
    )


def build_industry_map(topic: str) -> IndustryMap:
    """Build a four-layer map for a known or arbitrary Chinese topic."""

    known = _known_industry(topic)
    if known:
        definition = _KNOWN_INDUSTRIES[known]
        concepts = _unique(
            (
                known,
                *definition["aliases"],
                *definition["core"],
                *definition["direct_upstream"],
                *definition["direct_downstream"],
            )
        )
        return IndustryMap(
            topic=topic,
            canonical_topic=known,
            map_kind="curated_template",
            core=tuple(definition["core"]),
            direct_upstream=tuple(definition["direct_upstream"]),
            direct_downstream=tuple(definition["direct_downstream"]),
            adjacent=tuple(definition["adjacent"]),
            query_terms=concepts,
            signal_terms=_SIGNAL_TERMS,
            boundary_notes=tuple(definition["notes"]),
        )

    cleaned = re.sub(r"(行业|产业|赛道|领域)$", "", topic.strip(), flags=re.IGNORECASE) or topic.strip()
    core = (
        f"{cleaned}核心产品与服务",
        f"{cleaned}技术平台与系统",
        f"{cleaned}产业化解决方案",
    )
    upstream = (
        f"{cleaned}关键设备与零部件",
        f"{cleaned}关键材料与传感器",
        f"{cleaned}基础软件、工具链与技术服务",
    )
    downstream = (
        f"{cleaned}垂直行业应用",
        f"{cleaned}交付、运营与商业化服务",
        f"采购或规模化采用{cleaned}的企业",
    )
    adjacent = (
        f"与{cleaned}共享关键技术的相邻赛道",
        f"与{cleaned}共享客户但没有直接产品关联的企业",
        f"仅使用{cleaned}概念进行宣传的企业",
    )
    return IndustryMap(
        topic=topic,
        canonical_topic=cleaned,
        map_kind="generated_generic_template",
        core=core,
        direct_upstream=upstream,
        direct_downstream=downstream,
        adjacent=adjacent,
        query_terms=_unique((cleaned, *core, *upstream, *downstream)),
        signal_terms=_SIGNAL_TERMS,
        boundary_notes=(
            "这是规则生成的临时行业地图；执行时应使用首批证据验证边界。",
            "仅概念相关且没有直接业务关联的企业进入相邻层。",
        ),
    )


def _discovery_queries(industry_map: IndustryMap, mode: OpportunityMode) -> tuple[str, ...]:
    concepts = _unique(
        (
            industry_map.canonical_topic,
            *industry_map.core[:3],
            *industry_map.direct_upstream[:2],
            *industry_map.direct_downstream[:2],
        )
    )
    signal_groups = (
        "融资 增资 产业基金",
        "扩产 量产 工厂 基地 产线",
        "订单 中标 交付 战略合作",
        "获批 注册 临床 许可 技术里程碑",
    )
    queries = [f"{concept} {signals}" for concept in concepts for signals in signal_groups]
    if mode is OpportunityMode.CANDIDATE_FLOAT:
        queries.extend(f"{concept} 团队建设 组织升级" for concept in concepts)
    return tuple(queries)


def _clarifications(
    mode: OpportunityMode,
    industry_topic: str | None,
    candidate: CandidateProfile | None,
    mode_confidence: str,
) -> ClarificationPlan:
    questions: list[ClarifyingQuestion] = []
    if mode_confidence == "low" and not industry_topic:
        questions.append(ClarifyingQuestion(
            question_id="confirm_mode_and_topic",
            prompt="你希望从哪个行业/技术方向正向找公司，还是要拿一位候选人做 Float？",
            reason="当前输入无法可靠区分 Market Scan 与 Candidate Float，也没有行业边界。",
            blocking=True,
            answer_field="mode_and_industry",
        ))
    elif mode is OpportunityMode.MARKET_SCAN and not industry_topic:
        questions.append(ClarifyingQuestion(
            question_id="market_industry",
            prompt="这次希望扫描哪个行业、技术方向或产业问题？",
            reason="行业边界会决定公司集合、固定信源包和事件词。",
            blocking=True,
            answer_field="industry_topic",
            examples=("脑机接口", "商业航天", "AI制药"),
        ))
    elif mode is OpportunityMode.CANDIDATE_FLOAT and candidate is not None:
        missing = set(candidate.missing_critical_fields)
        if "role_title" in missing:
            questions.append(ClarifyingQuestion(
                question_id="candidate_role",
                prompt="这位候选人目前的职位或核心职能是什么？",
                reason="没有职能信息就无法建立总监级岗位假设和能力图谱。",
                blocking=True,
                answer_field="candidate.role_title",
                examples=("数据采集总监", "半导体设备研发副总裁"),
            ))
        if "core_business_context" in missing:
            questions.append(ClarifyingQuestion(
                question_id="candidate_business_context",
                prompt="他的核心经验主要属于什么业务场景或技术环境？",
                reason="同一职位在不同行业的可迁移能力和目标公司会显著不同。",
                blocking=False,
                answer_field="candidate.core_business_context",
                examples=("自动驾驶路采", "具身智能真机数采", "互联网数据平台"),
            ))
        if "candidate_geography_preference" in missing:
            questions.append(ClarifyingQuestion(
                question_id="candidate_geography",
                prompt="候选人的工作地域有没有明确限制？",
                reason="地域限制会实质改变 Float 的可选公司集合；无答案时按中国大陆招聘市场探索。",
                blocking=False,
                answer_field="candidate.geography_preferences",
                examples=("只看上海", "大湾区优先", "全国均可"),
            ))
        if "leadership_scope" in missing:
            questions.append(ClarifyingQuestion(
                question_id="candidate_leadership_scope",
                prompt="他管理过多大团队，或承担过怎样的预算、组织搭建责任？",
                reason="这决定能否匹配总监以上且带组织责任的岗位。",
                blocking=False,
                answer_field="candidate.leadership_scope",
                examples=("管理50人团队", "从0到1搭建部门"),
            ))
    next_question = questions[0] if questions else None
    can_execute = not any(question.blocking for question in questions)
    return ClarificationPlan(
        can_execute_exploratory=can_execute,
        next_question=next_question,
        deferred_questions=tuple(questions[1:]),
    )


def _execution_stages(mode: OpportunityMode) -> tuple[ExecutionStage, ...]:
    stages = [
        ExecutionStage(
            "interpret",
            "解释请求",
            "确定模式、行业边界、地域、时间和总监级目标。",
            ("raw_request",),
            ("opportunity_request", "clarification_queue"),
        ),
        ExecutionStage(
            "map_industry",
            "构建行业地图",
            "把任意行业输入展开为核心、直接上游、直接下游和相邻层。",
            ("industry_topic",),
            ("industry_map", "query_terms"),
        ),
        ExecutionStage(
            "select_sources",
            "选择稳定信源",
            "优先复用固定信源和行业来源包，缺口才使用公共搜索。",
            ("industry_map", "source_registry"),
            ("source_run_plan",),
        ),
        ExecutionStage(
            "collect",
            "采集180天窗口",
            "增量采集来源文档并保留原始出处、日期和内容指纹。",
            ("source_run_plan", "time_policy"),
            ("source_documents",),
        ),
        ExecutionStage(
            "normalize_eventize",
            "归一与事件聚簇",
            "执行URL、内容、报道和商业事件分层去重及公司实体归一。",
            ("source_documents",),
            ("statements", "events", "canonical_entities"),
        ),
        ExecutionStage(
            "apply_geography",
            "判断招聘市场相关性",
            "保留与目标招聘市场存在可解释团队、项目或扩张关联的公司。",
            ("canonical_entities", "events", "geography"),
            ("geography_qualified_companies",),
        ),
        ExecutionStage(
            "infer_roles_and_gate",
            "推断岗位并执行硬门槛",
            "形成总监以上岗位假设，并要求至少一条招聘广告之前的上游信号。",
            ("events", "geography_qualified_companies"),
            ("gate_qualified_companies", "late_opportunity_appendix"),
        ),
        ExecutionStage(
            "score",
            "事件级可解释评分",
            "最近90天信号加权；每项加减分必须引用事件与声明。",
            ("gate_qualified_companies", "events"),
            ("score_breakdowns",),
        ),
        ExecutionStage(
            "basic_research",
            "基础研究",
            "为全部候选公司补充公开负责人、应联系角色和信息缺口。",
            ("score_breakdowns",),
            ("basic_company_research",),
        ),
    ]
    if mode is OpportunityMode.CANDIDATE_FLOAT:
        stages.append(ExecutionStage(
            "float_match_and_deep_research",
            "Float匹配与深度研究",
            "按需求概率、候选人匹配、时机和可研究性排序，并研究投资人与内部决策者。",
            ("candidate_profile", "score_breakdowns", "events"),
            ("float_scores", "investor_research", "internal_decision_makers"),
            cost_tier="high",
        ))
    stages.append(ExecutionStage(
        "rank_publish",
        "生成可解释Top 20",
        "降低软门槛补足结果但不放宽硬门槛，按分数降序输出。",
        ("score_breakdowns", "basic_company_research"),
        ("top_20", "score_explanations", "uncertainties"),
    ))
    return tuple(stages)


class OpportunityRequestPlanner:
    """Parse a natural-language request into an executable, auditable plan."""

    def plan(
        self,
        text: str,
        *,
        candidate_context: str | None = None,
        deep_research: bool = False,
    ) -> OpportunityExecutionPlan:
        if not text or not text.strip():
            raise ValueError("text must not be empty")
        cleaned = re.sub(r"\s+", " ", text).strip()
        mode, mode_confidence, mode_reason = detect_mode(cleaned)
        explicit_topic = extract_industry_topic(cleaned)
        candidate = _candidate_profile(cleaned, candidate_context) if mode is OpportunityMode.CANDIDATE_FLOAT else None

        industry_topic = explicit_topic
        if not industry_topic and candidate is not None:
            if candidate.desired_directions:
                industry_topic = candidate.desired_directions[0]
            elif candidate.industry_experience:
                industry_topic = candidate.industry_experience[0]
            elif candidate.role_title:
                industry_topic = re.sub(
                    r"(总监|副总裁|总经理|首席[\u4e00-\u9fffA-Za-z]{0,8}|负责人|VP|Head|Director|CTO|COO|CEO)$",
                    "",
                    candidate.role_title,
                    flags=re.IGNORECASE,
                ).strip() or candidate.role_title

        geography = extract_geography(cleaned if candidate_context is None else f"{cleaned} {candidate_context}")
        request = OpportunityRequest(
            raw_text=cleaned,
            mode=mode,
            mode_confidence=mode_confidence,
            mode_reason=mode_reason,
            industry_topic=industry_topic,
            target_seniority="director_plus",
            target_role=candidate.role_title if candidate else None,
            geography=geography,
            time_policy=extract_time_policy(cleaned),
            candidate_profile=candidate,
            deep_research_requested=deep_research or mode is OpportunityMode.CANDIDATE_FLOAT,
        )
        clarification = _clarifications(mode, industry_topic, candidate, mode_confidence)
        industry_map = build_industry_map(industry_topic) if industry_topic else None
        hard_gates = HardGates()
        result_policy = ResultPolicy()
        queries = _discovery_queries(industry_map, mode) if industry_map else ()
        can_execute = industry_map is not None and clarification.can_execute_exploratory
        return OpportunityExecutionPlan(
            request=request,
            clarification=clarification,
            industry_map=industry_map,
            hard_gates=hard_gates,
            result_policy=result_policy,
            source_strategy=SourceStrategy(),
            discovery_queries=queries,
            stages=_execution_stages(mode),
            can_execute_now=can_execute,
        )


def plan_opportunity_request(
    text: str,
    *,
    candidate_context: str | None = None,
    deep_research: bool = False,
) -> OpportunityExecutionPlan:
    """Convenience API for callers that do not need a reusable planner."""

    return OpportunityRequestPlanner().plan(
        text,
        candidate_context=candidate_context,
        deep_research=deep_research,
    )


__all__ = [
    "CandidateProfile",
    "ClarificationPlan",
    "ClarifyingQuestion",
    "ExecutionStage",
    "GeographyScope",
    "HardGates",
    "IndustryMap",
    "OpportunityExecutionPlan",
    "OpportunityMode",
    "OpportunityRequest",
    "OpportunityRequestPlanner",
    "ResultPolicy",
    "SourceStrategy",
    "TimePolicy",
    "build_industry_map",
    "detect_mode",
    "extract_geography",
    "extract_industry_topic",
    "extract_time_policy",
    "plan_opportunity_request",
]
