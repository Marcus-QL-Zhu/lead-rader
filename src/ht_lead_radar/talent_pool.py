"""Build anonymized Director+ talent-pool drafts from an existing Lead report.

This module is deliberately deterministic and offline.  It never performs
discovery or verification calls; the daily Lead report is its only input.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


LIEPIN_SENIORITY = frozenset({"1-3年", "3-5年", "5-10年", "10年以上"})
LIEPIN_EDUCATION = frozenset(
    {"不限", "大专", "本科", "硕士", "MBA/EMBA", "博士", "中专/中技"}
)
LIEPIN_PUBLIC_PAYLOAD_FIELDS = frozenset(
    {
        "position_name",
        "position_scope",
        "cities",
        "seniority",
        "work_experience_years",
        "education",
        "salary_low",
        "salary_high",
        "must_have_signals",
        "preferred_signals",
        "benefits",
        "job_type",
        "languages",
        "recruit_count",
        "target_count",
    }
)
DIRECTOR_MARKERS = (
    "总监",
    "负责人",
    "head",
    "vp",
    "副总裁",
    "总经理",
    "cto",
    "首席",
)
EXCLUDED_MARKERS = ("经理", "专家", "principal", "staff", "fellow")
PUBLIC_DISCLAIMER_MARKERS = (
    "人才蓄水",
    "长期机会储备",
    "不代表特定企业",
    "不代表某一特定企业",
    "不构成任何企业真实招聘委托",
)


@dataclass(frozen=True)
class SourceLead:
    company: str
    score: float
    role_hypotheses: tuple[str, ...]
    evidence_urls: tuple[str, ...]
    event_types: tuple[str, ...]


@dataclass(frozen=True)
class TalentPoolDraft:
    draft_id: str
    run_date: str
    direction: str
    talent_persona: str
    role_family: str
    seniority: str
    attraction_angle: str
    recommended_title: str
    why_now: str
    source_leads: tuple[SourceLead, ...]
    source_role_hypotheses: tuple[str, ...]
    public_payload: dict[str, Any]
    payload_hash: str
    status: str = "pending_approval"
    expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DraftBundle:
    schema_version: int
    run_date: str
    direction: str
    source_run_id: str
    drafts: tuple[TalentPoolDraft, ...]
    generation_error: str = ""
    generation_provider: str = "template"
    generation_model: str = ""
    company_demand_analysis: tuple[dict[str, Any], ...] = ()
    talent_themes: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoleTemplate:
    family: str
    persona: str
    title: str
    keywords: tuple[str, ...]
    responsibilities: tuple[str, ...]
    requirements: tuple[str, ...]


TEMPLATES = (
    RoleTemplate(
        "研发与工程",
        "复杂硬科技产品的研发与工程负责人",
        "硬科技研发总监",
        ("研发", "技术", "算法", "机器人", "软件", "硬件", "cto"),
        (
            "制定中长期技术路线并对关键里程碑负责",
            "搭建跨学科研发团队及核心岗位梯队",
            "推动原型、工程化与量产之间的协同",
            "建立研发流程、质量门槛和复盘机制",
            "管理外部技术合作及关键供应商",
        ),
        (
            "十年以上硬科技研发或工程管理经验",
            "有从零到一产品化或规模交付经历",
            "管理过多职能研发团队及核心预算",
            "能把技术路线转化为业务里程碑",
            "具备复杂问题拆解和组织建设能力",
        ),
    ),
    RoleTemplate(
        "产品与商业化",
        "从技术验证走向规模商业化的产品负责人",
        "硬科技产品总监",
        ("产品", "商业化", "解决方案", "市场", "业务"),
        (
            "定义产品组合、路线图和目标市场",
            "连接研发、销售、交付与客户需求",
            "建立需求洞察和产品决策机制",
            "推动标杆客户验证与可复制方案",
            "对产品商业结果和团队建设负责",
        ),
        (
            "十年以上复杂技术产品管理经验",
            "有技术产品从验证到商业化的经历",
            "理解研发边界并能管理客户预期",
            "具备跨部门资源协调和团队领导力",
            "对硬科技产业链和客户场景有认知",
        ),
    ),
    RoleTemplate(
        "供应链与制造",
        "面向量产爬坡与国产替代的供应链负责人",
        "供应链总监",
        ("供应链", "采购", "制造", "生产", "工厂", "质量", "交付"),
        (
            "制定关键品类供应与国产替代策略",
            "建设供应商开发、认证和绩效体系",
            "支持新产品导入及产能爬坡",
            "统筹成本、质量、交付和供应风险",
            "搭建供应链团队与跨部门协同机制",
        ),
        (
            "十年以上先进制造供应链管理经验",
            "有关键物料导入或国产替代实绩",
            "熟悉研发导入、质量及量产流程",
            "管理过复杂供应商网络和核心预算",
            "具备风险预判及组织建设能力",
        ),
    ),
    RoleTemplate(
        "销售与业务拓展",
        "推动大客户突破与新市场复制的业务负责人",
        "业务拓展总监",
        ("销售", "商务", "业务拓展", "客户", "国际", "渠道"),
        (
            "制定重点行业及大客户拓展策略",
            "建立从线索到规模合作的销售体系",
            "推动标杆项目落地并沉淀复制方法",
            "协同产品与交付管理商业承诺",
            "组建高绩效业务团队并对结果负责",
        ),
        (
            "十年以上硬科技行业商业拓展经验",
            "有复杂项目和关键客户突破记录",
            "理解技术方案、采购链条与决策流程",
            "具备团队管理和业务体系建设能力",
            "能够平衡增长速度、毛利与交付风险",
        ),
    ),
    RoleTemplate(
        "战略与组织",
        "支撑高速成长期组织升级的战略负责人",
        "战略与运营总监",
        ("战略", "运营", "组织", "人力", "hr", "融资"),
        (
            "把公司战略拆解为年度经营重点",
            "建立跨部门经营分析和决策机制",
            "推动关键组织能力与人才梯队建设",
            "牵引重大专项并跟踪经营闭环",
            "支持管理层识别增长与执行风险",
        ),
        (
            "十年以上战略、运营或组织管理经验",
            "服务过快速成长的技术型企业",
            "能够连接业务目标、组织与财务指标",
            "具备复杂项目推动和团队领导能力",
            "善于在不确定环境中建立运行机制",
        ),
    ),
)

ANGLE_BY_EVENT = {
    "funding": "从融资后扩张到组织能力落地",
    "factory_or_capacity": "从产能建设到规模交付",
    "major_order": "从标杆订单到可复制增长",
    "partnership": "从生态合作到新市场打开",
    "data_or_model": "从技术突破到产品化",
    "product_launch": "从新品发布到商业闭环",
}

FAMILY_BY_EVENT = {
    "funding": "战略与组织",
    "factory_or_capacity": "供应链与制造",
    "major_order": "销售与业务拓展",
    "partnership": "销售与业务拓展",
    "data_or_model": "研发与工程",
    "product_launch": "产品与商业化",
}


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def is_director_plus(title: str) -> bool:
    lowered = title.casefold()
    excluded = any(marker in lowered for marker in EXCLUDED_MARKERS if marker != "经理")
    if excluded or ("经理" in lowered and "总经理" not in lowered):
        return False
    return any(marker in lowered for marker in DIRECTOR_MARKERS)


def validate_liepin_payload(payload: Mapping[str, Any]) -> None:
    """Validate the final JSON contract consumed by Liepin Skills."""

    missing = sorted(LIEPIN_PUBLIC_PAYLOAD_FIELDS - set(payload))
    if missing:
        raise ValueError(f"missing Liepin fields: {', '.join(missing)}")
    extra = sorted(set(payload) - LIEPIN_PUBLIC_PAYLOAD_FIELDS - {"work_email"})
    if extra:
        raise ValueError(f"unsupported Liepin fields: {', '.join(extra)}")
    if not is_director_plus(str(payload["position_name"])):
        raise ValueError("position_name must be Director+")
    scope = str(payload["position_scope"]).strip()
    if not scope or len(scope) > 500:
        raise ValueError("position_scope must contain 1-500 characters")
    if any(marker in scope for marker in PUBLIC_DISCLAIMER_MARKERS):
        raise ValueError("position_scope contains unsupported prefatory text")
    sections = scope.split("\n\n")
    if len(sections) != 2:
        raise ValueError("position_scope must contain exactly two separated sections")
    for section, header in zip(
        sections,
        ("【岗位职责】", "【任职要求】"),
        strict=True,
    ):
        lines = section.splitlines()
        if not lines or lines[0] != header:
            raise ValueError(f"position_scope must contain {header}")
        bullets = lines[1:]
        if not 5 <= len(bullets) <= 10 or not all(
            line.startswith("• ") and line[2:].strip() for line in bullets
        ):
            raise ValueError(f"{header} must contain 5-10 non-empty '• ' bullet lines")
    if payload["seniority"] not in LIEPIN_SENIORITY:
        raise ValueError("invalid Liepin seniority enum")
    if payload["education"] not in LIEPIN_EDUCATION:
        raise ValueError("invalid Liepin education enum")
    cities = payload["cities"]
    if (
        not isinstance(cities, list)
        or len(cities) != 1
        or not all(isinstance(item, str) and item.strip() for item in cities)
    ):
        raise ValueError("cities must contain exactly one city")
    for key in ("salary_low", "salary_high"):
        if not re.fullmatch(r"\d+k", str(payload[key])):
            raise ValueError(f"{key} must use the Liepin '30k' format")
    low = int(str(payload["salary_low"])[:-1])
    high = int(str(payload["salary_high"])[:-1])
    if not 0 < low < high <= 85 or high - low > 20:
        raise ValueError("salary range violates Liepin constraints")
    if not isinstance(payload["target_count"], int) or payload["target_count"] <= 0:
        raise ValueError("target_count must be a positive integer")
    for key in (
        "must_have_signals",
        "preferred_signals",
        "benefits",
        "languages",
    ):
        value = payload[key]
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            raise ValueError(f"{key} must be a non-empty string list")
    if payload["job_type"] != "社招":
        raise ValueError("job_type must be 社招")
    if payload["languages"] != ["普通话"]:
        raise ValueError("languages must equal ['普通话']")
    if "五险一金" not in payload["benefits"]:
        raise ValueError("benefits must include 五险一金")
    if not isinstance(payload["recruit_count"], int) or payload["recruit_count"] <= 0:
        raise ValueError("recruit_count must be a positive integer")
    experience = payload.get("work_experience_years")
    if (
        not isinstance(experience, list)
        or len(experience) not in {1, 2}
        or not all(isinstance(item, int) and item >= 0 for item in experience)
    ):
        raise ValueError("work_experience_years must be [min] or [min, max]")


def build_liepin_position_scope(
    responsibilities: Iterable[str],
    requirements: Iterable[str],
) -> str:
    """Build the two-section bullet format required by liepin-job-posting."""

    responsibility_items = [
        str(item).strip() for item in responsibilities if str(item).strip()
    ]
    requirement_items = [
        str(item).strip() for item in requirements if str(item).strip()
    ]
    for label, items in (
        ("岗位职责", responsibility_items),
        ("任职要求", requirement_items),
    ):
        if not 5 <= len(items) <= 10:
            raise ValueError(f"{label} must contain 5-10 items")
    scope = (
        "【岗位职责】\n"
        + "\n".join(f"• {item}" for item in responsibility_items)
        + "\n\n【任职要求】\n"
        + "\n".join(f"• {item}" for item in requirement_items)
    )
    if len(scope) > 500:
        raise ValueError("position_scope must contain at most 500 characters")
    return scope


def assert_anonymized(
    payload: Mapping[str, Any],
    *,
    forbidden_terms: Iterable[str],
) -> None:
    public_text = json.dumps(payload, ensure_ascii=False).casefold()
    leaked = sorted(
        {
            term.strip()
            for term in forbidden_terms
            if len(term.strip()) >= 2 and term.strip().casefold() in public_text
        }
    )
    if leaked:
        raise ValueError(
            "public payload leaks source identifiers: " + ", ".join(leaked)
        )


def _role_template(title: str) -> RoleTemplate:
    lowered = title.casefold()
    for template in TEMPLATES:
        if any(keyword.casefold() in lowered for keyword in template.keywords):
            return template
    return TEMPLATES[0]


def _source_lead(lead: Mapping[str, Any]) -> SourceLead:
    evidence = lead.get("evidence") or ()
    return SourceLead(
        company=str(lead.get("company") or "").strip(),
        score=float(lead.get("score") or 0),
        role_hypotheses=tuple(
            str(item).strip()
            for item in (lead.get("target_roles") or ())
            if str(item).strip()
        ),
        evidence_urls=tuple(
            dict.fromkeys(
                str(item.get("source_url") or "").strip()
                for item in evidence
                if isinstance(item, Mapping)
                and str(item.get("source_url") or "").strip()
            )
        ),
        event_types=tuple(
            dict.fromkeys(
                str(item.get("event_type") or "").strip()
                for item in evidence
                if isinstance(item, Mapping)
                and str(item.get("event_type") or "").strip()
            )
        ),
    )


def _forbidden_terms(lead: Mapping[str, Any]) -> set[str]:
    result = {str(lead.get("company") or "").strip()}
    for evidence in lead.get("evidence") or ():
        if not isinstance(evidence, Mapping):
            continue
        result.update(str(item).strip() for item in evidence.get("people") or ())
    research = lead.get("basic_research") or {}
    if isinstance(research, Mapping):
        for key in ("aliases", "products", "founders", "customers"):
            values = research.get(key) or ()
            if isinstance(values, str):
                values = (values,)
            result.update(str(item).strip() for item in values)
    return {item for item in result if item}


def _scope(template: RoleTemplate, direction: str, angle: str) -> str:
    del direction, angle
    return build_liepin_position_scope(
        template.responsibilities,
        template.requirements,
    )


def _public_direction(direction: str) -> str:
    """Reduce arbitrary internal input to a broad, non-identifying sector."""

    lowered = direction.casefold()
    mappings = (
        (("半导体", "芯片"), "半导体"),
        (("具身", "机器人", "灵巧手"), "智能机器人"),
        (("航天", "卫星", "火箭"), "商业航天"),
        (("核聚变", "聚变"), "先进能源"),
        (("脑机",), "脑机接口"),
        (("新能源", "储能"), "先进能源"),
    )
    for markers, label in mappings:
        if any(marker in lowered for marker in markers):
            return label
    return "硬科技"


def _public_payload(
    template: RoleTemplate, *, direction: str, angle: str
) -> dict[str, Any]:
    public_direction = _public_direction(direction)
    payload = {
        "position_name": template.title,
        "position_scope": _scope(template, public_direction, angle),
        "cities": ["上海"],
        "seniority": "10年以上",
        "work_experience_years": [10],
        "education": "本科",
        "salary_low": "50k",
        "salary_high": "70k",
        "must_have_signals": list(template.requirements[:5]),
        "preferred_signals": [
            "有跨阶段组织升级经验",
            "有硬科技产业链协同经验",
            "能够在高不确定环境中推进结果",
        ],
        "benefits": [
            "五险一金",
            "带薪年假",
        ],
        "target_count": 10,
        "job_type": "社招",
        "recruit_count": 1,
        "languages": ["普通话"],
    }
    validate_liepin_payload(payload)
    return payload


def generate_draft_bundle(
    report: Mapping[str, Any],
    *,
    target_count: int = 5,
    minimum_count: int = 3,
    maximum_count: int = 10,
) -> DraftBundle:
    if not minimum_count <= target_count <= maximum_count <= 10:
        raise ValueError(
            "draft count must satisfy 1 <= minimum <= target <= maximum <= 10"
        )
    manifest = report.get("manifest") or {}
    run_date = str(manifest.get("as_of") or "").strip()
    direction = str(manifest.get("direction") or "").strip()
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_date or not direction:
        raise ValueError("report manifest requires as_of and direction")
    leads = [item for item in (report.get("leads") or ()) if isinstance(item, Mapping)]
    if not leads:
        return DraftBundle(1, run_date, direction, run_id, ())

    groups: dict[str, dict[str, Any]] = {}
    forbidden: set[str] = set()
    for lead in leads:
        forbidden.update(_forbidden_terms(lead))
        source = _source_lead(lead)
        roles = list(source.role_hypotheses) or ["研发总监"]
        for role in roles:
            template = _role_template(role)
            group = groups.setdefault(
                template.family,
                {"template": template, "sources": [], "roles": [], "events": []},
            )
            group["sources"].append(source)
            group["roles"].append(role)
            group["events"].extend(source.event_types)

    # Add only event-supported adjacent personas toward the default target.
    # If the report is extremely sparse, fill only the hard minimum with broad
    # cross-client personas rather than pretending five equally strong signals.
    sources = [_source_lead(item) for item in leads[:3]]
    events = [event for source in sources for event in source.event_types]
    roles = [role for source in sources for role in source.role_hypotheses]
    templates_by_family = {template.family: template for template in TEMPLATES}
    adjacent_families = list(
        dict.fromkeys(
            FAMILY_BY_EVENT[event] for event in events if event in FAMILY_BY_EVENT
        )
    )
    for family in adjacent_families:
        if len(groups) >= target_count:
            break
        template = templates_by_family[family]
        if template.family in groups:
            continue
        groups[template.family] = {
            "template": template,
            "sources": sources,
            "roles": roles,
            "events": events,
        }
    for template in TEMPLATES:
        if len(groups) >= minimum_count:
            break
        if template.family not in groups:
            groups[template.family] = {
                "template": template,
                "sources": sources,
                "roles": roles,
                "events": events,
            }

    ranked = sorted(
        groups.values(),
        key=lambda group: (
            -max((source.score for source in group["sources"]), default=0),
            group["template"].family,
        ),
    )[:maximum_count]
    selected = ranked[:target_count] if len(ranked) >= target_count else ranked
    drafts: list[TalentPoolDraft] = []
    for group in selected:
        template: RoleTemplate = group["template"]
        event_types = tuple(dict.fromkeys(group["events"]))
        angle = next(
            (ANGLE_BY_EVENT[event] for event in event_types if event in ANGLE_BY_EVENT),
            "从关键业务信号到组织能力提前储备",
        )
        payload = _public_payload(template, direction=direction, angle=angle)
        assert_anonymized(payload, forbidden_terms=forbidden)
        payload_hash = canonical_payload_hash(payload)
        identity = "\x1f".join(
            (run_date, direction, template.persona, template.family, angle)
        )
        draft_id = "tp_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        why_now = (
            f"当日{direction}市场信号集中指向“{angle}”；"
            "先建立跨客户可用的总监级人才池，有助于早于正式招聘广告形成供给。"
        )
        unique_sources = {
            source.company: source for source in group["sources"] if source.company
        }
        drafts.append(
            TalentPoolDraft(
                draft_id=draft_id,
                run_date=run_date,
                direction=direction,
                talent_persona=template.persona,
                role_family=template.family,
                seniority="Director+",
                attraction_angle=angle,
                recommended_title=template.title,
                why_now=why_now,
                source_leads=tuple(unique_sources.values()),
                source_role_hypotheses=tuple(dict.fromkeys(group["roles"])),
                public_payload=payload,
                payload_hash=payload_hash,
                expires_at=draft_expiry_date(run_date),
            )
        )
    if drafts and not minimum_count <= len(drafts) <= maximum_count:
        raise ValueError("could not build the required differentiated draft count")
    return DraftBundle(1, run_date, direction, run_id, tuple(drafts))


def draft_expiry_date(run_date: str) -> str:
    """Return the inclusive review deadline for a generated draft."""

    from datetime import date, timedelta

    return (date.fromisoformat(run_date) + timedelta(days=7)).isoformat()


def write_draft_bundle(bundle: DraftBundle, output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_draft_bundle(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("draft bundle must be a JSON object")
    return payload


__all__ = [
    "DraftBundle",
    "TalentPoolDraft",
    "assert_anonymized",
    "canonical_payload_hash",
    "draft_expiry_date",
    "generate_draft_bundle",
    "is_director_plus",
    "load_draft_bundle",
    "validate_liepin_payload",
    "write_draft_bundle",
]
