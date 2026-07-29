"""Canonical early-hiring signal taxonomy shared by collection and inference."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class SignalDefinition:
    name: str
    phase: str
    pattern: re.Pattern[str]
    aliases: tuple[str, ...] = ()
    workforce_precursor: bool = False


def _rx(value: str) -> re.Pattern[str]:
    return re.compile(value, re.I)


SIGNALS: tuple[SignalDefinition, ...] = (
    SignalDefinition(
        "executive_change",
        "build_organize",
        _rx(
            r"任命|履新|接任|升任|新任|换帅|人事调整|"
            r"(?:加盟|加入).{0,18}(?:董事长|总裁|副总裁|总经理|CEO|CTO|COO|"
            r"首席|董事|总监)|"
            r"appointed|joins? as|named .{0,30}(?:ceo|cto|coo|president|"
            r"general manager|vice president|managing director)"
        ),
        ("leadership",),
    ),
    SignalDefinition(
        "merger_acquisition",
        "strategy_capital",
        _rx(r"并购|收购|控股权|控制权变更|要约收购|acquisition|acquires?|merger"),
        ("m_and_a", "change_of_control"),
    ),
    SignalDefinition(
        "joint_venture_or_spinout",
        "build_organize",
        _rx(r"合资公司|成立合资|分拆|拆分|独立运营|spin[- ]?off|joint venture"),
        ("joint_venture", "spinout"),
    ),
    SignalDefinition(
        "ipo_or_listing",
        "strategy_capital",
        _rx(r"上市辅导|辅导备案|递交招股书|申报上市|启动上市|ipo|initial public offering"),
        ("listing_preparation",),
    ),
    SignalDefinition(
        "new_site_or_entity",
        "build_organize",
        _rx(r"区域总部|新设子公司|成立子公司|新事业部|第二总部|研发中心|运营中心|落户"),
        ("new_subsidiary", "regional_hq", "new_business_unit"),
    ),
    SignalDefinition(
        "factory_or_capacity",
        "strategy_capital",
        _rx(r"工厂|基地|产线|扩产|产能|设备入场|量产|投产|制造中心"),
        ("capacity_expansion", "mass_production"),
    ),
    SignalDefinition(
        "project_buildout",
        "strategy_capital",
        _rx(r"项目启动|项目开工|建设项目|项目落地|竣工|投运|装置建成"),
        ("project_approval",),
    ),
    SignalDefinition(
        "project_call",
        "build_organize",
        _rx(r"项目征集|揭榜挂帅|场景征集|供应商征集|申报通知|项目申报|project call"),
        ("pilot_program", "supplier_call"),
    ),
    SignalDefinition(
        "eia_or_permit",
        "strategy_capital",
        _rx(r"环境影响|环评|拟审查|建设项目受理|用地获批|施工许可|批复"),
        ("land_or_environment",),
    ),
    SignalDefinition(
        "procurement_intention",
        "strategy_capital",
        _rx(r"采购意向|拟采购|预算金额|采购预算|计划采购"),
    ),
    SignalDefinition(
        "procurement_tender",
        "strategy_capital",
        _rx(r"公开招标|招标公告|采购公告|竞争性磋商|竞争性谈判"),
    ),
    SignalDefinition(
        "major_order",
        "strategy_capital",
        _rx(r"重大订单|亿元订单|中标|定点|供应商|框架合同|采购合同|批量订单"),
        ("award_or_supplier", "contract_award"),
    ),
    SignalDefinition(
        "customer_validation",
        "build_organize",
        _rx(r"客户验证|通过验证|供应商认证|准入|首批交付|批量交付|复购"),
        ("product_validation",),
    ),
    SignalDefinition(
        "funding",
        "strategy_capital",
        _rx(
            r"融资|增资|战略投资|入股|募资|funding|raises?|raised|"
            r"venture round|seed round|series [a-f]"
        ),
        ("financing", "industrial_fund"),
    ),
    SignalDefinition(
        "global_expansion",
        "build_organize",
        _rx(r"出海|海外市场|全球市场|国际化|进入.{0,20}市场|海外子公司|海外基地"),
        ("market_expansion",),
    ),
    SignalDefinition(
        "channel_expansion",
        "build_organize",
        _rx(r"渠道体系|经销商|代理商|渠道伙伴|全国渠道|销售网络|服务网络"),
    ),
    SignalDefinition(
        "technical_milestone",
        "build_organize",
        _rx(r"发布|首发|亮相|展示|技术突破|试验成功|样机|迭代|首飞|点火|试车|流片成功|验证成功"),
        ("product_launch", "technology_milestone"),
    ),
    SignalDefinition(
        "data_or_model",
        "build_organize",
        _rx(r"数据集|数据采集|大模型|vla|vtla|技能库|训练平台|数据平台"),
    ),
    SignalDefinition(
        "regulatory_or_clinical",
        "build_organize",
        _rx(r"获批|注册证|临床试验|临床研究|受理|审评|审批|适航"),
        ("regulatory_approval", "clinical_milestone"),
    ),
    SignalDefinition(
        "research_or_ip",
        "build_organize",
        _rx(r"联合实验室|研究院|联合研究中心|专利授权|技术许可|知识产权|首席科学家"),
        ("research_program", "technology_asset"),
    ),
    SignalDefinition(
        "enterprise_system",
        "build_organize",
        _rx(r"\bERP\b|\bMES\b|\bPLM\b|\bCRM\b|数字化转型|信息化升级|系统上线"),
        ("digital_transformation",),
    ),
    SignalDefinition(
        "partnership",
        "build_organize",
        _rx(r"战略合作|生态伙伴|合作协议|联合解决方案|签署合作"),
    ),
    SignalDefinition(
        "policy_or_standard",
        "build_organize",
        _rx(r"牵头.{0,20}标准|参与制定.{0,20}标准|入选.{0,20}试点|示范名单"),
    ),
    SignalDefinition(
        "workforce_cluster",
        "build_organize",
        _rx(
            r"(?:经理|专家|工程师).{0,20}(?:招聘|岗位|职位)|"
            r"(?:招聘|岗位|职位).{0,20}(?:经理|专家|工程师)"
        ),
        workforce_precursor=True,
    ),
    SignalDefinition(
        "job_ad",
        "recruit",
        _rx(r"招聘|职位|岗位|加入我们"),
    ),
)


_BY_NAME = {
    name: definition
    for definition in SIGNALS
    for name in (definition.name, *definition.aliases)
}

_TERMINATION_PATTERN = re.compile(
    r"\u5173\u95ed|\u524a\u51cf|\u7ec8\u6b62|\u64a4\u56de|\u64a4\u9500|"
    r"\u53d6\u6d88|\u6682\u505c|\u505c\u4ea7|\u6401\u7f6e|\u89e3\u6563|"
    r"\u7834\u4ea7|\u88c1\u5458|shutdown|cancel(?:led|ed)?|withdrawn?|"
    r"terminated?|layoffs?",
    re.I,
)


def canonical_event_type(value: str) -> str:
    definition = _BY_NAME.get(str(value or "").strip())
    return definition.name if definition else str(value or "").strip()


def infer_signal(
    text: str,
    *,
    include_workforce_precursors: bool = True,
) -> tuple[str, str]:
    if _TERMINATION_PATTERN.search(text or ""):
        return "other", "ignore"
    # Recruiting text is provenance, not an operating event. Classify it before
    # factory/order/etc. so a mixed sentence cannot smuggle a job ad into replay.
    for signal_name in ("workforce_cluster", "job_ad"):
        signal = _BY_NAME[signal_name]
        if signal.workforce_precursor and not include_workforce_precursors:
            continue
        if signal.pattern.search(text or ""):
            return signal.name, signal.phase
    for signal in SIGNALS:
        if signal.name in {"workforce_cluster", "job_ad"}:
            continue
        if signal.workforce_precursor and not include_workforce_precursors:
            continue
        if signal.pattern.search(text or ""):
            return signal.name, signal.phase
    return "other", "build_organize"


def prediction_signal_types(
    *,
    include_workforce_precursors: bool = True,
) -> frozenset[str]:
    return frozenset(
        signal.name
        for signal in SIGNALS
        if signal.name != "job_ad"
        and (include_workforce_precursors or not signal.workforce_precursor)
    )


def canonicalize_event_types(values: Iterable[str]) -> set[str]:
    return {canonical_event_type(value) for value in values}


__all__ = [
    "SIGNALS",
    "SignalDefinition",
    "canonical_event_type",
    "canonicalize_event_types",
    "infer_signal",
    "prediction_signal_types",
]
