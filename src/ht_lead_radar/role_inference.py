"""Industry-aware Director-plus role hypotheses."""

from __future__ import annotations

from collections.abc import Iterable

from .models import CompanyLead


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
    "project_approval": ("项目建设总监", "政府事务总监", "运营总监"),
    "land_or_environment": ("项目建设总监", "EHS总监", "运营总监"),
    "regulatory_approval": ("法规合规总监", "质量总监", "商业化总监"),
    "industrial_fund": ("战略与投资总监", "政府事务总监", "业务发展总监"),
    "clinical_milestone": ("临床运营总监", "医学事务总监", "注册法规总监"),
}


def roles_for(direction: str, event_types: Iterable[str], limit: int = 3) -> list[str]:
    lowered = direction.casefold()
    event_set = set(event_types)
    role_map = dict(GENERIC_ROLES)
    for aliases, mapping in ROLE_MAPS:
        if any(alias.casefold() in lowered for alias in aliases):
            role_map.update(mapping)
            break
    roles: list[str] = []
    for event_type in event_set:
        for role in role_map.get(event_type, ()):
            if role not in roles:
                roles.append(role)
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
        inferred = roles_for(direction, event_types)
        if inferred:
            lead.target_roles = inferred
            role_text = "、".join(inferred)
            lead.hiring_thesis = (
                f"{lead.hiring_thesis.rstrip('。')}；结合{direction}行业能力链，"
                f"优先验证的总监级以上岗位为：{role_text}。"
            )
    return leads


__all__ = ["GENERIC_ROLES", "ROLE_MAPS", "enrich_industry_roles", "roles_for"]
