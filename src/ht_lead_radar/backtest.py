"""Leakage-safe historical replay for early Director+ hiring predictions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import urlsplit

from .company_demand_v2 import (
    COMPANY_DEMAND_SYSTEM_PROMPT,
    build_company_demand_repair_prompt,
    build_single_company_demand_prompt,
    parse_single_company_demand,
)
from .models import Evidence
from .company_timeline import build_company_timeline
from .signals import canonical_event_type
from .talent_demand_analysis import DemandAnalysisError
from .taxonomy import classify_seniority


class PromptRunner(Protocol):
    def run(
        self,
        prompt: str,
        *,
        session_id: str,
        system_prompt: str = "",
    ) -> str: ...


@dataclass(frozen=True)
class HistoricalJob:
    company: str
    title: str
    description: str
    published_at: str
    source_url: str
    source_name: str = ""
    observed_at: str = ""
    content_sha256: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HistoricalJob":
        return cls(
            company=str(value.get("company") or "").strip(),
            title=str(
                value.get("title")
                or value.get("exact_title")
                or ""
            ).strip(),
            description=str(
                value.get("description")
                or value.get("responsibilities_summary")
                or ""
            ).strip(),
            published_at=str(value.get("published_at") or "").strip(),
            source_url=str(value.get("source_url") or "").strip(),
            source_name=str(value.get("source_name") or "").strip(),
            observed_at=str(value.get("observed_at") or "").strip(),
            content_sha256=str(value.get("content_sha256") or "").strip(),
        )


@dataclass(frozen=True)
class BacktestConfig:
    cutoff: date
    horizon_months: int = 3
    include_workforce_precursors: bool = False
    max_roles_per_company: int = 5
    prompt_version: str = "historical-demand-v8-anonymized"
    experiment_id: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.max_roles_per_company <= 5:
            raise ValueError("max_roles_per_company must be between 1 and 5")
        if not self.prompt_version.strip():
            raise ValueError("prompt_version is required")


@dataclass(frozen=True)
class PredictionMatch:
    company: str
    predicted_title: str
    predicted_family: str
    status: str
    actual_title: str = ""
    actual_url: str = ""
    actual_published_at: str = ""
    lead_days: int | None = None
    canonical_role_key: str = ""
    company_type: str = ""
    actual_job_id: str = ""


HISTORICAL_PROMPT_VERSION = "historical-demand-v8-anonymized"
HISTORICAL_TEMPORAL_EMBARGO = """
历史回放约束：
- 你正处于公司事实包 simulated_as_of 所示日期，只能使用事实包中明确提供的事实。
- 禁止使用、暗示或反向推导 simulated_as_of 当日及之后的新闻、招聘广告、职位名称或模型记忆。
- 即使你在训练知识或上下文中知道后来发生了什么，也必须忽略；不确定时输出空假设和待观察信号。
""".strip()


ROLE_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("general_management", ("总经理", "总裁", "ceo", "president", "managing director")),
    (
        "strategy_transformation",
        (
            "战略",
            "转型",
            "组织发展",
            "董事会办公室",
            "strategy",
            "transformation",
            "organization development",
            "chief of staff",
        ),
    ),
    (
        "human_resources",
        ("人力", "hr", "人才", "组织效能", "human resources", "people", "talent"),
    ),
    (
        "finance_control",
        (
            "财务",
            "财经",
            "内控",
            "审计",
            "cfo",
            "finance",
            "financial control",
            "audit",
            "controlling",
        ),
    ),
    (
        "capital_markets",
        (
            "董秘",
            "证券事务",
            "资本市场",
            "投资者关系",
            "capital markets",
            "investor relations",
            "board secretary",
        ),
    ),
    (
        "corporate_development",
        (
            "投资",
            "并购",
            "整合",
            "corporate development",
            "investment",
            "acquisition",
            "m&a",
            "integration",
        ),
    ),
    (
        "research_development",
        (
            "研发",
            "技术",
            "架构",
            "cto",
            "总师",
            "首席科学家",
            "工程平台",
            "research",
            "engineering",
            "technology",
            "architect",
        ),
    ),
    (
        "algorithm_data",
        (
            "算法",
            "数据",
            "模型",
            "ai平台主管",
            "软件平台",
            "algorithm",
            "data",
            "model",
            "ai platform",
            "software platform",
        ),
    ),
    ("product", ("产品", "产品线", "product", "product line")),
    (
        "program_delivery",
        (
            "项目",
            "交付",
            "客户成功",
            "program",
            "project",
            "delivery",
            "customer success",
        ),
    ),
    (
        "manufacturing",
        (
            "制造",
            "量产",
            "产能",
            "总装",
            "生产",
            "工厂",
            "基地运营",
            "manufacturing",
            "production",
            "plant operations",
        ),
    ),
    (
        "process_engineering",
        (
            "工艺",
            "工程化",
            "工程落地",
            "系统集成",
            "设备",
            "厂务",
            "process engineering",
            "industrialization",
            "equipment",
            "facilities",
            "system integration",
        ),
    ),
    ("quality", ("质量", "可靠性", "验证", "quality", "reliability", "validation")),
    (
        "supply_chain",
        (
            "供应链",
            "采购",
            "物料",
            "supply chain",
            "procurement",
            "sourcing",
            "material",
        ),
    ),
    (
        "sales_accounts",
        ("销售", "大客户", "客户", "商务", "sales", "account", "customer"),
    ),
    (
        "commercialization",
        (
            "商业化",
            "业务发展",
            "市场拓展",
            "增长",
            "growth excellence",
            "commercial excellence",
            "commercial activation",
            "go-to-market",
            "pricing",
            "business development",
            "commercialization",
        ),
    ),
    (
        "channel_ecosystem",
        (
            "渠道",
            "生态",
            "合作伙伴",
            "开发者关系",
            "channel",
            "ecosystem",
            "alliance",
            "partner",
        ),
    ),
    (
        "international",
        ("海外", "国际", "全球", "区域总经理", "international", "overseas"),
    ),
    ("marketing", ("市场", "品牌", "产品营销", "marketing", "brand")),
    (
        "government_affairs",
        ("政府事务", "公共事务", "产业关系", "government affairs", "public affairs"),
    ),
    (
        "regulatory_clinical",
        (
            "法规",
            "注册",
            "临床",
            "医学事务",
            "适航",
            "regulatory",
            "clinical",
            "medical affairs",
        ),
    ),
    (
        "ehs_compliance",
        ("ehs", "合规", "安全", "环境", "compliance", "safety", "environment"),
    ),
    ("legal", ("法务", "法律", "legal", "general counsel", "counsel")),
    (
        "digital_it",
        (
            "数字化",
            "信息化",
            "it",
            "erp",
            "mes",
            "plm",
            "digital",
            "information technology",
        ),
    ),
    (
        "research_partnership",
        (
            "科研合作",
            "学术合作",
            "联合实验室",
            "academic partnership",
            "research partnership",
            "joint laboratory",
        ),
    ),
    (
        "application_solutions",
        (
            "应用工程",
            "系统解决方案",
            "应用创新",
            "application management",
            "application engineering",
            "customer engineer",
            "solutions engineering",
        ),
    ),
)


def _contains_term(text: str, term: str) -> bool:
    """Match ASCII ontology terms as tokens, not inside unrelated words."""

    folded_text = text.casefold()
    folded_term = term.casefold()
    if re.search(r"[a-z0-9]", folded_term):
        return (
            re.search(
                rf"(?<![a-z0-9]){re.escape(folded_term)}(?![a-z0-9])",
                folded_text,
            )
            is not None
        )
    return folded_term in folded_text


def parse_date(value: str) -> date | None:
    match = re.search(r"(20\d{2})[-/年](\d{1,2})(?:[-/月](\d{1,2}))?", value or "")
    if not match:
        return None
    try:
        return date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 1),
        )
    except ValueError:
        return None


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    month_lengths = (
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    )
    return date(year, month, min(value.day, month_lengths[month - 1]))


def _source_group(item: Evidence) -> str:
    explicit = item.independent_source_group.strip()
    if explicit:
        return explicit
    return urlsplit(item.source_url).netloc.casefold()


def _availability_date(item: Evidence) -> date | None:
    values = [
        parsed
        for raw in (item.published_at, item.observed_at)
        if raw.strip() and (parsed := parse_date(raw)) is not None
    ]
    return max(values) if values else None


def evidence_before_cutoff(
    evidence: Iterable[Evidence],
    config: BacktestConfig,
) -> list[Evidence]:
    selected: list[Evidence] = []
    approved_source_kinds = {
        "company_official",
        "government_official",
        "exchange_filing",
        "regulator_official",
        "academic_official",
        "mainstream_media",
    }
    for item in evidence:
        event_type = canonical_event_type(item.event_type)
        occurred = parse_date(item.event_date)
        available = _availability_date(item)
        source_kind = item.source_kind.casefold()
        if source_kind == "mainstream_media" and not item.observed_at.strip():
            continue
        if (
            occurred is None
            or available is None
            or not item.source_excerpt.strip()
            or occurred >= config.cutoff
            or available >= config.cutoff
        ):
            continue
        recruiting_text = f"{item.title}\n{item.source_excerpt}\n{item.snippet}"
        source_kind = item.source_kind.casefold()
        explicit_recruiting = (
            item.is_recruiting_input
            or source_kind in {
                "job_ad", "job_board", "ats", "recruiting", "josint"
            }
            or re.search(
                r"招聘|职位发布|岗位要求|任职要求|投递简历|加入我们|"
                r"apply now|job description|careers?\b|we(?:'re| are) hiring|"
                r"vacanc(?:y|ies)|job opening|open positions?|join our team|"
                r"\b(?:now\s+)?hiring\b|"
                r"\brecruit(?:s|ed|er|ers|ing|ment)?\b|"
                r"\bapplications?\s+(?:(?:is|are)\s+)?(?:now\s+)?open\b",
                recruiting_text,
                re.I,
            )
            is not None
        )
        if (
            event_type == "job_ad"
            or explicit_recruiting
            or source_kind not in approved_source_kinds
        ):
            continue
        if (
            event_type == "workforce_cluster"
            and not config.include_workforce_precursors
        ):
            continue
        selected.append(
            Evidence(
                **{
                    **asdict(item),
                    "event_type": event_type,
                }
            )
        )
    return selected


def build_prediction_packets(
    evidence: Iterable[Evidence],
    config: BacktestConfig,
) -> tuple[dict[str, Any], ...]:
    grouped: dict[str, list[Evidence]] = {}
    for item in evidence_before_cutoff(evidence, config):
        grouped.setdefault(item.company, []).append(item)
    packets: list[dict[str, Any]] = []
    for lead_index, company in enumerate(sorted(grouped), start=1):
        items = sorted(grouped[company], key=lambda item: item.event_date)
        company_types = {item.company_type for item in items}
        if len(company_types) != 1 or next(iter(company_types), "") not in {
            "startup_private",
            "listed",
            "foreign",
        }:
            raise ValueError(
                f"historical evidence must have one frozen company_type: {company}"
            )
        timeline_input = []
        for item in items:
            record = asdict(item)
            record.pop("content_sha256", None)
            content_sha256 = _stable_hash(record)
            if item.content_sha256 and item.content_sha256 != content_sha256:
                raise ValueError(
                    f"historical evidence content hash mismatch: {item.company}"
                )
            timeline_input.append({**asdict(item), "content_sha256": content_sha256})
        timeline = build_company_timeline(
            timeline_input,
            as_of=config.cutoff,
            limit=8,
            allow_undated=False,
        )
        packets.append(
            {
                "lead_index": lead_index,
                "company": company,
                "direction": items[0].direction,
                "simulated_as_of": config.cutoff.isoformat(),
                "evidence": list(timeline["evidence"]),
                "timeline": {
                    key: value
                    for key, value in timeline.items()
                    if key not in {"evidence", "buckets"}
                },
                "known_context": {},
                "company_type": items[0].company_type,
            }
        )
    return tuple(packets)


def _anonymize_prediction_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Remove direct company/domain identifiers before a historical LLM call."""

    company = str(packet.get("company") or "")
    company_id = f"Candidate-{int(packet['lead_index']):03d}"
    raw_organizations = {
        company,
        *(str(value) for item in packet.get("evidence") or () for value in item.get("organizations") or ()),
    }
    organizations: set[str] = set()
    for organization in raw_organizations:
        organization = organization.strip()
        if not organization:
            continue
        organizations.add(organization)
        organizations.update(
            part.strip()
            for part in re.split(r"[()（）]", organization)
            if len(part.strip()) >= 2
        )

    def scrub(value: str) -> str:
        result = str(value or "")
        for organization in sorted(organizations, key=len, reverse=True):
            if organization:
                result = result.replace(organization, "该公司")
        return result

    evidence = []
    source_group_aliases: dict[str, str] = {}
    for index, item in enumerate(packet.get("evidence") or (), start=1):
        original_group = str(item.get("source_group") or "").strip()
        if original_group not in source_group_aliases:
            source_group_aliases[original_group] = (
                f"source-group-{len(source_group_aliases) + 1}"
            )
        evidence.append(
            {
                **dict(item),
                "title": f"截止日前公开运营证据 #{index}",
                "fact": scrub(str(item.get("fact") or "")),
                "source_url": "",
                "source_locator": "",
                "source_group": source_group_aliases[original_group],
                "people": [],
                "organizations": [],
            }
        )
    return {
        **dict(packet),
        "company": company_id,
        "company_type": "",
        "direction": scrub(str(packet.get("direction") or "")),
        "evidence": evidence,
    }


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runner_metadata(runner: PromptRunner) -> dict[str, Any]:
    config = getattr(runner, "config", None)
    return {
        "provider": str(getattr(config, "provider", "") or ""),
        "model": str(getattr(config, "model", "") or ""),
        "api_kind": str(getattr(config, "api_kind", "") or ""),
        "temperature": float(getattr(config, "temperature", 0.0) or 0.0),
    }


