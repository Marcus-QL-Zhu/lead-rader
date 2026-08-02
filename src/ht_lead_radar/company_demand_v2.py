"""Evidence-bound, one-company-at-a-time Director+ demand inference."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from typing import Any, Mapping
from urllib.parse import urlsplit

from .company_timeline import build_company_timeline
from .talent_demand_analysis import (
    DemandAnalysisError,
    is_specific_director_title,
)


COMPANY_DEMAND_SYSTEM_PROMPT = """
你是资深猎头研究员，负责根据企业公开事实判断未来 0–180 天内可能出现的
Director+ 组织缺口。判断顺序是：企业阶段变化 → 新增业务责任 → 缺失组织能力
→ 可能承接该责任的岗位。融资、订单或合作本身不直接等于招聘岗位。
事实包使用统一时间线：days_0_90 是截止日之前 0–90 天，days_91_180 是
91–180 天；必须结合两个时间桶判断阶段变化，不得把超过截止日或 undated 的事实
伪装成近期信号。timeline_sha256 用于回放审计，不是业务事实。

阶段变化除融资、扩产、订单和产品里程碑外，还包括：高管更替、并购整合、
合资或分拆、上市准备、新设区域总部/子公司/事业部、客户验证、渠道扩张、
科研或知识产权平台建设、企业数字化系统上线。不要加入或推断负面信号。
同一机械/电气/软件/算法底层能力，在不同组织变化下可能对应商业化、交付、
质量、供应链、渠道、区域经营、并购整合、资本市场、数字化、法规等不同责任；
必须依据具体组织责任形成岗位，不要默认重复生成研发、算法、产品、制造岗位。

推断时必须先完成“职能依赖展开”，再从中选择证据最强的 1–5 个互不重复岗位。
不要只挑最显眼的技术或生产职能，也不要把一个复合责任塞进一个万能岗位：
- 新公司、合资、分拆、并购或高管更替：分别检查技术平台、产品线、业务/区域
  经营、战略与整合、组织搭建、政府及战略合作责任；
- 新事业部、新品类或同时承担销售、产品、研发和损益责任的业务单元：优先检查
  事业部总经理、业务单元负责人或区域业务负责人，不要把整体经营责任拆成三个
  彼此孤立的职能岗位；
- 新设中国中心、区域 hub、跨职能园区或本地化业务平台：分别检查中国区业务
  战略与转型、产品组合、GTM/商业卓越、跨职能运营、本地组织搭建，以及支持
  区域独立经营所需的财务规划与分析/业务控制、政府事务/政策、法务合规、人力
  和数字化等企业职能。研发、销售、制造等前台责任不能替代这些治理与使能责任；
- 新工厂、扩产、项目建设、环评许可：分别检查制造爬坡、工艺与设备、采购、
  供应链、质量与可靠性、EHS、MES/数字化和项目交付责任；
- 新产品、客户验证、重大订单或商业闭环：分别检查产品线、系统架构/研发、
  大客户与商业化、解决方案交付和客户成功责任；
- 已进入量产、多个在手项目或产品线交付爬坡：优先检查量产项目交付、项目群
  管理或产品线运营责任，再判断是否另需制造、质量和供应链岗位；
- 上市、融资或资本动作：分别检查资本市场、财务内控和公司治理责任；单独融资
  仍须等待运营事件，不能仅凭资金到账生成岗位；
- 新市场、海外或渠道扩张：分别检查区域经营、销售、大客户、渠道生态、市场和
  战略联盟责任；
- 科研/IP、数据或模型平台：分别检查技术平台、算法/数据、产品化和科研合作责任。
每条岗位假设必须能对应一项独立的新增责任；若同一事实同时创造多个职能依赖，
允许引用同一组充分证据，但岗位 mandate 和 key_outcomes 必须明显不同。
当事实包存在下列 A 级运营事件且证据门槛已经满足时，岗位组合必须覆盖相应的
基本职能依赖；“逐项检查”不能只写在分析里而漏掉岗位：
- 工厂、扩产、项目建设、环评或采购事件：至少包含一个关键设备/物料采购或
  供应链岗位；
