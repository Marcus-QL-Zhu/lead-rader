"""Industry-aware Director-plus role hypotheses."""

from __future__ import annotations

from collections.abc import Iterable

from .models import CompanyLead
from .signals import canonicalize_event_types


ROLE_MAPS: tuple[tuple[tuple[str, ...], dict[str, tuple[str, ...]]], ...] = (
    (
        ("脑机接口", "神经接口", "bci"),
        {
            "funding": ("脑机接口研发总监", "临床与医学事务总监", "注册法规总监"),
            "clinical_milestone": ("临床运营总监", "医学事务总监", "注册法规总监"),
            "regulatory_approval": ("注册法规总监", "质量体系总监", "商业化总监"),
            "technical_milestone": ("神经工程研发总监", "算法总监", "产品总监"),
            "data_or_model": ("神经数据平台主管", "脑电算法总监", "数据合规总监"),
            "factory_or_capacity": ("制造运营总监", "质量总监", "供应链总监"),
            "partnership": ("临床合作总监", "产业合作总监", "产品总监"),
        },
    ),
    (
        ("半导体", "芯片", "集成电路"),
        {
            "funding": ("研发总监", "产品线总经理", "战略与投资总监"),
            "factory_or_capacity": ("工厂运营总监", "工艺整合总监", "供应链总监"),
            "land_or_environment": ("厂务总监", "EHS总监", "项目建设总监"),
            "major_order": ("大客户总监", "交付总监", "质量总监"),
            "technical_milestone": ("芯片研发总监", "工程平台主管", "产品总监"),
            "global_expansion": ("海外业务总监", "全球供应链总监", "区域总经理"),
            "partnership": ("生态合作总监", "产品线总经理", "大客户总监"),
        },
    ),
    (
        ("商业航天", "民营航天", "火箭", "卫星"),
        {
            "funding": ("型号研发总监", "商业化总监", "战略与组织总监"),
            "technical_milestone": ("型号总师", "试验与验证总监", "质量总监"),
            "regulatory_approval": ("适航与合规总监", "质量总监", "项目总监"),
            "factory_or_capacity": ("总装制造总监", "供应链总监", "基地运营总监"),
            "major_order": ("项目交付总监", "政府与大客户总监", "供应链总监"),
            "project_approval": ("项目建设总监", "基地运营总监", "政府事务总监"),
            "partnership": ("产业合作总监", "商业化总监", "项目总监"),
        },
    ),
    (
        ("核聚变", "聚变能源", "可控核聚变"),
        {
            "funding": ("聚变工程研发总监", "工程项目总监", "供应链总监"),
            "technical_milestone": ("等离子体研发总监", "磁体工程总监", "总师"),
            "factory_or_capacity": ("装置工程总监", "制造与装配总监", "质量总监"),
            "major_order": ("工程交付总监", "采购供应链总监", "质量总监"),
            "project_approval": ("工程项目总监", "政府事务总监", "EHS总监"),
            "partnership": ("科研合作总监", "产业合作总监", "工程项目总监"),
        },
    ),
    (
        ("具身智能", "人形机器人", "灵巧手", "机器人"),
        {
            "funding": ("机器人研发总监", "产品总监", "战略与组织总监"),
            "data_or_model": ("具身数据平台主管", "机器人算法总监", "数据采集总监"),
            "technical_milestone": ("机器人研发总监", "算法总监", "产品总监"),
            "factory_or_capacity": ("量产总监", "制造运营总监", "供应链总监"),
            "major_order": ("交付总监", "质量总监", "大客户总监"),
            "partnership": ("产业合作总监", "解决方案总监", "大客户总监"),
        },
    ),
)

GENERIC_ROLES = {
    "executive_change": (
        "战略转型总监", "组织与人才发展总监", "业务运营总监",
    ),
    "merger_acquisition": (
        "并购整合总监", "企业发展总监", "财务整合总监",
    ),
    "joint_venture_or_spinout": (
        "合资公司总经理", "新业务运营总监", "战略合作总监",
    ),
    "ipo_or_listing": (
        "董事会秘书", "资本市场总监", "内控与审计总监",
    ),
    "new_site_or_entity": (
        "区域总经理", "新基地运营总监", "区域人力资源总监",
    ),
    "project_buildout": (
        "项目建设总监", "基地运营总监", "工程管理总监",
    ),
    "project_call": (
        "项目申报与产业合作总监", "解决方案总监", "政府事务总监",
    ),
    "eia_or_permit": (
        "项目建设总监", "EHS总监", "厂务总监",
    ),
    "procurement_intention": (
        "采购平台主管", "项目建设总监", "供应链规划总监",
    ),
    "procurement_tender": (
        "政府大客户总监", "投标平台主管", "解决方案总监",
    ),
    "customer_validation": (
        "客户质量总监", "应用工程总监", "客户成功总监",
    ),
    "channel_expansion": (
        "渠道销售总监", "生态合作总监", "售后服务总监",
    ),
    "regulatory_or_clinical": (
        "注册法规总监", "临床运营总监", "医学事务总监",
    ),
    "research_or_ip": (
        "科研合作总监", "知识产权总监", "技术战略总监",
    ),
    "enterprise_system": (
        "数字化转型总监", "企业信息化总监", "业务流程平台主管",
    ),
    "policy_or_standard": (
        "政府事务总监", "标准与认证总监", "产业政策总监",
    ),
    "workforce_cluster": (
        "组织与人才发展总监", "业务部门总监", "人力资源总监",
    ),
    # Read-time aliases for historical facts.
    "project_approval": ("项目建设总监", "政府事务总监", "运营总监"),
    "land_or_environment": ("项目建设总监", "EHS总监", "运营总监"),
    "regulatory_approval": ("法规合规总监", "质量总监", "商业化总监"),
    "industrial_fund": ("战略与投资总监", "政府事务总监", "业务发展总监"),
    "clinical_milestone": ("临床运营总监", "医学事务总监", "注册法规总监"),
}