def _cap_analysis_hypotheses(
    analysis: Mapping[str, Any],
    max_roles: int,
) -> dict[str, Any]:
    """Apply the frozen top-k limit deterministically after model parsing."""
    capped = dict(analysis)
    hypotheses = list(capped.get("hypotheses") or ())
    capped["hypotheses"] = hypotheses[:max_roles]
    return capped


def run_historical_predictions(
    evidence: Iterable[Evidence],
    config: BacktestConfig,
    runner: PromptRunner,
) -> dict[str, Any]:
    packets = build_prediction_packets(evidence, config)
    analyses: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    company_types = {}
    for packet in packets:
        company = str(packet["company"])
        company_type = str(packet.get("company_type") or "")
        if company_type not in {"startup_private", "listed", "foreign"}:
            raise ValueError(f"historical evidence has invalid company_type: {company}")
        company_types[company] = company_type
    model_packets = tuple(_anonymize_prediction_packet(packet) for packet in packets)
    prompt_audit: list[dict[str, Any]] = []
    system_prompt = COMPANY_DEMAND_SYSTEM_PROMPT + "\n\n" + HISTORICAL_TEMPORAL_EMBARGO
    for packet, model_packet in zip(packets, model_packets, strict=True):
        experiment = config.experiment_id.strip() or config.prompt_version
        session_id = (
            f"lead-radar-backtest:{experiment}:"
            f"{config.cutoff.isoformat()}:{packet['lead_index']}"
        )
        user_prompt = (
            HISTORICAL_TEMPORAL_EMBARGO
            + "\n\n"
            + build_single_company_demand_prompt(
                model_packet,
                max_roles=config.max_roles_per_company,
            )
        )
        response = runner.run(
            user_prompt,
            session_id=session_id,
            system_prompt=system_prompt,
        )
        audit: dict[str, Any] = {
            "company": packet["company"],
            "model_company_id": model_packet["company"],
            "session_id": session_id,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "system_prompt_sha256": _stable_hash(system_prompt),
            "user_prompt_sha256": _stable_hash(user_prompt),
            "response": response,
            "response_sha256": _stable_hash(response),
        }
        try:
            analysis = parse_single_company_demand(response, packet=model_packet)
        except DemandAnalysisError as first_error:
            repair_prompt = build_company_demand_repair_prompt(
                model_packet, response, first_error
            )
            repaired = runner.run(
                HISTORICAL_TEMPORAL_EMBARGO + "\n\n" + repair_prompt,
                session_id=session_id + ":repair",
                system_prompt=system_prompt,
            )
            audit["repair_prompt"] = (
                HISTORICAL_TEMPORAL_EMBARGO + "\n\n" + repair_prompt
            )
            audit["repair_response"] = repaired
            audit["repair_prompt_sha256"] = _stable_hash(audit["repair_prompt"])
            audit["repair_response_sha256"] = _stable_hash(repaired)
            try:
                analysis = parse_single_company_demand(repaired, packet=model_packet)
            except DemandAnalysisError as repair_error:
                failures.append(
                    {
                        "company": str(packet["company"]),
                        "error": f"{type(repair_error).__name__}: {repair_error}",
                    }
                )
                prompt_audit.append(audit)
                continue
        analysis = _cap_analysis_hypotheses(
            analysis,
            config.max_roles_per_company,
        )
        analysis = {**analysis, "company": packet["company"]}
        analyses.append(analysis)
        prompt_audit.append(audit)
    return {
        "manifest": {
            "snapshot_schema_version": 3,
            "prompt_version": config.prompt_version,
            "cutoff": config.cutoff.isoformat(),
            "horizon_months": config.horizon_months,
            "prediction_max_roles_per_company": config.max_roles_per_company,
            "workforce_precursors_enabled": config.include_workforce_precursors,
            "prediction_inputs_exclude_job_ads": True,
            "josint_inputs_enabled": False,
            "candidate_companies": sorted(company_types),
            "prediction_packets_sha256": _stable_hash(packets),
            "model_packets_sha256": _stable_hash(model_packets),
            "system_prompt_sha256": _stable_hash(system_prompt),
            "runner": _runner_metadata(runner),
        },
        "prediction_packets": packets,
        "model_packets": model_packets,
        "prompt_audit": prompt_audit,
        "analyses": analyses,
        "failures": failures,
        "company_types": company_types,
    }