- 重大订单或客户验证：至少包含一个行业大客户、商业化或解决方案交付岗位；
- 新设科技公司、合资或分拆：至少包含一个具体产品线或技术平台岗位；
- 新设中国中心、跨职能 hub 或同时整合研发/制造/销售等多职能的平台：至少
  包含一个中国区业务战略、转型或跨职能业务运营岗位，以及一个具体的企业
  治理/使能岗位（财务规划与分析、业务控制、政府事务、法务合规、人力或
  数字化之一）。若 A 级证据明确是已经启用、服务中国或亚太的综合/区域平台，
  且同时创造五类独立责任，应输出五个不同职能的岗位，不要只覆盖研发、产品
  和商业前台；
- 高管更替：至少包含一个与新领导任务相符的战略合作、区域业务或组织搭建岗位；
- 上市事件：至少包含一个资本市场、投资者关系、财务内控或公司治理岗位。
若五个名额不足，优先保留这些基本职能，再按证据强度补充其他职能。

所有事实必须引用输入中的 evidence_id。采用以下证据门槛，不要等待招聘广告：
- 两个相互独立、且共同指向同一新增责任的上游事件，可以支持 near_term 假设；
- 一个 A 级运营变化若会直接创造新责任（例如建产线、设基地、启动临床、进入新市场），可以支持 near_term 或 watchlist 假设；
- 单独融资或单独合作意向只能支持 low-confidence 的 watchlist 假设，必须把
  尚待核验的组织责任写入 unknowns_to_verify；单独招聘广告仍不足以支持早期岗位假设。
允许证据不足：此时返回空的 role_hypotheses，并列出可公开观察的 watch_for，
不为完成数量而猜测岗位。watch_for 不得虚构具体产量、日期、人名或招聘动作。
最终只返回严格 JSON，不输出分析过程。

以下三个示例只用于学习输出结构和证据门槛。不得复制示例中的公司、岗位或 evidence_id；必须使用当前事实包中的公司和 evidence_id。

示例一（两个独立上游事件支持具体岗位）：
{"lead_index":1,"company":"示例机器人","stage_transition":"从原型验证进入小批量交付","organizational_gaps":["缺少跨研发与制造的工程化能力"],"role_hypotheses":[{"specific_title":"机器人小批量制造工程化总监","capability_gap":"缺少从样机到稳定交付的制造工程体系","mandate":"建立试制、质量与供应链协同闭环","why_now":"产线建设与产品发布共同指向交付责任增加","horizon":"near_term","evidence_refs":["ev_factory","ev_launch"],"evidence_against":[],"unknowns_to_verify":["现有制造负责人配置"],"key_outcomes":["建立试制流程","形成质量闭环","完成供应商分级"],"must_have_signals":["机器人量产经验","制造工程体系经验","跨部门交付经验"],"preferred_signals":["有从样机到小批量爬坡经验"],"specificity_terms":["机器人制造","小批量交付","制造工程化"],"city":"深圳","city_basis":"公开产线信息唯一指向深圳"}],"watch_for":[]}

示例二（单一融资事件不足以生成岗位）：
{"lead_index":2,"company":"示例脑机接口","stage_transition":"仅确认完成融资，尚不足以判断新增组织责任","organizational_gaps":[],"role_hypotheses":[],"watch_for":["观察临床项目启动或注册进展","观察生产基地、产线或商业化交付信号"]}