ARCHETYPE_ROLES = {
    "startup_private": {
        "funding": ("商业化副总裁", "产品与战略总监", "财务总监"),
        "executive_change": ("CEO办公室主任", "组织发展总监", "业务运营总监"),
    },
    "listed": {
        "executive_change": ("战略转型总监", "组织与人才发展总监", "经营管理总监"),
        "merger_acquisition": ("并购整合总监", "集团财务管控总监", "投后管理总监"),
    },
    "foreign": {
        "executive_change": ("中国区战略总监", "中国区人力资源总监", "中国区业务运营总监"),
        "global_expansion": ("中国区业务拓展总监", "本地化供应链总监", "中国区产品营销总监"),
    },
}


def infer_company_archetype(context: str) -> str:
    lowered = context.casefold()
    if any(
        term in lowered
        for term in (
            "外企", "跨国公司", "中国区", "大中华区", "global", "china president",
            "中国有限公司",
        )
    ):
        return "foreign"
    if any(
        term in lowered
        for term in (
            "上市公司", "证券代码", "上交所", "深交所", "港交所", "科创板",
            "创业板", "annual report",
        )
    ):
        return "listed"
    return "startup_private"


def roles_for(
    direction: str,
    event_types: Iterable[str],
    limit: int = 3,
    *,
    company_context: str = "",
) -> list[str]:
    if limit <= 0:
        return []
    lowered = direction.casefold()
    role_map = dict(GENERIC_ROLES)
    sector_event_types: set[str] = set()
    for aliases, mapping in ROLE_MAPS:
        if any(alias.casefold() in lowered for alias in aliases):
            role_map.update(mapping)
            sector_event_types = set(mapping)
            break
    raw_event_types = set(event_types)
    event_set = {
        value if value in role_map else next(iter(canonicalize_event_types((value,))))
        for value in raw_event_types
    }
    archetype = infer_company_archetype(company_context)
    for event_type, roles in ARCHETYPE_ROLES[archetype].items():
        if event_type in event_set and event_type not in sector_event_types:
            role_map[event_type] = roles
    active_events = [
        event_type for event_type in role_map if event_type in event_set
    ]
    roles: list[str] = []
    max_roles = max(
        (len(role_map[event_type]) for event_type in active_events),
        default=0,
    )
    # Interleave event types in declared map order. Iterating the caller's set
    # made role order vary with PYTHONHASHSEED and let one event monopolise all
    # three hypotheses.
    for role_index in range(max_roles):
        for event_type in active_events:
            event_roles = role_map[event_type]
            if (
                role_index < len(event_roles)
                and event_roles[role_index] not in roles
            ):
                roles.append(event_roles[role_index])
            if len(roles) >= limit:
                return roles
    return roles


def enrich_industry_roles(leads: list[CompanyLead], direction: str) -> list[CompanyLead]:
    for lead in leads:
        event_types = {
            item.event_type
            for item in lead.evidence
            if item.event_type != "job_ad"
        }
        context = " ".join(
            f"{item.title} {item.snippet}"
            for item in lead.evidence
            if item.event_type != "job_ad"
        )
        inferred = roles_for(
            direction,
            event_types,
            company_context=context,
        )
        if inferred:
            lead.target_roles = inferred
            role_text = "、".join(inferred)
            lead.hiring_thesis = (
                f"{lead.hiring_thesis.rstrip('。')}；结合{direction}行业能力链，"
                f"优先验证的总监级以上岗位为：{role_text}。"
            )
    return leads


__all__ = [
    "ARCHETYPE_ROLES",
    "GENERIC_ROLES",
    "ROLE_MAPS",
    "enrich_industry_roles",
    "infer_company_archetype",
    "roles_for",
]