def _title_role_family(title: str) -> str:
    text = title.casefold()
    if any(
        term in text
        for term in (
            "\u6218\u7565\u5408\u4f5c",
            "\u751f\u6001\u5408\u4f5c",
            "strategic alliance",
            "alliance lead",
        )
    ):
        return "channel_ecosystem"
    if any(term in text for term in ("总经理", "总裁", "president", "managing director")):
        return "general_management"
    if (
        any(term in text for term in ("事业部", "业务单元", "business unit"))
        or re.search(r"\b(?:head|vp|director)\s+of\s+.+\bbu\b", text)
    ):
        return "general_management"
    if any(
        term in text
        for term in (
            "\u5ba2\u6237",
            "\u9500\u552e",
            "\u5546\u52a1",
            "sales",
            "account",
            "customer",
        )
    ):
        return "sales_accounts"
    if any(
        term in text
        for term in (
            "\u4f9b\u5e94\u94fe",
            "\u91c7\u8d2d",
            "\u7269\u6599",
        )
    ):
        return "supply_chain"
    if any(
        term in text
        for term in (
            "\u5de5\u7a0b\u5316",
            "\u5de5\u827a",
            "\u8bbe\u5907",
            "\u5382\u52a1",
        )
    ):
        return "process_engineering"
    if any(
        term in text
        for term in (
            "\u5546\u4e1a\u5316",
            "\u4e1a\u52a1\u53d1\u5c55",
            "\u5e02\u573a\u62d3\u5c55",
            "\u589e\u957f",
            "growth excellence",
            "commercial excellence",
            "commercial activation",
            "go-to-market",
        )
    ):
        return "commercialization"
    scores = [
        (sum(1 for term in terms if _contains_term(text, term)), family)
        for family, terms in ROLE_FAMILIES
    ]
    score, family = max(scores, default=(0, "other"))
    return family if score else "other"