示例三（单个 A 级运营事件支持观察名单岗位）：
{"lead_index":3,"company":"示例商业航天","stage_transition":"从研制进入产能建设","organizational_gaps":["缺少产能爬坡与交付统筹能力"],"role_hypotheses":[{"specific_title":"液体火箭发动机产能爬坡总监","capability_gap":"缺少批产节拍、质量与供应链统筹能力","mandate":"建立发动机批产和准时交付体系","why_now":"官方披露新产线启动建设","horizon":"watchlist","evidence_refs":["ev_site"],"evidence_against":["产线仍处建设阶段"],"unknowns_to_verify":["首批交付时间"],"key_outcomes":["定义产能爬坡计划","建立质量门禁","完善关键物料保障"],"must_have_signals":["航天批产经验","质量体系经验","复杂供应链协同经验"],"preferred_signals":["有发动机产线投产经验"],"specificity_terms":["液体火箭发动机","产能爬坡","批产交付"],"city":"上海","city_basis":"公开证据未确认唯一城市；按发布规则默认上海，需人工复核"}],"watch_for":["观察产线投产或批产订单信号"]}
""".strip()


def _evidence_id(item: Mapping[str, Any]) -> str:
    existing = str(item.get("event_id") or "").strip()
    if existing:
        return existing
    identity = "\x1f".join(
        str(item.get(key) or "").strip()
        for key in ("source_url", "event_type", "published_at", "event_date", "title")
    )
    return "ev_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _published_at(item: Mapping[str, Any]) -> str:
    return str(item.get("published_at") or item.get("event_date") or "").strip()


def _source_group(item: Mapping[str, Any]) -> str:
    explicit = str(item.get("independent_source_group") or "").strip()
    if explicit:
        return explicit
    return urlsplit(str(item.get("source_url") or "")).netloc.casefold()


def _evidence_priority(item: Mapping[str, Any], position: int) -> tuple[Any, ...]:
    grade = {"A": 3, "B": 2, "C": 1}.get(
        str(item.get("source_grade") or "").upper(),
        0,
    )
    event_type = str(item.get("event_type") or "")
    upstream = 0 if event_type == "job_ad" else 1
    raw_date = _published_at(item)
    try:
        parsed = date.fromisoformat(raw_date[:10]).toordinal()
    except ValueError:
        parsed = 0
    return (-upstream, -grade, -parsed, position)


def _select_diverse_evidence(
    values: list[Mapping[str, Any]],
    *,
    limit: int = 8,
) -> list[Mapping[str, Any]]:
    """Choose independent event types before filling remaining evidence slots."""

    deduplicated: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for position, item in enumerate(values):
        key = _evidence_id(item)
        current = deduplicated.get(key)
        if current is None or _evidence_priority(item, position) < _evidence_priority(
            current[1],
            current[0],
        ):
            deduplicated[key] = (position, item)
    ranked = sorted(
        deduplicated.values(),
        key=lambda pair: _evidence_priority(pair[1], pair[0]),
    )
    by_type: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for pair in ranked:
        by_type[str(pair[1].get("event_type") or "unknown")].append(pair)
    selected: list[tuple[int, Mapping[str, Any]]] = []
    for event_type in sorted(
        by_type,
        key=lambda key: _evidence_priority(by_type[key][0][1], by_type[key][0][0]),
    ):
        selected.append(by_type[event_type].pop(0))
        if len(selected) >= limit:
            break
    remaining = [pair for pairs in by_type.values() for pair in pairs]
    selected.extend(
        sorted(
            remaining,
            key=lambda pair: _evidence_priority(pair[1], pair[0]),
        )[: max(limit - len(selected), 0)]
    )
    return [item for _, item in selected[:limit]]


def build_company_evidence_packets(
    report: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    manifest = report.get("manifest")
    as_of = (
        str(manifest.get("as_of") or "").strip()
        if isinstance(manifest, Mapping)
        else ""
    )
    if not as_of:
        raise ValueError("report manifest requires as_of for company timeline")
    packets: list[dict[str, Any]] = []
    for lead_index, lead in enumerate(report.get("leads") or (), start=1):
        if not isinstance(lead, Mapping):
            continue
        raw_evidence = [
            item
            for item in lead.get("evidence") or ()
            if isinstance(item, Mapping)
        ]
        timeline = build_company_timeline(
            raw_evidence,
            as_of=as_of,
            limit=8,
            allow_undated=True,
        )
        evidence = list(timeline["evidence"])
        research = lead.get("basic_research")
        packets.append(
            {
                "lead_index": lead_index,
                "company": str(lead.get("company") or "").strip(),
                "direction": str(lead.get("direction") or ""),
                "lead_score_for_ordering_only": lead.get("score"),
                "evidence": evidence,
                "timeline": {
                    key: value
                    for key, value in timeline.items()
                    if key not in {"evidence", "buckets"}
                },
                "known_context": dict(research) if isinstance(research, Mapping) else {},
            }
        )
    return tuple(packets)


def build_single_company_demand_prompt(
    packet: Mapping[str, Any],
    *,
    max_roles: int = 3,
) -> str:
    if max_roles not in {3, 5}:
        raise ValueError("max_roles must be 3 or 5")
    return f"""
