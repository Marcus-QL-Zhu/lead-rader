from __future__ import annotations

from dataclasses import dataclass


TARGET_TERMS = (
    '董事长', '首席执行官', '首席技术官', '首席运营官', '首席人力资源官',
    '总裁', '副总裁', '总经理', '事业部负责人', '事业部总经理', '基地负责人',
    '工厂负责人', '总监', 'director', 'head of', 'vice president', 'vp', 'cxo',
)

CONDITIONAL_TERMS = ('首席科学家', 'chief scientist', '总师', 'chief engineer', '负责人')

EXCLUDED_TERMS = (
    '高级经理', '资深经理', '经理', '主管', '高级专家', '资深专家', '专家',
    'principal', 'staff', 'fellow', 'individual contributor',
)

SCOPE_TERMS = (
    '团队搭建', '组建团队', '团队管理', '预算', 'p&l', '损益', '招聘决策',
    '绩效管理', '跨部门', '向ceo汇报', '向cto汇报', '向总经理汇报', '全面负责',
)


@dataclass(frozen=True)
class DirectionProfile:
    name: str
    aliases: tuple[str, ...]
    discovery_terms: tuple[str, ...]
    role_by_event: dict[str, tuple[str, ...]]
    seed_companies: tuple[str, ...] = ()


DEXTEROUS_HAND = DirectionProfile(
    name='灵巧手',
    aliases=('灵巧手', '仿生手', '机器人手', '触觉灵巧操作', 'dexterous hand'),
    discovery_terms=(
        '融资', '增资', '量产', '扩产', '工厂', '基地', '订单', '交付', '中标',
        '战略合作', '设备入场', '产线', '投资计划', '技术里程碑', '海外市场',
    ),
    role_by_event={
        'factory_or_capacity': ('量产总监', '制造运营总监', '供应链总监'),
        'major_order': ('交付总监', '供应链总监', '质量总监'),
        'funding': ('研发总监', '产品总监', '战略与组织发展总监'),
        'technical_milestone': ('灵巧手研发总监', '触觉感知研发总监', '产品总监'),
        'data_or_model': ('具身数据平台主管', '机器人算法总监'),
        'global_expansion': ('海外业务总监', '全球交付总监'),
        'executive_change': ('研发总监', '制造运营总监'),
        'partnership': ('产业合作总监', '大客户总监'),
    },
    seed_companies=(
        '灵心巧手', '因时机器人', '戴盟机器人', '傲意科技',
        '灵巧智能', '舞肌科技', '钛虎机器人', '帕西尼感知科技',
    ),
)


GENERIC = DirectionProfile(
    name='generic',
    aliases=(),
    discovery_terms=('融资', '订单', '工厂', '基地', '扩产', '量产', '战略合作', '中标'),
    role_by_event={
        'factory_or_capacity': ('制造运营总监', '供应链总监'),
        'major_order': ('交付总监', '大客户总监'),
        'funding': ('研发总监', '产品总监'),
        'technical_milestone': ('研发总监', '产品总监'),
        'data_or_model': ('数据平台主管', '算法总监'),
        'global_expansion': ('海外业务总监',),
        'executive_change': ('事业部总监',),
        'partnership': ('产业合作总监',),
    },
)


def profile_for(direction: str) -> DirectionProfile:
    normalized = direction.strip().lower()
    if any(alias.lower() in normalized or normalized in alias.lower() for alias in DEXTEROUS_HAND.aliases):
        return DEXTEROUS_HAND
    return DirectionProfile(
        name=direction.strip(),
        aliases=(direction.strip(),),
        discovery_terms=GENERIC.discovery_terms,
        role_by_event=GENERIC.role_by_event,
        seed_companies=(),
    )


def classify_seniority(title: str, description: str = '') -> tuple[str, bool, list[str]]:
    text = f'{title} {description}'.lower()
    matched_scope = [term for term in SCOPE_TERMS if term in text]
    if any(term in text for term in TARGET_TERMS):
        return 'director_plus', True, matched_scope
    if any(term in text for term in CONDITIONAL_TERMS) and len(matched_scope) >= 2:
        return 'director_plus', True, matched_scope
    if any(term in text for term in EXCLUDED_TERMS):
        return 'below_director_or_ic', False, matched_scope
    return 'unknown', False, matched_scope


def target_roles(profile: DirectionProfile, event_types: set[str], limit: int = 3) -> list[str]:
    roles: list[str] = []
    active_events = [event_type for event_type in profile.role_by_event if event_type in event_types]
    max_roles = max((len(profile.role_by_event[event_type]) for event_type in active_events), default=0)
    for role_index in range(max_roles):
        for event_type in active_events:
            event_roles = profile.role_by_event[event_type]
            if role_index < len(event_roles) and event_roles[role_index] not in roles:
                roles.append(event_roles[role_index])
            if len(roles) >= limit:
                return roles
    return roles[:limit]