def role_family(title: str, description: str = "") -> str:
    title_family = _title_role_family(title)
    if title_family != "other":
        return title_family
    text = f"{title} {description}".casefold()
    # Specific operating objects beat generic modifiers such as "strategy".
    if any(term in text for term in ("客户", "销售", "商务")):
        return "sales_accounts"
    scores = [
        (sum(1 for term in terms if _contains_term(text, term)), family)
        for family, terms in ROLE_FAMILIES
    ]
    score, family = max(scores, default=(0, "other"))
    return family if score else "other"


def _families_match(
    predicted_title: str,
    predicted_description: str,
    job: HistoricalJob,
) -> bool:
    predicted_family = effective_role_family(
        predicted_title,
        predicted_description,
    )
    actual_family = effective_role_family(job.title, job.description)
    if predicted_family == actual_family:
        return True
    if {predicted_family, actual_family} == {
        "manufacturing",
        "process_engineering",
    }:
        return True
    return False


def _maximum_role_job_matching(
    hypotheses: list[Mapping[str, Any]],
    jobs: list[HistoricalJob],
) -> dict[int, int]:
    """Return a deterministic maximum-cardinality prediction/job matching."""

    edges: dict[int, list[int]] = {}
    for prediction_index, hypothesis in enumerate(hypotheses):
        title = str(hypothesis.get("specific_title") or "")
        description = " ".join(
            str(hypothesis.get(key) or "")
            for key in ("capability_gap", "mandate")
        )
        candidates = [
            job_index
            for job_index, job in enumerate(jobs)
            if _families_match(title, description, job)
        ]
        edges[prediction_index] = sorted(
            candidates,
            key=lambda job_index: (
                0
                if re.sub(r"\s+", "", title).casefold()
                == re.sub(r"\s+", "", jobs[job_index].title).casefold()
                else 1,
                parse_date(jobs[job_index].published_at) or date.max,
                job_index,
            ),
        )

    job_to_prediction: dict[int, int] = {}

    def augment(prediction_index: int, seen_jobs: set[int]) -> bool:
        for job_index in edges[prediction_index]:
            if job_index in seen_jobs:
                continue
            seen_jobs.add(job_index)
            incumbent = job_to_prediction.get(job_index)
            if incumbent is None or augment(incumbent, seen_jobs):
                job_to_prediction[job_index] = prediction_index
                return True
        return False

    for prediction_index in sorted(
        edges,
        key=lambda index: (len(edges[index]), index),
    ):
        augment(prediction_index, set())
    return {
        prediction_index: job_index
        for job_index, prediction_index in job_to_prediction.items()
    }