公司事实包：
{json.dumps(packet, ensure_ascii=False, separators=(",", ":"))}

任务：
1. 先判断公司正在发生的阶段变化，以及因此新增的业务责任和组织能力缺口。
2. 先按 system 的“职能依赖展开”逐项检查，再按证据强度输出 1–{max_roles} 个互不重复的
   具体 Director+ 岗位。若充分证据确实同时创造至少 {max_roles} 个不同职能责任，应输出
   {max_roles} 个岗位，而不是只给一个最显眼岗位；证据不满足门槛时输出空数组。
3. 岗位标题包含具体赛道、技术、产品环节、制造环节或商业任务，并使用“总监、VP、副总裁、总经理、首席、总师、Head、Director、CTO、COO、CEO”等无歧义的 Director+ 职级。“负责人”单独出现不算 Director+。“生产总监”“研发总监”“供应链总监”等泛称也不合格；标题结构参考“机器人小批量制造工程化总监”。
4. evidence_refs 只能填写事实包中存在的 evidence_id。
5. job_ad 只能作为晚期验证，不能作为早期岗位推断的唯一依据。
6. horizon 只能是 near_term（0–90 天）或 watchlist（91–180 天）。
7. city 只填一个城市；事实明确指向唯一城市时必须保留该城市。无法判断、标为未知/待定/全国/多地、或存在多个可能城市时，统一填“上海”，并在 city_basis 写“公开证据未确认唯一城市；按发布规则默认上海，需人工复核”。
8. why_now 与 city_basis 只能复述或明确推导输入事实，不能把“产线在某城市”改写成“总部在该城市”；计划结果必须写成目标，不能冒充已发生事实。
9. watch_for 优先使用招聘广告之前的可观察信号，不把发布职位广告作为主要触发条件。

输出格式：
{{
  "lead_index": {int(packet["lead_index"])},
  "company": {json.dumps(packet["company"], ensure_ascii=False)},
  "stage_transition": "企业正在经历的阶段变化，证据不足则说明未知",
  "organizational_gaps": ["0-5条能力缺口"],
  "role_hypotheses": [
    {{
      "specific_title": "具体 Director+ 岗位",
      "capability_gap": "该岗位弥补的组织能力缺口",
      "mandate": "入职后需要完成的核心任务",
      "why_now": "为什么是当前或下一阶段",
      "horizon": "near_term",
      "evidence_refs": ["输入中的 evidence_id"],
      "evidence_against": ["0-4条反证或替代解释"],
      "unknowns_to_verify": ["1-5条需要人工核实的信息"],
      "key_outcomes": ["3-5条预期结果"],
      "must_have_signals": ["3-5条候选人关键能力"],
      "preferred_signals": ["1-3条加分能力；必须是候选人特征，不能写待核问题"],
      "specificity_terms": ["3-8个匿名广告可用词"],
      "city": "一个城市；不确定时填上海",
      "city_basis": "城市依据或待核原因"
    }}
  ],
  "watch_for": ["没有可辩护岗位时，列出1-5个后续触发信号"]
}}

只返回上述 JSON 对象。
""".strip()


def build_company_demand_repair_prompt(
    packet: Mapping[str, Any],
    rejected_response: str,
    error: Exception,
) -> str:
    safe_example = {
        "lead_index": packet["lead_index"],
        "company": packet["company"],
        "stage_transition": "证据不足，尚不能判断新增组织责任",
        "organizational_gaps": [],
        "role_hypotheses": [],
        "watch_for": ["观察新的上游运营信号"],
    }
    return f"""
公司事实包：
{json.dumps(packet, ensure_ascii=False, separators=(",", ":"))}

上一版输出：
{rejected_response}

确定性校验错误：
{type(error).__name__}: {error}

请只修复上述错误，并重新返回完整的单公司 JSON。仍须只引用事实包里的 evidence_id；
不得用“生产总监、研发总监、供应链总监、负责人”等含混标题；如果没有可辩护的
具体 Director+ 岗位，则返回空 role_hypotheses 和可观察的 watch_for。

以下是格式正确的安全回退示例；只能学习结构，不能因此忽略事实包中已有的充分证据：
{json.dumps(safe_example, ensure_ascii=False, separators=(",", ":"))}