_GENERIC_ROLE_TITLE = re.compile(
    r"^(?:\u6280\u672f|\u7814\u53d1|\u5de5\u7a0b|\u4e1a\u52a1|\u8fd0\u8425)"
    r"(?:\u9ad8\u7ea7)?(?:\u526f\u603b\u88c1|\u603b\u7ecf\u7406|\u603b\u76d1|"
    r"\u8d1f\u8d23\u4eba|head|director)$",
    re.I,
)


def effective_role_family(title: str, description: str = "") -> str:
    compact = re.sub(r"[\s/_-]+", "", title)
    if _GENERIC_ROLE_TITLE.fullmatch(compact):
        return role_family("", description)
    return role_family(title, description)


def role_family_set(title: str, description: str = "") -> frozenset[str]:
    """Return symmetric ontology tags; no benchmark-specific title exceptions."""

    text = f"{title} {description}".casefold()
    families = {
        family
        for family, terms in ROLE_FAMILIES
        if any(_contains_term(text, term) for term in terms)
    }
    primary = role_family(title, description)
    if primary != "other":
        families.add(primary)
    return frozenset(families)


def canonical_role_key(title: str, description: str = "") -> str:
    family = effective_role_family(title, description)
    core = re.sub(
        r"(?:副总裁|总裁|总经理|总监|主管|负责人|vp|head|director|chief|ceo|cto|coo)",
        "",
        title.casefold(),
    )
    for pattern, replacement in (
        (
            r"\u5177\u8eab\u667a\u80fd\u673a\u5668\u4eba|"
            r"\u4eba\u5f62\u673a\u5668\u4eba|\u56db\u8db3\u673a\u5668\u4eba",
            "\u673a\u5668\u4eba",
        ),
        (r"\u6279\u91cf\u5316|\u89c4\u6a21\u5316|\u6279\u4ea7", "\u91cf\u4ea7"),
        (r"\u5546\u4e1a\u95ed\u73af", "\u5546\u4e1a\u5316"),
        (r"\u6218\u7565|\u9ad8\u7ea7|\u8d44\u6df1", ""),
    ):
        core = re.sub(pattern, replacement, core)
    core = re.sub(r"[\W_]+", "", core)
    return f"{family}:{core or 'generic'}"


def _actual_job_id(job: HistoricalJob) -> str:
    return _stable_hash(
        {
            "company": job.company,
            "canonical_role_key": canonical_role_key(job.title, job.description),
            "published_at": job.published_at,
        }
    )


def _historical_job_content_hash(job: HistoricalJob) -> str:
    return _stable_hash(
        {
            "company": job.company,
            "title": job.title,
            "description": job.description,
            "published_at": job.published_at,
            "observed_at": job.observed_at,
            "source_url": job.source_url,
            "source_name": job.source_name,
        }
    )


def _validate_snapshot_audit(prediction_snapshot: Mapping[str, Any]) -> None:
    manifest = prediction_snapshot.get("manifest") or {}
    packets = prediction_snapshot.get("prediction_packets")
    if not isinstance(packets, (list, tuple)):
        raise ValueError("acceptance snapshot must preserve prediction packets")
    if _stable_hash(packets) != manifest.get("prediction_packets_sha256"):
        raise ValueError("prediction packet hash mismatch")
    model_packets = prediction_snapshot.get("model_packets")
    if not isinstance(model_packets, (list, tuple)):
        raise ValueError("acceptance snapshot must preserve anonymized model packets")
    if _stable_hash(model_packets) != manifest.get("model_packets_sha256"):
        raise ValueError("model packet hash mismatch")
    if not str(manifest.get("prompt_version") or "").strip():
        raise ValueError("missing historical prompt version")
    runner = manifest.get("runner") or {}
    if not str(runner.get("provider") or "") or not str(runner.get("model") or ""):
        raise ValueError("snapshot must preserve provider and model identity")
    system_prompt = COMPANY_DEMAND_SYSTEM_PROMPT + "\n\n" + HISTORICAL_TEMPORAL_EMBARGO
    if manifest.get("system_prompt_sha256") != _stable_hash(system_prompt):
        raise ValueError("historical system prompt hash mismatch")
    packet_by_company = {str(packet.get("company") or ""): packet for packet in packets}
    model_packet_by_company = {
        str(packet.get("company") or ""): model_packet
        for packet, model_packet in zip(packets, model_packets, strict=True)
    }
    audits = prediction_snapshot.get("prompt_audit")
    if not isinstance(audits, list) or {
        str(item.get("company") or "") for item in audits
    } != set(packet_by_company):
        raise ValueError("prompt audit does not cover every prediction packet")
    for audit in audits:
        company = str(audit.get("company") or "")
        expected_user = (
            HISTORICAL_TEMPORAL_EMBARGO
            + "\n\n"
            + build_single_company_demand_prompt(
                model_packet_by_company[company],
                max_roles=int(manifest.get("prediction_max_roles_per_company") or 5),
            )
        )
        for field in ("system_prompt", "user_prompt", "response"):
            value = str(audit.get(field) or "")
            if audit.get(f"{field}_sha256") != _stable_hash(value):
                raise ValueError(f"prompt audit hash mismatch: {company}:{field}")
        if audit.get("system_prompt") != system_prompt:
            raise ValueError("prompt audit system prompt mismatch")
        if audit.get("user_prompt") != expected_user:
            raise ValueError("prompt audit user prompt does not match packet")
        if "repair_prompt" in audit or "repair_response" in audit:
            for field in ("repair_prompt", "repair_response"):
                value = str(audit.get(field) or "")
                if audit.get(f"{field}_sha256") != _stable_hash(value):
                    raise ValueError(f"prompt audit hash mismatch: {company}:{field}")

    if manifest.get("synthetic_test_snapshot") is True:
        return

    analyses_by_company = {
        str(item.get("company") or ""): item
        for item in prediction_snapshot.get("analyses") or ()
    }
    for audit in audits:
        company = str(audit.get("company") or "")
        final_response = str(
            audit.get("repair_response")
            if "repair_response" in audit
            else audit.get("response") or ""
        )
        try:
            reparsed = parse_single_company_demand(
                final_response,
                packet=model_packet_by_company[company],
            )
        except DemandAnalysisError:
            if company in analyses_by_company:
                raise ValueError(
                    f"stored analysis is not reproducible from response: {company}"
                )
            continue
        if company not in analyses_by_company:
            raise ValueError(
                f"parseable response is missing stored analysis: {company}"
            )
        reparsed = _cap_analysis_hypotheses(
            reparsed,
            int(manifest.get("prediction_max_roles_per_company") or 5),
        )
        reparsed = {**reparsed, "company": company}
        if _stable_hash(reparsed) != _stable_hash(analyses_by_company[company]):
            raise ValueError(f"stored analysis differs from response: {company}")