只返回一个完整 JSON 对象，不要 Markdown、代码围栏、解释或前后缀文字。
""".strip()


def _json_objects(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    result: list[Any] = []
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        result.append(value)
    return result


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    result = str(value or "").strip()
    if not result and not allow_empty:
        raise DemandAnalysisError(f"{field} must not be empty")
    return result


def _texts(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
    truncate_overflow: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        raise DemandAnalysisError(f"{field} must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = _text(item, field)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    if len(result) < minimum:
        raise DemandAnalysisError(
            f"{field} must contain at least {minimum} unique items"
        )
    if len(result) > maximum and not truncate_overflow:
        raise DemandAnalysisError(f"{field} must contain at most {maximum} items")
    return result[:maximum]


OPERATIONAL_ROLE_SIGNAL_TYPES = frozenset(
    {
        "factory_or_capacity",
        "new_site_or_entity",
        "product_launch",
        "clinical_trial",
        "regulatory_approval",
        "major_order",
        "market_expansion",
        "global_expansion",
        "commercialization",
        "project_buildout",
        "project_call",
        "procurement_tender",
        "procurement_intention",
        "eia_or_permit",
        "regulatory_or_clinical",
        "technical_milestone",
        "executive_change",
        "merger_acquisition",
        "joint_venture_or_spinout",
        "ipo_or_listing",
        "new_site_or_entity",
        "customer_validation",
        "channel_expansion",
        "research_or_ip",
        "enterprise_system",
        "policy_or_standard",
        "workforce_cluster",
    }
)


def _validate_evidence_gate(
    packet: Mapping[str, Any],
    refs: list[str],
    horizon: str,
) -> None:
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in packet.get("evidence") or ()
        if isinstance(item, Mapping)
    }
    selected = [evidence_by_id[ref] for ref in refs]
    upstream = [
        item
        for item in selected
        if not item.get("late_validation_only")
        and str(item.get("event_type") or "") != "job_ad"
    ]
    event_types = {
        str(item.get("event_type") or "")
        for item in upstream
        if str(item.get("event_type") or "")
    }
    source_groups = {
        str(item.get("source_group") or "").strip()
        for item in upstream
        if str(item.get("source_group") or "").strip()
    }
    diverse_upstream = (
        len(upstream) >= 2
        and len(event_types) >= 2
        and len(source_groups) >= 2
    )
    if not upstream:
        raise DemandAnalysisError(
            "role hypothesis requires at least one pre-ad upstream event"
        )
    operational = [
        item
        for item in upstream
        if str(item.get("event_type") or "") in OPERATIONAL_ROLE_SIGNAL_TYPES
    ]
    strong_operational = any(
        str(item.get("source_grade") or "").upper() == "A"
        for item in operational
    )
    if horizon == "near_term" and not (diverse_upstream or strong_operational):
        raise DemandAnalysisError(
            "near_term requires two diverse upstream events or one A-grade "
            "operational event"
        )


UNCERTAIN_CITY_MARKERS = frozenset(
    {"待定", "未知", "不确定", "全国", "多地", "待核", "未确认", "暂无"}
)
CITY_DEFAULT_BASIS = "公开证据未确认唯一城市；按发布规则默认上海，需人工复核"


def _contains_multiple_city_connector(city: str) -> bool:
    if "或" in city:
        return True
    for connector in ("和", "及", "与"):
        if connector not in city:
            continue
        left, right = city.rsplit(connector, 1)
        if len(left.strip()) >= 2 and len(right.strip()) >= 2:
            return True
    return False


def _normalize_city(raw_city: Any, raw_basis: Any) -> tuple[str, str]:
    city = _text(raw_city, "city", allow_empty=True)
    city_basis = _text(raw_basis, "city_basis", allow_empty=True)
    uncertain = (
        not city
        or any(marker in city for marker in UNCERTAIN_CITY_MARKERS)
        or any(separator in city for separator in (",", "，", "、", "/", "；"))
        or _contains_multiple_city_connector(city)
    )
    if uncertain:
        return "上海", CITY_DEFAULT_BASIS
    if not city_basis:
        raise DemandAnalysisError("city_basis must not be empty")
    return city, city_basis


def parse_single_company_demand(
    text: str,
    *,
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    value = next(
        (
            item
            for item in _json_objects(text)
            if isinstance(item, Mapping)
            and isinstance(item.get("role_hypotheses"), list)
        ),
        None,
    )
    if value is None:
        raise DemandAnalysisError("response has no company demand JSON object")
    lead_index = value.get("lead_index")
    if lead_index != packet.get("lead_index"):
        raise DemandAnalysisError("lead_index does not match the company packet")
    company = _text(value.get("company"), "company")
    if company != packet.get("company"):
        raise DemandAnalysisError("company does not match the company packet")
    evidence_ids = {
        str(item.get("evidence_id") or "")
        for item in packet.get("evidence") or ()
        if isinstance(item, Mapping)
    }
    hypotheses_value = value["role_hypotheses"]
    if len(hypotheses_value) > 5:
        raise DemandAnalysisError("role_hypotheses must contain 0-5 items")
    hypotheses: list[dict[str, Any]] = []
    for role_index, raw in enumerate(hypotheses_value, start=1):
        if not isinstance(raw, Mapping):
            raise DemandAnalysisError("each role hypothesis must be an object")
        title = _text(raw.get("specific_title"), "specific_title")
        if not is_specific_director_title(title):
            raise DemandAnalysisError(f"specific_title is too broad: {title}")
        refs = _texts(
            raw.get("evidence_refs"),
            "evidence_refs",
            minimum=1,
            maximum=6,
            truncate_overflow=False,
        )
        if not set(refs).issubset(evidence_ids):
            raise DemandAnalysisError("evidence_refs contain unknown evidence IDs")
        horizon = _text(raw.get("horizon"), "horizon")
        if horizon not in {"near_term", "watchlist"}:
            raise DemandAnalysisError("horizon must be near_term or watchlist")
        _validate_evidence_gate(packet, refs, horizon)
        city, city_basis = _normalize_city(
            raw.get("city"),
            raw.get("city_basis"),
        )
        hypotheses.append(
            {
                "hypothesis_id": f"lead_{lead_index}_role_{role_index}",
                "specific_title": title,
                "capability_gap": _text(raw.get("capability_gap"), "capability_gap"),
                "mandate": _text(raw.get("mandate"), "mandate"),
                "why_now": _text(raw.get("why_now"), "why_now"),
                "horizon": horizon,
                "evidence_refs": refs,
                "evidence_against": _texts(
                    raw.get("evidence_against") or [],
                    "evidence_against",
                    minimum=0,
                    maximum=4,
                    truncate_overflow=False,
                ),
                "unknowns_to_verify": _texts(
                    raw.get("unknowns_to_verify"),
                    "unknowns_to_verify",
                    minimum=1,
                    maximum=5,
                    truncate_overflow=False,
                ),
                "key_outcomes": _texts(
                    raw.get("key_outcomes"),
                    "key_outcomes",
                    minimum=3,
                    maximum=5,
                ),
                "must_have_signals": _texts(
                    raw.get("must_have_signals"),
                    "must_have_signals",
                    minimum=3,
                    maximum=5,
                ),
                "preferred_signals": _texts(
                    raw.get("preferred_signals"),
                    "preferred_signals",
                    minimum=1,
                    maximum=3,
                ),
                "specificity_terms": _texts(
                    raw.get("specificity_terms"),
                    "specificity_terms",
                    minimum=3,
                    maximum=8,
                ),
                "city": city,
                "city_basis": city_basis,
            }
        )
    watch_for = _texts(
        value.get("watch_for") or [],
        "watch_for",
        minimum=1 if not hypotheses else 0,
        maximum=5,
    )
    return {
        "lead_index": lead_index,
        "company": company,
        "stage_transition": _text(value.get("stage_transition"), "stage_transition"),
        "organizational_gaps": _texts(
            value.get("organizational_gaps") or [],
            "organizational_gaps",
            minimum=0,
            maximum=5,
        ),
        "hypotheses": hypotheses,
        "watch_for": watch_for,
    }


__all__ = [
    "COMPANY_DEMAND_SYSTEM_PROMPT",
    "OPERATIONAL_ROLE_SIGNAL_TYPES",
    "build_company_demand_repair_prompt",
    "build_company_evidence_packets",
    "build_single_company_demand_prompt",
    "parse_single_company_demand",
]