def validate_predictions(
    prediction_snapshot: Mapping[str, Any],
    jobs: Iterable[HistoricalJob],
) -> dict[str, Any]:
    manifest = prediction_snapshot.get("manifest") or {}
    if manifest.get("workforce_precursors_enabled") is not False:
        raise ValueError("acceptance snapshot must disable workforce precursors")
    if manifest.get("prediction_inputs_exclude_job_ads") is not True:
        raise ValueError(
            "acceptance snapshot must exclude job ads from prediction inputs"
        )
    if int(manifest.get("snapshot_schema_version") or 0) < 3:
        raise ValueError("acceptance snapshot lacks auditable provenance")
    _validate_snapshot_audit(prediction_snapshot)
    packets = prediction_snapshot["prediction_packets"]
    company_types = {
        str(packet.get("company") or ""): str(packet.get("company_type") or "")
        for packet in packets
    }
    if dict(prediction_snapshot.get("company_types") or {}) != company_types:
        raise ValueError("top-level company_types do not match frozen packets")
    analysis_companies = {
        str(analysis.get("company") or "")
        for analysis in prediction_snapshot.get("analyses") or ()
    }
    if not analysis_companies <= set(company_types):
        raise ValueError("analysis company is absent from frozen packets")
    cutoff = date.fromisoformat(str(manifest["cutoff"]))
    horizon_end = add_months(cutoff, int(manifest.get("horizon_months") or 3))
    all_jobs = list(jobs)
    for job in all_jobs:
        if job.content_sha256 and job.content_sha256 != _historical_job_content_hash(
            job
        ):
            raise ValueError(f"historical job content hash mismatch: {job.company}")
    eligible_jobs = [
        job
        for job in all_jobs
        if (published := parse_date(job.published_at)) is not None
        and cutoff <= published < horizon_end
        and classify_seniority(job.title, job.description)[1]
    ]
    matches: list[PredictionMatch] = []
    for analysis in prediction_snapshot.get("analyses") or ():
        company = str(analysis.get("company") or "")
        company_jobs = [job for job in eligible_jobs if job.company == company]
        hypotheses = list(
            analysis.get("hypotheses")
            or analysis.get("role_hypotheses")
            or ()
        )
        role_job_matching = _maximum_role_job_matching(
            hypotheses,
            company_jobs,
        )
        for hypothesis_index, hypothesis in enumerate(hypotheses):
            predicted_title = str(hypothesis.get("specific_title") or "")
            family = role_family(
                predicted_title,
                " ".join(
                    str(hypothesis.get(key) or "")
                    for key in ("capability_gap", "mandate")
                ),
            )
            predicted_description = " ".join(
                str(hypothesis.get(key) or "") for key in ("capability_gap", "mandate")
            )
            if hypothesis_index in role_job_matching:
                actual = company_jobs[role_job_matching[hypothesis_index]]
                published = parse_date(actual.published_at)
                exact = (
                    re.sub(r"\s+", "", predicted_title).casefold()
                    == re.sub(r"\s+", "", actual.title).casefold()
                )
                matches.append(
                    PredictionMatch(
                        company=company,
                        predicted_title=predicted_title,
                        predicted_family=family,
                        status="exact_match" if exact else "family_match",
                        actual_title=actual.title,
                        actual_url=actual.source_url,
                        actual_published_at=actual.published_at,
                        lead_days=(published - cutoff).days if published else None,
                        company_type=company_types[company],
                        actual_job_id=_actual_job_id(actual),
                        canonical_role_key=canonical_role_key(
                            predicted_title,
                            predicted_description,
                        ),
                    )
                )
            elif company_jobs:
                actual = min(
                    company_jobs,
                    key=lambda job: parse_date(job.published_at) or horizon_end,
                )
                matches.append(
                    PredictionMatch(
                        company=company,
                        predicted_title=predicted_title,
                        predicted_family=family,
                        status="company_only_match",
                        actual_title=actual.title,
                        actual_url=actual.source_url,
                        actual_published_at=actual.published_at,
                        company_type=company_types[company],
                        actual_job_id=_actual_job_id(actual),
                        canonical_role_key=canonical_role_key(
                            predicted_title,
                            predicted_description,
                        ),
                    )
                )
            else:
                matches.append(
                    PredictionMatch(
                        company=company,
                        predicted_title=predicted_title,
                        predicted_family=family,
                        status="not_observed",
                        company_type=company_types[company],
                        canonical_role_key=canonical_role_key(
                            predicted_title,
                            predicted_description,
                        ),
                    )
                )
    distinct_titles = sorted(
        {
            re.sub(r"\s+", "", match.predicted_title)
            for match in matches
            if match.predicted_title
        }
    )
    distinct_role_keys = sorted(
        {match.canonical_role_key for match in matches if match.canonical_role_key}
    )
    matched = [
        match for match in matches if match.status in {"exact_match", "family_match"}
    ]
    distinct_families = sorted(
        {
            match.predicted_family
            for match in matches
            if match.predicted_family and match.predicted_family != "other"
        }
    )
    verified_types = sorted(
        {
            str(company_types.get(match.company) or "")
            for match in matched
            if str(company_types.get(match.company) or "")
        }
    )
    companies_with_hypotheses = {
        str(analysis.get("company") or "")
        for analysis in prediction_snapshot.get("analyses") or ()
        if analysis.get("hypotheses") or analysis.get("role_hypotheses")
    }
    candidate_count = len(company_types)
    analyzed_company_count = len(analysis_companies)
    failed_company_count = len(prediction_snapshot.get("failures") or ())
    return {
        "manifest": {
            **dict(manifest),
            "validation_start": cutoff.isoformat(),
            "validation_end_exclusive": horizon_end.isoformat(),
            "validation_jobs_sha256": _stable_hash(
                [asdict(job) for job in eligible_jobs]
            ),
            "snapshot_audit_verified": True,
        },
        "validation_jobs": [asdict(job) for job in eligible_jobs],
        "counts": {
            "candidate_count": candidate_count,
            "analyzed_company_count": analyzed_company_count,
            "failed_company_count": failed_company_count,
            "companies_with_hypotheses": len(companies_with_hypotheses),
            "candidate_prediction_coverage": (
                len(companies_with_hypotheses) / candidate_count
                if candidate_count
                else 0.0
            ),
            "predictions": len(matches),
            "role_matches": len(matched),
            "company_only_matches": sum(
                match.status == "company_only_match" for match in matches
            ),
            "not_observed": sum(match.status == "not_observed" for match in matches),
            "distinct_predicted_titles": len(distinct_titles),
            "distinct_predicted_role_families": len(distinct_families),
            "distinct_canonical_role_keys": len(distinct_role_keys),
        },
        "distinct_predicted_titles": distinct_titles,
        "distinct_predicted_role_families": distinct_families,
        "distinct_canonical_role_keys": distinct_role_keys,
        "verified_company_types": verified_types,
        "matches": [asdict(match) for match in matches],
    }


def load_evidence(path: str | Path) -> list[Evidence]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    values = value.get("evidence") if isinstance(value, Mapping) else value
    if not isinstance(values, list):
        raise ValueError("evidence JSON must be a list or contain an evidence list")
    return [Evidence(**item) for item in values]


def load_jobs(path: str | Path) -> list[HistoricalJob]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    values = value.get("jobs") if isinstance(value, Mapping) else value
    if not isinstance(values, list):
        raise ValueError("jobs JSON must be a list or contain a jobs list")
    return [HistoricalJob.from_dict(item) for item in values]


def write_frozen_snapshot(value: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"frozen backtest snapshot already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


__all__ = [
    "BacktestConfig",
    "HistoricalJob",
    "PredictionMatch",
    "build_prediction_packets",
    "evidence_before_cutoff",
    "load_evidence",
    "load_jobs",
    "canonical_role_key",
    "effective_role_family",
    "role_family",
    "role_family_set",
    "run_historical_predictions",
    "validate_predictions",
    "write_frozen_snapshot",
]
