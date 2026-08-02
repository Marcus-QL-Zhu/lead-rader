"""Deterministic action spans and atomic claims for semantic adjudication."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha1
import re
from typing import Any, Iterable, Mapping, Sequence

from .document_router import DocumentRoute, DocumentUnit, route_document
from .entity_ledger import ArticleEntityLedger
from .models import CleanArticle


ACTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "funding": re.compile(
        r"(?:完成|获得|宣布完成|启动|开启|开始|筹集|募集|募资|洽谈|拟|计划|将)"
        r"[^。！？；\n]{0,100}(?:融资|Pre[ -]?(?:IPO|[A-H](?:\+{1,2})?)\s*轮|"
        r"[A-H](?:\+{1,2})?\s*轮|天使(?:\s*\+{1,2})?\s*轮|种子\s*轮)|"
        r"(?:落地|官宣)[^。！？；\n]{0,80}(?:融资|募资)|"
        r"(?:非公开发行|定向增发|定增|发行)"
        r"[^。！？；\n]{0,100}(?:募资|募集资金|公司债券)|"
        r"融资[^。！？；\n]{0,40}(?:接近|即将|已经)?完成|"
        r"(?:向|对)[^。！？；\n]{2,60}投资(?:[0-9一二三四五六七八九十百千万亿]|美元|人民币)|"
        r"完成(?:了)?一笔[^。！？；\n]{0,30}投资|"
        r"(?:基金)?[^。！？；\n]{0,80}(?:完成|宣布完成)(?:首关|终关|募集)|"
        r"(?:拟|将)?以自有资金[^。！？；\n]{0,80}(?:认缴出资|参与投资)|"
        r"(?:共同投资|出资)[^。！？；\n]{0,80}(?:基金|合伙企业)|"
        r"累计融资(?:金额|总额)?[^。！？；\n]{0,30}(?:超过|突破|达到)|"
        r"(?:启动|开启)[^。！？；\n]{0,30}(?:新一轮|下一轮)?融资|"
        r"刷新[^。！？；\n]{0,50}融资(?:纪录|记录)",
        re.I,
    ),
    "executive_change": re.compile(
        r"退出这家[^。！？；\n]{0,100}(?:公司|实验室)|"
        r"(?:任命|聘任|委派|推荐|免去|不再担任|出任|担任|加入|离任|辞任|升任|接任|换帅)"
        r"[^。！？；\n]{0,80}(?:董事长|总裁|CEO|首席|总经理|负责人|"
        r"一号位|副总裁|VP|总监)|"
        r"(?:重返|退出|离开)[^。！？；\n]{0,60}?(?:公司|集团|OpenAI|实验室)",
        re.I,
    ),
    "ipo_or_listing": re.compile(
        r"递表|提交上市申请|启动IPO|港股IPO|完成上市|正式上市|"
        r"(?:新三板|北交所|交易所|港交所|证券交易所)[^。！？；\n]{0,10}挂牌|"
        r"挂牌(?:上市|交易)|"
        r"新股申购|"
        r"实施[^。！？；\n]{0,40}风险警示|撤销[^。！？；\n]{0,40}风险警示|"
        r"终止上市|退市",
        re.I,
    ),
    "major_order": re.compile(
        r"(?:中标|签订|签署|获得|斩获|获(?!悉))[^。！？；\n]{0,100}"
        r"(?:订单|合同|采购项目|项目定点|供应定点)|"
        r"(?:订单|合同)[^。！？；\n]{0,60}(?:签订|签署|落地)|"
        r"(?:新增|获得|拿下|签下)[^。！？；\n]{0,60}(?:订单|合同)|"
        r"(?:订单|合同)[^。！？；\n]{0,60}(?:新增|增长)|"
        r"承诺[^。！？；\n]{0,80}(?:未来支出|投资)",
        re.I,
    ),
    "factory_or_capacity": re.compile(
        r"(?:开工|投产(?!融)|扩产|扩建|建设|建成|落地|投建|追加投资)"
        r"[^。！？；\n]{0,100}(?:工厂|产线|基地|产能|制造|项目)|"
        r"(?:工厂|产线|基地|产能(?!力)|产量)[^。！？；\n]{0,100}"
        r"(?:开工|投产(?!融)|扩产|扩建|建设|建成|落地|提升|扩大)|"
        r"规模化量产|万台[^。！？；\n]{0,30}(?:产能|交付)|"
        r"(?:建有|建设了|已建成|总体完成|实现交付)[^。！？；\n]{0,100}"
        r"(?:生产基地|数据中心|工厂|车间|产线|项目)|"
        r"(?:生产基地|数据中心|工厂|车间|产线|项目)[^。！？；\n]{0,80}"
        r"(?:建成|交付|并网|投运|投建|扩充|扩大)|"
        r"(?:扩充|拓展|扩大)[^。！？；\n]{0,80}(?:产能|规模化交付能力|生产能力)|"
        r"(?:加快|推进)?[^。！？；\n]{0,30}(?:产能|生产能力)"
        r"[^。！？；\n]{0,50}(?:拓展|扩充|提升)|"
        r"承诺[^。！？；\n]{0,80}投资[^。！？；\n]{0,100}"
        r"(?:制造|产能|工厂|产线|布局|根基)",
        re.I,
    ),
    # Explicit investment-use clauses are early hiring signals. Keep them as
    # separate host-locked claims instead of relying on generic open actions.
    "workforce_cluster": re.compile(
        r"(?:核心研发团队扩充|研发团队扩充|团队扩充|高端人才引进|人才引进|"
        r"人才招聘|招聘人才|招募人才)",
        re.I,
    ),
    "research_or_ip": re.compile(
        r"(?:基础模型持续迭代|技术研发|数据闭环建设|工程化验证|协同研发|"
        r"共性技术研发|研发投入|研发平台建设)",
        re.I,
    ),
    "project_buildout": re.compile(
        r"(?:产业化平台建设|基础实验平台建设|中试生产平台建设|"
        r"产业创新平台申报|平台申报|产业平台建设)",
        re.I,
    ),
    "global_expansion": re.compile(
        r"(?:全球化落地|全球市场拓展|海外市场拓展|出海布局|海外业务布局)",
        re.I,
    ),
    "partnership": re.compile(
        r"(?:达成|签署|签订|建立|开展|深化)[^。！？；\n]{0,80}"
        r"(?:战略合作|合作协议|合作备忘录|长期合作|合资)|"
        r"与[^。！？；\n]{2,80}(?:开展|达成|签署|签订|建立|深化)"
        r"[^。！？；\n]{0,50}合作|"
        r"(?:为其|向其)[^。！？；\n]{0,100}(?:提供担保|提供支持|芯片支持)|"
        r"(?:会面|会晤)[^。！？；\n]{0,100}(?:磋商|商谈|洽谈)[^。！？；\n]{0,80}合作|"
        r"(?:磋商|商谈|洽谈)[^。！？；\n]{0,80}合作(?:方案|事宜)?|"
        r"(?:联合|携手)[^。！？；\n]{1,100}(?:发布|共建|打造|攻关|研发)|"
        r"(?:共同|联合)[^。！？；\n]{1,80}(?:投资|主办)|"
        r"(?:担任|聘请)[^。！？；\n]{0,40}(?:独家)?财务顾问|"
        r"推进[^。！？；\n]{0,60}(?:研发|临床研究)?合作项目|"
        r"由[^。！？；\n]{2,80}主办[^。！？；\n]{0,80}(?:联合|共同)主办|"
        r"(?:主办|承办)[^。！？；\n]{0,80}(?:大会|会议|活动)",
        re.I,
    ),
    "technical_milestone": re.compile(
        r"(?:发布|推出|首发|量产|交付|获批|上线公测|正式开源|开源|"
        r"搭建完成|研制成功|正式上线)[^，,。！？；\n]{0,120}"
        r"(?:芯片|机器人|模型|产品|平台|设备|系统|药物|卫星|火箭|"
        r"技术|API|数据矿|决策大脑|赛道|专项|专题|挑战赛)|"
        r"(?:芯片|机器人|模型|产品|平台|设备|系统|药物|卫星|火箭|"
        r"技术|API|数据矿|决策大脑)[^，,。！？；\n]{0,120}"
        r"(?:发布|推出|首发|量产|交付|下线|获批|上线公测|正式开源|"
        r"开源|搭建完成|研制成功|正式上线|全新上线)|"
        r"(?:发布|推出|升级上线|全新上线|正式上线|上线|开启内测|"
        r"开始(?:小范围)?测试|正在训练|接入)[^，,。！？；\n]{1,120}"
        r"(?:功能|能力|工作台|智能体|助手|Agent|模式|服务|客户端|"
        r"模型|平台|系统|API|基础设施|产品)|"
        r"完成[^。！？；\n]{0,60}(?:基础设施|平台|系统)[^。！？；\n]{0,30}重构|"
        r"(?:封禁|阻断)[^。！？；\n]{0,80}(?:账号|攻击|威胁)|"
        r"正在开发[^。！？；\n]{0,120}(?:产品|技术|芯片|模型)|"
        r"(?:计划|拟|将)[^。！？；\n]{0,80}(?:开启|启动)"
        r"[^。！？；\n]{0,40}(?:适配|验证|测试)|"
        r"(?:发布|推出)\s*(?:开源版|新版|新一代)?"
        r"[A-Z][A-Za-z0-9 ._+/-]{1,48}|"
        r"上线(?:App|APP|PC端|网页端|客户端|服务)|"
        r"开启内测|"
        r"接入[^，,。！？；\n]{1,80}(?:API|模型|平台|系统|服务)|"
        r"(?:API服务|模型|平台|系统)[^，,。！？；\n]{0,60}接入|"
        r"开始(?:小范围)?测试|"
        r"正在训练\s*[A-Z][A-Za-z0-9 ._+/-]{1,48}|"
        r"(?:计划|拟|将|预计)[^，,。！？；\n]{0,50}(?:发布|推出)"
        r"\s*[A-Z][A-Za-z0-9 ._+/-]{1,48}|"
        r"[A-Z][A-Za-z0-9 ._+/-]{1,48}(?:将|预计|计划)"
        r"[^，,。！？；\n]{0,50}(?:发布|推出)|"
        r"(?:建成|搭建|形成)[^。！？；\n]{0,100}(?:技术平台|数据平台|产品体系|产品矩阵)|"
        r"(?:首次展示|首次亮相|亮相|首次[^。！？；\n]{0,40}展示)"
        r"[^。！？；\n]{0,100}(?:产品|设备|系统|模型|机器人|芯片|扫描仪)|"
        r"(?:产品|设备|系统|模型|机器人|芯片|数据手套)"
        r"[^。！？；\n]{0,100}(?:首次展示|首次亮相|亮相)|"
        r"(?:完成|实现)[^。！？；\n]{0,80}(?:技术迭代|工艺验证|概念验证|技术突破)|"
        r"(?:牵头|承担)[^。！？；\n]{0,80}(?:科研项目|攻关项目)|"
        r"(?:发布|推出)[^。！？；\n]{0,100}(?:产品体系|产品矩阵)|"
        r"(?:即将|计划|拟|将)[^。！？；\n]{0,24}发布的?"
        r"[^。！？；\n]{0,100}(?:产品|设备|系统|模型|机器人|芯片|数据手套)|"
        r"(?:将|预计)[^。！？；\n]{0,60}亮相|"
        r"(?:持续)?迭代升级[^。！？；\n]{0,120}(?:平台|系统)|"
        r"即将实现[^。！？；\n]{0,50}(?:产业化上市|商业化)",
        re.I,
    ),
    "new_site_or_entity": re.compile(
        r"(?:成立|设立|注册|落地|启用|租赁)[^。！？；\n]{0,100}"
        r"(?:公司|子公司|中心|基地|实验室|研究院|园区)|"
        r"(?:公司|子公司|中心|基地|实验室|研究院|园区|总部)"
        r"[^。！？；\n]{0,60}(?:成立|设立|注册|落地|启用|租赁)|"
        r"(?:组建|新成立|成立|设立)[^。！？；\n]{0,80}"
        r"(?:部门|事业部|总部|实验室|研究中心)",
        re.I,
    ),
    "regulatory_or_clinical": re.compile(
        r"(?:获批|批准|受理|取得|获得)[^。！？；\n]{0,100}"
        r"(?:许可|认证|资质|临床|注册证|测试牌照|测试许可)|"
        r"(?:项目|方案|申请)[^。！？；\n]{0,80}获核准|"
        r"(?:被|因[^。！？；\n]{0,40})(?:证监会)?立案|"
        r"收到[^。！？；\n]{0,100}(?:行政处罚|处罚决定书|事先告知书)|"
        r"获得[^。！？；\n]{0,80}监管机构批准|"
        r"(?:启动|开展|进入|推进|完成)[^，,。！？；\n]{0,100}(?:人体)?(?:临床试验|临床研究)|"
        r"(?:临床试验|临床研究)[^。！？；\n]{0,100}(?:启动|开展|进入|完成|入组)|"
        r"(?:已|均已)?获得[^。！？；\n]{0,60}(?:人体临床)?概念验证|"
        r"(?:完成|通过)[^。！？；\n]{0,60}(?:人体临床)?概念验证|"
        r"(?:获|获得|取得)[^。！？；\n]{0,80}(?:FDA|NMPA|IND)[^。！？；\n]{0,40}(?:资格|批准|许可|备案)|"
        r"(?:FDA|NMPA|IND)[^。！？；\n]{0,60}(?:获批|资格|申报|备案)|"
        r"(?:I|II|III|Ⅰ|Ⅱ|Ⅲ)期[^。！？；\n]{0,60}(?:临床|入组)[^。！？；\n]{0,40}(?:完成|启动)|"
        r"通过[^。！？；\n]{0,80}(?:ISO\s*\d+|体系认证)|"
        r"完成[^。！？；\n]{0,60}(?:FDA\s*DMF|DMF)备案|"
        r"(?:将|计划)[^，,。！？；\n]{0,60}(?:进入|启动|推进)[^，,。！？；\n]{0,40}(?:临床|IND)|"
        r"(?:将|计划)[^。！？；\n]{0,80}完成(?:国内)?注册上市|"
        r"(?:临床数据显示|临床项目)[^。！？；\n]{0,140}"
        r"(?:完全缓解率|生物标志物下降|损伤改善|疗效改善)",
        re.I,
    ),
    "policy_or_standard": re.compile(
        r"(?:发布|印发|出台|实施|征求意见)[^。！？；\n]{0,120}"
        r"(?:政策|标准|办法|条例|规范|通知)",
        re.I,
    ),
    "procurement_tender": re.compile(
        r"(?:启动|发布|参与|完成)[^。！？；\n]{0,80}(?:招标|采购)|"
        r"(?:招标|采购)[^。！？；\n]{0,80}(?:启动|发布|中标|入围)",
        re.I,
    ),
    "customer_validation": re.compile(
        r"(?:客户|车企|医院|高校|实验室)[^。！？；\n]{0,120}"
        r"(?:采用|导入|验证|定点|复购)|"
        r"(?:实现|完成)[^。！？；\n]{0,80}(?:销售发货|客户交付)|"
        r"销售发货|商业化交付|"
        r"(?:验证|试点)[^。！？；\n]{0,100}(?:落地路径|商业路径)|"
        r"(?:选定|确定)[^。！？；\n]{0,80}(?:技术方|供应商|方案商)|"
        r"(?:连续)?完成[^。！？；\n]{0,60}\d+轮[^。！？；\n]{0,30}"
        r"(?:实验操作|实验|测试|验证)|"
        r"进入[^。！？；\n]{0,80}(?:交付|用户复现)阶段|"
        r"覆盖[^。！？；\n]{0,50}(?:用户|员工)[^。！？；\n]{0,100}"
        r"(?:真实|业务)?场景[^。！？；\n]{0,40}(?:完成|通过)验证|"
        r"将[^。！？；\n]{0,60}(?:业务重心|重点业务|主要业务)"
        r"[^。！？；\n]{0,80}(?:转向|进入|聚焦)[^。！？；\n]{0,60}(?:场景|行业|客户)|"
        r"(?:产品|服务|解决方案)[^。！？；\n]{0,80}(?:已)?覆盖[^。！？；\n]{0,60}"
        r"(?:客户|医院|机构|国家|省区)|"
        r"(?:已)?覆盖[^。！？；\n]{0,60}(?:客户|医院|机构|国家|省区)|"
        r"(?:已|正式)?在[^。！？；\n]{0,80}?(?:客户|医院|项目|场景|市场)"
        r"[^。！？；\n]{0,60}?(?:落地|应用|运转|部署|交付|验证)|"
        r"(?:获得|通过)[^。！？；\n]{0,80}(?:客户|药企|医院)[^。！？；\n]{0,40}"
        r"(?:认可|审核|验证)|"
        r"(?:规模化|批量化)(?:的)?[^。！？；\n]{0,30}(?:部署|交付)|"
        r"(?:交付|发货)[^。！？；\n]{0,60}(?:客户|市场)|"
        r"向[^。！？；\n]{0,80}(?:市场)?客户交付|"
        r"获得[^。！？；\n]{0,80}(?:大规模生产)?订单|"
        r"实现(?:了)?[^。！？；\n]{0,60}商业化生产案例|"
        r"(?:在)?海外[^。！？；\n]{0,60}(?:开展|完成)[^。！？；\n]{0,30}"
        r"(?:产品验证|客户验证|示范)|"
        r"(?:启动|开展|推出|招募)[^。！？；\\n]{0,50}(?:全球|社区|开发者)?大使计划",
        re.I,
    ),
    "merger_acquisition": re.compile(
        r"(?:拟|宣布|完成|同意)[^。！？；\n]{0,100}"
        r"(?:收购|并购|合并|出售|受让|转让)|"
        r"(?:收购|并购|合并)[^。！？；\n]{0,100}(?:完成|获批|交割)|"
        r"(?:控股股东|实控人|控制权)[^。！？；\n]{0,100}(?:变更|转让)|"
        r"筹划[^。！？；\n]{0,80}控制权变更|"
        r"公开挂牌转让[^。！？；\n]{0,100}股权",
        re.I,
    ),
    "enterprise_system": re.compile(
        r"(?:上线|部署|启用|建设|升级)[^。！？；\n]{0,120}"
        r"(?:ERP|MES|CRM|企业系统|管理系统|业务平台)",
        re.I,
    ),
    "open_action": re.compile(
        r"(?:带来|展示|形成|建成|打造|共建|落地|部署|交付|运转|覆盖|"
        r"扩充|拓展|启用|申报|备案|认证|入组|推进|推动|攻关|"
        r"进入|加入|担任|官宣|探索|建立|迭代|升级)"
        r"[^。！？；，,\n]{1,120}",
        re.I,
    ),
}

_CURRENT_MARKER = re.compile(
    r"今日|当天|近日|日前|近期|刚刚|今年|本月|上月|已|正式|成功|完成|获得|"
    r"宣布|发布|推出|上线|开源|签署|签订|达成|投建|扩产|投产|"
    r"量产|交付|发货|中标|承诺|增资|发行|回购|收购|并购|转让|"
    r"立案|处罚|拟|计划|将|正在"
)
_HISTORICAL_MARKER = re.compile(
    r"历史(?:上|已)?|过去曾|此前(?:曾|已)?|曾经|回顾|例如|比如|先后完成|"
    r"以[^，。；]{0,20}为例|典型范例|典型案例|"
    r"此后[一二三四五六七八九十\d]+年|先后(?:完成|落地|建设|投资)|"
    r"作为[^。！？；]{0,100}(?:企业|公司|厂商)|"
    r"真正让[^。！？；]{0,100}角色升级|不仅仅是[^。！？；]{0,80}更是|"
    r"自20\d{2}年以来"
)
_STALE_MARKER = re.compile(r"去年|前年|大前年|曾今年")
_EXPLICIT_CURRENT_MARKER = re.compile(
    r"本轮|本次|今日|当天|近日|日前|近期|今年|当前|最新|此外"
)
_COREFERENCE_LEAD = re.compile(
    r"^(?:(?:今年|本年)\d{0,2}月?[，,]?|本轮(?:融资)?|不仅如此|与此同时|同时|此外|而且|目前|最新|面向未来|该|这个|这款|其|公司|"
    r"并|也|除|核心产品|第一|第二|第三|第四|至于|截至|包括我们|其实我们|我们|"
    r"他(?:继续)?(?:说道|表示|称)|她(?:继续)?(?:说道|表示|称)|两家企业|双方|"
    r"这笔合作|该合作|"
    r"[^，,。！？；\n]{2,16}(?:称|表示)[，,])"
)
_ITEM_CONTEXT_REFERENCE = re.compile(
    r"^(?:(?:今年|本年)\d{0,2}月?[，,]?|本轮(?:融资)?|目前|记者|据|我们|包括我们|其实我们|本周|公司|该|其|她|API服务|面向未来|"
    r"两家企业|双方)|该(?:初创)?公司|这家公司|内部|旗下"
)
_SUPPORT_CONTINUATION = re.compile(
    r"^(?:本轮融资|本次融资|此轮融资|本轮资金|融资资金|资金将|投资方|"
    r"本次交易|该项目|双方协议)"
)
_SPEAKER_LEAD = re.compile(
    r"^(?:[A-Za-z][A-Za-z .-]{1,30}|[\u4e00-\u9fff·]{2,8})[：:]"
)


@dataclass(frozen=True)
class ActionSpan:
    span_id: str
    unit_id: str
    char_start: int
    char_end: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AtomicClaim:
    claim_id: str
    span_id: str
    event_type_hint: str
    event_status_hint: str
    action_text: str
    action_char_start: int
    action_char_end: int
    allowed_subject_entity_ids: tuple[str, ...]
    primary_subject_entity_id: str
    allowed_event_types: tuple[str, ...] = ()
    funding_round_hint: str = ""
    host_mandatory: bool = False
    legacy_candidate_ids: tuple[str, ...] = ()

    def to_prompt_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionScopeExclusion:
    char_start: int
    char_end: int
    text: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionSpanLedger:
    version: str
    source_id: str
    source_article_id: str
    document_type: str
    spans: tuple[ActionSpan, ...]
    claims: tuple[AtomicClaim, ...]
    exclusions: tuple[ActionScopeExclusion, ...] = ()

    def spans_by_id(self) -> dict[str, ActionSpan]:
        return {span.span_id: span for span in self.spans}

    def claims_by_id(self) -> dict[str, AtomicClaim]:
        return {claim.claim_id: claim for claim in self.claims}

    def batches(self, max_claims: int = 3) -> tuple[tuple[AtomicClaim, ...], ...]:
        if not 1 <= max_claims <= 3:
            raise ValueError("max_claims must be between 1 and 3")
        return tuple(
            tuple(self.claims[start : start + max_claims])
            for start in range(0, len(self.claims), max_claims)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_id": self.source_id,
            "source_article_id": self.source_article_id,
            "document_type": self.document_type,
            "spans": [span.to_dict() for span in self.spans],
            "claims": [claim.to_prompt_dict() for claim in self.claims],
            "exclusions": [item.to_dict() for item in self.exclusions],
        }


def _sentence_ranges(body: str, unit: DocumentUnit) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = unit.char_start
    for match in re.finditer(r"[^。！？；;\n]+[。！？；;]?", unit.text):
        raw_start = cursor + match.start()
        raw_end = cursor + match.end()
        text = body[raw_start:raw_end]
        leading = len(text) - len(text.lstrip())
        trailing = len(text) - len(text.rstrip())
        start = raw_start + leading
        end = raw_end - trailing
        if end <= start:
            continue
        split_points = [start]
        split_points.extend(
            start + boundary.end()
            for boundary in re.finditer(
                r"\s+(?=(?:\d{1,2}月\d{1,2}日(?:消息)?[，,]|"
                r"记者获悉[，,]|[A-Za-z][A-Za-z0-9 .&+-]{1,30}"
                r"(?:一位)?发言人证实[，,]))",
                body[start:end],
            )
        )
        split_points.append(end)
        for left, right in zip(split_points, split_points[1:], strict=False):
            left += len(body[left:right]) - len(body[left:right].lstrip())
            right -= len(body[left:right]) - len(body[left:right].rstrip())
            if right > left:
                ranges.append((left, right))
    return ranges


def _mentioned_entities(
    text: str,
    entity_ledger: ArticleEntityLedger,
) -> tuple[str, ...]:
    matches: list[tuple[int, int, str]] = []
    eligible_entities = entity_ledger.eligible()
    all_surfaces = tuple(
        (surface, entity.entity_id)
        for entity in eligible_entities
        for surface in (entity.canonical_name, *entity.aliases)
        if surface
    )
    for entity in eligible_entities:
        positions: list[int] = []
        for alias in (entity.canonical_name, *entity.aliases):
            if not alias:
                continue
            for mention in re.finditer(re.escape(alias), text):
                if any(
                    other_id != entity.entity_id
                    and len(other_surface) > len(alias)
                    and text.startswith(other_surface, mention.start())
                    for other_surface, other_id in all_surfaces
                ):
                    continue
                suffix = text[mention.end() : mention.end() + 12]
                if re.match(r"\s*/?\s*(?:供图|摄影|图片)", suffix):
                    continue
                if entity.canonical_name.startswith(alias):
                    remainder = entity.canonical_name[len(alias) :]
                    extended_suffix = text[
                        mention.end() : mention.end() + len(remainder) + 12
                    ]
                    if re.match(
                        re.escape(remainder)
                        + r"\s*/?\s*(?:供图|摄影|图片)",
                        extended_suffix,
                    ):
                        continue
                positions.append(mention.start())
        if positions:
            matches.append((min(positions), -len(entity.canonical_name), entity.entity_id))
    return tuple(item[2] for item in sorted(matches))


_EXPLICIT_INELIGIBLE_ENTITY_SOURCES = frozenset(
    {
        "english_context",
        "company_reference",
        "legal_name",
        "explicit_alias",
        "listed_ticker",
        "organization_role",
    }
)


def _has_explicit_ineligible_entity(
    text: str,
    entity_ledger: ArticleEntityLedger,
) -> bool:
    """Prevent long-feature fallback from relabeling a named counterpart."""

    for entity in entity_ledger.entities:
        if entity.operating_subject_eligible:
            continue
        if not (
            set(entity.discovery_sources) & _EXPLICIT_INELIGIBLE_ENTITY_SOURCES
        ):
            continue
        surfaces = tuple(
            dict.fromkeys(
                surface
                for surface in (entity.canonical_name, *entity.aliases)
                if len(surface.strip()) >= 4
            )
        )
        if any(surface and re.search(re.escape(surface), text) for surface in surfaces):
            return True
    return False


def _action_entities(
    event_type: str,
    sentence: str,
    action_start: int,
    action_end: int,
    entity_ledger: ArticleEntityLedger,
    sentence_entities: tuple[str, ...],
) -> tuple[tuple[str, ...], str]:
    clause_start = max(
        (sentence.rfind(mark, 0, action_start) for mark in "，,:：；;"),
        default=-1,
    )
    clause = sentence[clause_start + 1 : action_end]
    clause_entities = _mentioned_entities(clause, entity_ledger)
    allowed = tuple(dict.fromkeys((*clause_entities, *sentence_entities)))
    if (
        event_type == "funding"
        and clause_entities
        and not (
            "投资" in sentence[action_start:action_end]
            and "融资" not in sentence[action_start:action_end]
        )
    ):
        allowed = clause_entities
    entity_by_id = entity_ledger.by_id()
    if event_type in {
        "technical_milestone",
        "customer_validation",
        "new_site_or_entity",
        "enterprise_system",
    }:
        product_only_sources = {
            "attached_product_owner",
            "english_context",
            "versioned_brand",
            "title_brand",
        }
        trusted_sources = {
            "legal_name",
            "explicit_alias",
            "corporate_product_reference",
            "company_reference",
            "organization_role",
            "action_subject",
            "direct_clause_company",
            "company_context",
            "context_alias",
            "inline_bilingual_entity",
            "title_action",
            "reporting_operational_subject",
            "listed_ticker",
        }
        trusted = {
            entity_id
            for entity_id in allowed
            if set(entity_by_id[entity_id].discovery_sources) & trusted_sources
        }
        pruned: list[str] = []
        for entity_id in allowed:
            entity = entity_by_id[entity_id]
            sources = set(entity.discovery_sources)
            if trusted and sources and sources <= product_only_sources:
                continue
            is_product_child = False
            for other_id in allowed:
                if other_id == entity_id:
                    continue
                other = entity_by_id[other_id]
                for root in (other.canonical_name, *other.aliases):
                    if len(root) < 2 or not entity.canonical_name.startswith(root):
                        continue
                    suffix = entity.canonical_name[len(root) :]
                    if suffix and (
                        re.search(r"[A-Za-z0-9]", suffix)
                        or re.search(
                            r"新款|全场景|办公|文档|助手|智能体|平台|模型|产品",
                            suffix,
                        )
                    ):
                        is_product_child = True
                        break
                if is_product_child:
                    break
            if not is_product_child:
                pruned.append(entity_id)
        if pruned:
            allowed = tuple(pruned)
    nearest: list[tuple[int, int, str]] = []
    for entity_id in allowed:
        entity = entity_by_id[entity_id]
        positions = [
            sentence.rfind(alias, 0, action_start)
            for alias in (entity.canonical_name, *entity.aliases)
            if alias
        ]
        positions = [position for position in positions if position >= clause_start + 1]
        if positions:
            nearest.append(
                (max(positions), len(entity.canonical_name), entity_id)
            )
    nearest.sort(reverse=True)
    primary = ""
    if nearest and (len(nearest) == 1 or nearest[0][0] > nearest[1][0]):
        primary = nearest[0][2]
    elif len(allowed) == 1:
        primary = allowed[0]
    if event_type == "factory_or_capacity" and allowed:
        primary = allowed[0]
    if event_type == "executive_change" and re.match(
        r"(?:重返|加入)", sentence[action_start:action_end]
    ):
        destination = sentence[action_start:action_end]
        destination_entities = _mentioned_entities(destination, entity_ledger)
        if destination_entities:
            primary = destination_entities[-1]
    if event_type == "executive_change" and re.match(
        r"退出这家[^。！？；\n]{0,100}(?:公司|实验室)",
        sentence[action_start:action_end],
    ):
        context_only = [
            entity_id for entity_id in allowed if entity_id not in clause_entities
        ]
        if context_only:
            primary = max(
                context_only,
                key=lambda entity_id: len(entity_by_id[entity_id].canonical_name),
            )
    return allowed, primary


def _dominant_entity_id(entity_ledger: ArticleEntityLedger) -> str:
    def anchor_tier(entity_id: str) -> int:
        sources = set(entity_ledger.by_id()[entity_id].discovery_sources)
        if sources & {"legal_name", "explicit_alias", "listed_ticker"}:
            return 3
        if sources & {
            "context_alias",
            "inline_bilingual_entity",
            "company_surface",
            "title_action",
            "company_context",
        }:
            return 2
        return 1

    ranked = sorted(
        (
            (
                anchor_tier(entity.entity_id),
                len(entity.mentions),
                len(entity.canonical_name),
                entity.entity_id,
            )
            for entity in entity_ledger.eligible()
        ),
        reverse=True,
    )
    if not ranked or ranked[0][1] < 2:
        return ""
    if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
        return ""
    return ranked[0][3]


def _dominant_entity_in_text(
    text: str, entity_ledger: ArticleEntityLedger
) -> str:
    """Return a unique repeatedly mentioned operating company in one unit."""

    ranked: list[tuple[int, int, int, str]] = []
    for entity in entity_ledger.eligible():
        surfaces = tuple(
            dict.fromkeys(
                surface
                for surface in (entity.canonical_name, *entity.aliases)
                if len(surface) >= 2
            )
        )
        # Aliases often overlap one legal-name occurrence.  Use the most
        # frequent surface, not their sum, so a single image credit cannot
        # masquerade as repeated operating-company context.
        count = max(
            (len(re.findall(re.escape(surface), text)) for surface in surfaces),
            default=0,
        )
        if count:
            sources = set(entity.discovery_sources)
            tier = (
                3
                if sources & {"legal_name", "explicit_alias", "listed_ticker"}
                else (
                    2
                    if sources
                    & {
                        "context_alias",
                        "inline_bilingual_entity",
                        "company_surface",
                        "title_action",
                        "company_context",
                    }
                    else 1
                )
            )
            ranked.append((tier, count, len(entity.canonical_name), entity.entity_id))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][1] < 2:
        return ""
    if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
        return ""
    return ranked[0][3]


def _strong_route_subject(entity_id: str, entity_ledger: ArticleEntityLedger) -> bool:
    entity = entity_ledger.by_id()[entity_id]
    name = entity.canonical_name
    return bool(
        re.search(
            r"(?:公司|集团|股份|科技|智能|时代|汽车|电子|能源|材料|"
            r"生物|医药|机器人|半导体)$",
            name,
        )
        or re.fullmatch(r"[A-Z][A-Za-z0-9.+-]{1,30}", name)
        or any(
            source
            in {
                "legal_name",
                "explicit_alias",
                "rule_seed",
                "listed_ticker",
                "company_reference",
                "action_subject",
                "direct_clause_company",
                "company_context",
                "context_alias",
                "inline_bilingual_entity",
                "organization_role",
                "direct_bulletin_company",
            }
            for source in entity.discovery_sources
        )
    )


def _host_mandatory(
    event_type: str, action_text: str, context_text: str = ""
) -> bool:
    if event_type in {
        "workforce_cluster",
        "research_or_ip",
        "project_buildout",
        "global_expansion",
    } and re.search(
        r"(?:融资|资金|本轮融资|融资资金)[^。！？；\n]{0,80}"
        r"(?:用于|投向|投入|支持|重点投向)[^。！？；\n]{0,140}"
        + re.escape(action_text),
        context_text,
    ):
        # A funding-use clause is an explicit company commitment, not a
        # background description. Preserve it as a hiring precursor even when
        # the model would otherwise classify the short action phrase as
        # unsupported.
        return True
    if event_type == "project_buildout" and re.search(
        r"(?:推进|计划|拟|将)[^。！？；\n]{0,50}(?:申报|建设)",
        context_text,
    ):
        return True
    if event_type == "global_expansion" and re.search(
        r"(?:推进|计划|拟|将|加速|持续)[^。！？；\n]{0,80}"
        r"(?:全球化|全球市场|海外市场|出海)",
        context_text,
    ):
        return True
    if event_type == "major_order" and re.search(
        r"承诺.{0,80}未来支出", action_text
    ):
        return True
    if event_type == "major_order" and re.search(
        r"签署不可撤销合同", action_text
    ):
        return True
    if event_type == "major_order" and re.search(
        r"(?:正式)?(?:签署|签订)[^。！？；\n]{0,100}(?:合同|订单|采购项目)|"
        r"(?:中标|斩获|获得)[^。！？；\n]{0,100}(?:合同|订单|采购项目)",
        action_text,
    ):
        return True
    if event_type == "factory_or_capacity" and re.search(
        r"承诺.{0,40}新增.{0,40}投资", action_text
    ):
        return True
    if event_type == "factory_or_capacity" and re.search(
        r"(?:工厂|产线|产能|数据中心)[^。！？；\n]{0,100}"
        r"(?:扩建|扩大|提升|投产|开工)[^。！？；\n]{0,40}"
        r"(?:\d+(?:\.\d+)?\s*(?:兆瓦|MW|GW|万台|台|条|座))",
        action_text,
        re.I,
    ):
        return True
    if event_type == "funding" and re.search(
        r"(?:计划|拟|将).{0,40}(?:启动|开启).{0,30}融资",
        action_text,
    ):
        return True
    if event_type == "funding" and re.search(
        r"(?:原定.{0,40}开始.{0,40}(?:[A-H](?:\+{1,2}|\d+)?\s*轮|融资)|"
        r"已提前开始|(?:启动|开启|开始).{0,30}(?:新一轮|下一轮)?融资)",
        f"{context_text} {action_text}",
        re.I,
    ):
        # A financing round that has already started is a high-value current
        # signal even when the sentence is abbreviated by the action span
        # extractor (for example, ``原定八月开始的G轮已提前开始``).
        return True
    if event_type == "funding" and re.search(r"(?:宣布)?完成$", action_text):
        # ``融资完成后，公司将……`` is a continuation/background clause,
        # not a second financing announcement.  Do not host-lock it; the
        # model may reject it as duplicate_or_summary and the post-merge audit
        # will keep it out of the final event set.
        if re.search(r"融资完成(?:后|之后)", context_text):
            return False
        return True
    if (
        event_type == "funding"
        and re.search(r"(?:近日|日前|今日|本月)", context_text)
        and re.search(r"宣布(?:已)?完成", context_text)
        and re.search(
            r"完成[^。！？；\n]{0,100}"
            r"(?:融资|Pre[ -]?IPO|[A-H](?:\+{1,2}|\d+)?轮|天使轮|种子轮)",
            action_text,
            re.I,
        )
    ):
        return True
    if event_type == "funding" and re.search(
        r"(?:向|对)[^，,。；]{1,60}投资|完成(?:了)?一笔[^，,。；]{0,40}投资",
        action_text,
    ):
        return True
    if event_type == "ipo_or_listing" and re.search(
        r"(?:实施|撤销).{0,40}风险警示", action_text
    ):
        return True
    if event_type == "customer_validation" and re.search(
        r"销售发货|商业化交付|(?:验证|试点).{0,100}(?:落地路径|商业路径)|"
        r"覆盖[^。！？；\n]{0,80}(?:用户|员工)[^。！？；\n]{0,120}"
        r"真实业务场景[^。！？；\n]{0,40}(?:完成|通过)验证|"
        r"进入[^。！？；\n]{0,80}交付[^。！？；\n]{0,60}用户复现|"
        r"将[^。！？；\n]{0,80}业务重心[^。！？；\n]{0,80}"
        r"(?:转向|进入|聚焦)[^。！？；\n]{0,80}(?:场景|行业|客户)",
        action_text,
    ):
        return True
    if event_type == "customer_validation" and re.search(
        r"累计出货|规模化交付|量产交付|客户验证|"
        r"(?:选定|确定)[^。！？；\n]{0,80}(?:技术方|供应商|方案商)|"
        r"(?:连续)?完成[^。！？；\n]{0,60}\d+轮[^。！？；\n]{0,30}"
        r"(?:实验操作|实验|测试|验证)|"
        r"(?:启动|开展|推出|招募)[^。！？；\\n]{0,50}(?:全球|社区|开发者)?大使计划",
        action_text,
    ):
        return True
    if event_type == "technical_milestone" and re.search(
        r"(?:发布|推出)[^。！？；\n]{0,100}(?:赛道|专项|专题|挑战赛)",
        action_text,
    ):
        return True
    if event_type == "policy_or_standard" and re.search(
        r"(?:印发|发布|出台|实施|征求意见)[^。！？；\n]{0,120}"
        r"(?:政策|标准|办法|条例|规范|通知)|关于[^。！？；\n]{0,120}通知",
        f"{context_text} {action_text}",
    ):
        # Policy notices are themselves a dated industry signal; keep the
        # deterministic seed even though the issuer is not an operating
        # company and therefore cannot be a normal lead subject.
        return True
    if event_type == "technical_milestone" and re.search(
        r"(?:计划|拟|将).{0,80}(?:开启|启动).{0,40}(?:适配|验证|测试)",
        action_text,
    ):
        return True
    if event_type == "technical_milestone" and re.search(
        r"(?:发布|推出|上线)[^，。；\n]{1,100}(?:工作台|平台|系统|模型|产品|方案|"
        r"工具|功能|智能体|助手|Agent)|"
        r"(?:发布|推出)\s*[A-Z][A-Za-z0-9 ._+/-]{1,48}|"
        r"(?:封禁|阻断)[^，。；\n]{0,80}(?:账号|攻击|威胁)",
        action_text,
    ):
        return True
    if event_type == "technical_milestone" and re.search(
        r"推进[^，。；\n]{0,40}申报",
        action_text,
    ):
        return True
    if event_type == "technical_milestone" and re.search(
        r"(?:在)?API中推出[^，。；]{1,50}(?:模式|服务|功能)",
        context_text,
        re.I,
    ):
        return True
    if event_type == "technical_milestone" and re.search(
        r"开启内测|开始(?:小范围)?测试|上线(?:App|APP|PC端|网页端|客户端|服务)|"
        r"(?:将于|将在|预计|计划)[^，。；]{0,50}(?:发布|推出|接入|上线)|"
        r"正在训练\s*[A-Z][A-Za-z0-9 ._+/-]{1,48}|"
        r"完成[^，。；]{0,60}基础设施[^，。；]{0,30}重构|"
        r"推出[A-Z][A-Za-z0-9 ._+/-]{1,48}",
        action_text,
    ):
        return True
    if event_type == "new_site_or_entity" and re.search(
        r"(?:设立|成立|组建)[^，。；]{0,80}(?:总部|部门|实验室|研究院|中心|子公司)",
        action_text,
    ):
        return True
    if event_type == "executive_change" and re.search(
        r"退出这家[^。！？；\n]{0,100}(?:公司|实验室)", action_text
    ):
        return True
    if event_type == "executive_change" and re.match(
        r"(?:重返|加入)[^。！？；\n]{0,60}(?:公司|集团|OpenAI|实验室)",
        action_text,
        re.I,
    ):
        return True
    if event_type == "partnership" and re.search(
        r"(?:达成|签署|签订|建立|开展|深化)[^。！？；\n]{0,80}"
        r"(?:战略合作|合作协议|合作备忘录|长期合作|联合研发)",
        action_text,
    ):
        return True
    if event_type == "partnership" and re.search(
        r"\u4f1a\u9762|\u4f1a\u6664|\u78cb\u5546|\u5546\u8c08|\u6d3d\u8c08",
        action_text,
    ):
        # A named bilateral meeting/negotiation is an explicit operating
        # event for every fan-out subject.
        return True
    return False


def _subject_groups_for_action(
    event_type: str,
    sentence: str,
    allowed_entities: tuple[str, ...],
    primary_entity: str,
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """Return output-capable subject groups for one atomic action.

    The adjudication contract can emit only one subject per Claim.  Most
    actions therefore remain one Claim with a bounded choice set.  Explicit
    bilateral negotiations and joint-contract language are different: the
    source asserts participation by every named operating company, so the
    host fans the action out into one independently adjudicated Claim per
    subject instead of pretending one Claim can cover multiple events.
    """

    should_fan_out = bool(
        len(allowed_entities) > 1
        and (
            (
                event_type == "partnership"
                and (
                    re.search(r"会面|会晤|磋商|商谈|洽谈", sentence)
                    or re.search(
                        r"签署|签订|携手|共建|共同",
                        sentence,
                    )
                    or re.search(
                        r"联合[^。！？；\n]{0,20}(?:发布|推出|研发|建设|主办|签署|签订|声明|合作)",
                        sentence,
                    )
                )
            )
            or (
                event_type == "major_order"
                and re.search(
                    r"(?:联合体|联合[^。！？；\n]{0,80})(?:签署|签订)",
                    sentence,
                )
            )
            or (
                event_type == "ipo_or_listing"
                and re.search(r"新股申购", sentence)
            )
        )
    )
    if should_fan_out:
        return tuple(((entity_id,), entity_id) for entity_id in allowed_entities)
    return ((allowed_entities, primary_entity),)


def _funding_round_hints(event_type: str, action_text: str) -> tuple[str, ...]:
    if event_type not in {"funding", "open_action"}:
        return ("",)
    rounds: list[str] = []
    for match in re.finditer(
        r"Pre[ -]?(?:IPO|[A-H](?:\+{1,2})?)\s*轮|"
        r"[A-H](?:\+{1,2}|\d+)?\s*轮|"
        r"天使(?:\s*\+{1,2})?\s*轮|种子\s*轮",
        action_text,
        re.I,
    ):
        rounds.append(re.sub(r"\s+", "", match.group(0)))
    if re.search(r"天使\s*\+\s*/\s*\+\+\s*轮", action_text):
        rounds.extend(("天使+轮", "天使++轮"))
    for match in re.finditer(
        r"(?P<first>[A-H]\d+)(?:及|和|、)(?P<second>[A-H]\d+)轮",
        action_text,
        re.I,
    ):
        rounds.extend((f"{match.group('first')}轮", f"{match.group('second')}轮"))
    unique = tuple(dict.fromkeys(rounds))
    conventional = tuple(
        value
        for value in unique
        if re.fullmatch(r"[A-H](?:\+{1,2}|\d+)?轮", value, re.I)
    )
    if conventional and any(value.casefold() == "preipo轮" for value in unique):
        # ``G轮（Pre IPO轮）`` is one round, not two competing claims.  Prefer
        # the conventional round label while retaining standalone Pre-IPO
        # rounds when no A-H round is present.
        unique = tuple(value for value in unique if value.casefold() != "preipo轮")
    return unique if len(unique) > 1 else (unique[0] if unique else "",)


def _open_allowed_event_types(action_text: str) -> tuple[str, ...]:
    """Return a bounded taxonomy for one high-recall open action."""

    output: list[str] = []
    families = (
        # Deployment of named AI employees/agents is an operating-system or
        # workflow rollout, not an untyped ``other`` claim.  Keep this narrow
        # so generic editorial uses of "覆盖" retain the existing fallback.
        (
            r"AgentOne|AI员工|员工正式上岗|覆盖(?:AI)?(?:销售|客服|运营|营销)",
            "enterprise_system",
        ),
        (
            r"临床|IND|FDA|NMPA|注册上市|注册证|认证|备案|入组|PoC|概念验证",
            "regulatory_or_clinical",
        ),
        (r"融资|募资|轮|投资", "funding"),
        (r"合作|联合|携手|共建|共同", "partnership"),
        (
            r"产能|工厂|产线|生产基地|车间|量产|规模化交付能力",
            "factory_or_capacity",
        ),
        (
            r"客户|医院|市场|部署|交付|落地应用|运转|验证场景|商业化",
            "customer_validation",
        ),
        (r"加入|担任|任命|聘任|离任|辞任|总裁|CEO|VP", "executive_change"),
        (
            r"\u53d1\u5e03|\u5c55\u793a|\u9996\u53d1|\u5e73\u53f0|\u7cfb\u7edf|\u4ea7\u54c1|\u6a21\u578b|Model|Foundation|\u6280\u672f|\u653b\u5173|\u6253\u901a|\u6253\u9020|\u5f62\u6210|\u5347\u7ea7[^\u3002\uff01\uff1f\uff1b\n]{0,40}(?:\u529f\u80fd|\u5e73\u53f0|\u6a21\u578b|\u4ea7\u54c1|\u670d\u52a1)",
            "technical_milestone",
        ),
        (
            r"核心研发团队扩充|研发团队扩充|团队扩充|高端人才引进|人才引进|人才招聘|招募人才",
            "workforce_cluster",
        ),
        (
            r"基础模型持续迭代|技术研发|数据闭环建设|工程化验证|协同研发|共性技术研发|研发投入",
            "research_or_ip",
        ),
        (
            r"产业化平台建设|基础实验平台建设|中试生产平台建设|产业创新平台申报|平台申报|产业平台建设",
            "project_buildout",
        ),
        (
            r"全球化落地|全球市场拓展|海外市场拓展|出海布局|海外业务布局",
            "global_expansion",
        ),
        (r"总部|研究院|实验室|事业部|中心|启用", "new_site_or_entity"),
    )
    for pattern, event_type in families:
        if re.search(pattern, action_text, re.I):
            output.append(event_type)
    return tuple(dict.fromkeys(output or ["other"]))


def _status_hint(text: str, action_start: int) -> str:
    prefix = text[max(0, action_start - 20) : action_start]
    action = text[action_start : action_start + 100]
    if "新股申购" in action:
        return "started"
    if re.search(r"接近完成|即将完成", action):
        return "started"
    if re.search(r"进入[^，。；]{0,40}阶段", action):
        return "started"
    if re.search(r"累计|截至", prefix):
        return "cumulative"
    # "推进……申报" describes an application/plan that has not yet been
    # completed.  The bounded phrase check is deliberately specific: other
    # uses of "推进" (for example,推进量产) may already be underway.
    if re.search(r"推进[^，。；]{0,40}申报", action):
        if not re.search(r"(?:完成|已|成功)[^，。；]{0,10}申报", action):
            return "target"
    if "申报" in action and re.search(
        r"(?:推进|计划|拟|将|申请)", prefix
    ):
        return "target"
    # Short action matches such as high-end talent introduction often begin
    # after the planning predicate. Recover the status from the whole sentence
    # when the clause is explicitly a use of financing or earmarked funds.
    if re.search(
        r"(?:融资|资金|本轮融资|融资资金)[^。！？；\n]{0,100}"
        r"(?:用于|投向|投入|支持|重点投向)[^。！？；\n]{0,160}"
        + re.escape(action),
        text,
    ):
        return "target"
    if re.search(r"拟|计划|预计|目标|未来将|将于|将在|将|力争|有望", prefix):
        return "target"
    if re.match(r"(?:推荐|建议).{0,40}(?:任|不再担任)", action):
        return "target"
    if re.match(r"(?:拟|计划|将)", action) or "日起" in prefix:
        return "target"
    if re.search(r"承诺.{0,40}新增.{0,40}投资", action[:100]):
        return "target"
    if re.search(
        r"(?:扩建|扩产|建设|投建)计划|计划[^，。；]{0,50}(?:扩大|扩建|扩产|建设)",
        action[:100],
    ):
        return "target"
    if re.search(r"开展[^，。；]{0,30}(?:长期合作|合作)", action[:40]):
        return "started"
    if re.search(r"磋商|商谈|洽谈", action[:100]):
        return "started"
    future_action = re.search(
        r"(?:拟|将|计划)[^，。；]{0,8}"
        r"(?:变更|收购|投建|建设|融资|上市|挂牌|投产|量产|扩产|开工|设立|成立|发布|推出|交付|接入|签署|合作)",
        action[:30],
    )
    completed_before_future = bool(
        future_action
        and re.search(
            r"完成|签署|签订|达成|发布|推出|获批|获得",
            action[: future_action.start()],
        )
    )
    if future_action and not completed_before_future:
        return "target"
    if "立案" in action or re.search(r"正在|筹划|启动|开启|开始", action[:20]):
        return "started"
    if re.search(r"启动|开启|筹划|正在|开始", prefix):
        return "started"
    return "completed"


def _legacy_ids_for_span(
    span: ActionSpan,
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    output: list[str] = []
    for candidate in candidates:
        quote = str(candidate.get("quote") or "")
        if not quote:
            continue
        overlaps = span.text in quote or quote in span.text
        if not overlaps:
            candidate_start = candidate.get("char_start")
            candidate_end = candidate.get("char_end")
            try:
                overlaps = bool(
                    int(candidate_start) < span.char_end
                    and int(candidate_end) > span.char_start
                )
            except (TypeError, ValueError):
                overlaps = False
        if overlaps:
            value = str(candidate.get("claim_id") or candidate.get("id") or "")
            if value:
                output.append(value)
    return tuple(dict.fromkeys(output))


def _action_signature(value: str) -> str:
    return re.sub(
        r"[^A-Za-z0-9\u4e00-\u9fff]+|(?:正式|已经|已|今日|目前|近日|日前)",
        "",
        value,
    ).casefold()


def _suppress_headline_duplicate_claims(
    claims: Sequence[AtomicClaim],
    spans_by_id: Mapping[str, ActionSpan],
) -> tuple[AtomicClaim, ...]:
    """Prefer the detailed occurrence over a repeated aggregator headline."""

    suppressed: set[str] = set()
    for index, claim in enumerate(claims):
        span = spans_by_id[claim.span_id]
        if not re.match(r"^[（(][^）)\n]{1,16}[）)]\s*", span.text):
            continue
        signature = _action_signature(claim.action_text)
        if len(signature) < 6:
            continue
        for later in claims[index + 1 :]:
            later_span = spans_by_id[later.span_id]
            if later.action_char_start - claim.action_char_start > 320:
                break
            if later.event_type_hint != claim.event_type_hint:
                continue
            if not (
                set(claim.allowed_subject_entity_ids)
                & set(later.allowed_subject_entity_ids)
            ):
                continue
            later_signature = _action_signature(later.action_text)
            if len(later_signature) < 6:
                continue
            same_action = (
                signature in later_signature
                or later_signature in signature
                or len(
                    set(signature[index : index + 3] for index in range(len(signature) - 2))
                    & set(
                        later_signature[index : index + 3]
                        for index in range(len(later_signature) - 2)
                    )
                )
                >= 3
            )
            if same_action and later_span.char_start >= span.char_start:
                suppressed.add(claim.claim_id)
                break
    return tuple(claim for claim in claims if claim.claim_id not in suppressed)


def _suppress_enterprise_duplicate_claims(
    claims: Sequence[AtomicClaim],
    spans_by_id: Mapping[str, ActionSpan],
) -> tuple[AtomicClaim, ...]:
    """Collapse headline/body restatements for typed enterprise rollouts."""

    suppressed: set[str] = set()
    for index, claim in enumerate(claims):
        if not (
            claim.event_type_hint == "open_action"
            and "enterprise_system" in claim.allowed_event_types
        ):
            continue
        signature = _action_signature(claim.action_text)
        normalized = re.sub(
            r"ai|四大|四个|[一二三四五六七八九十0-9]+|和|与|及|场景",
            "",
            signature,
        )
        if len(normalized) < 6:
            continue
        for later in claims[index + 1 :]:
            if not (
                later.event_type_hint == "open_action"
                and "enterprise_system" in later.allowed_event_types
                and set(claim.allowed_subject_entity_ids)
                & set(later.allowed_subject_entity_ids)
            ):
                continue
            later_span = spans_by_id[later.span_id]
            span = spans_by_id[claim.span_id]
            if later.action_char_start - claim.action_char_start > 320:
                break
            later_signature = _action_signature(later.action_text)
            later_normalized = re.sub(
                r"ai|四大|四个|[一二三四五六七八九十0-9]+|和|与|及|场景",
                "",
                later_signature,
            )
            if (
                normalized == later_normalized
                and later_span.char_start >= span.char_start
            ):
                suppressed.add(claim.claim_id)
                break
    return tuple(claim for claim in claims if claim.claim_id not in suppressed)


def _scope_allows(
    route: DocumentRoute,
    sentence: str,
    published_at: str,
) -> bool:
    # Routing determines document structure and batching, not whether an
    # operating predicate is true.  Temporal/background exclusions belong to
    # the local action clause below.  Keeping this host stage recall-oriented
    # prevents current conference phrasing such as “本届…带来” from being
    # discarded merely because it does not use a small current-marker lexicon.
    return True


def _action_scope_allows(
    route: DocumentRoute,
    sentence: str,
    published_at: str,
    action_start: int,
    action_end: int,
) -> bool:
    """Apply temporal scope to the local action clause, not the whole sentence."""

    left = max(
        (sentence.rfind(mark, 0, action_start) for mark in "，,:：；;"),
        default=-1,
    )
    right_candidates = [
        position
        for mark in "，,:：；;"
        if (position := sentence.find(mark, action_end)) >= 0
    ]
    right = min(right_candidates) if right_candidates else len(sentence)
    clause = sentence[left + 1 : right]
    # A temporal qualifier can sit immediately before the clause delimiter,
    # e.g. ``去年10月，研究院成立``.  The delimiter-bounded clause above
    # would otherwise retain the action while dropping the stale qualifier.
    # Inspect only a short local prefix so an unrelated historical paragraph
    # elsewhere in the sentence cannot suppress a current action.
    local_prefix = sentence[max(0, action_start - 48) : action_start]
    if _STALE_MARKER.search(local_prefix) and not _EXPLICIT_CURRENT_MARKER.search(
        local_prefix
    ):
        return False
    if _STALE_MARKER.search(clause):
        return False
    if _HISTORICAL_MARKER.search(clause) and not _EXPLICIT_CURRENT_MARKER.search(
        clause
    ):
        return False
    if (
        route.document_type == "long_feature"
        and re.match(r"^(?:随后|其后|后来)[，,]", clause)
        and not re.search(r"20\d{2}年|今年|本月|近日|近期", clause)
    ):
        return False
    article_year = int(published_at[:4]) if published_at[:4].isdigit() else 0
    explicit_years = [int(value) for value in re.findall(r"(20\d{2})年", clause)]
    if article_year and explicit_years and max(explicit_years) < article_year:
        return False
    return True


def build_action_span_ledger(
    article: CleanArticle,
    entity_ledger: ArticleEntityLedger,
    legacy_candidates: Iterable[Mapping[str, Any]] = (),
) -> ActionSpanLedger:
    route = route_document(article)
    candidates = [dict(item) for item in legacy_candidates]
    spans: dict[tuple[int, int], ActionSpan] = {}
    claims: list[AtomicClaim] = []
    exclusions: list[ActionScopeExclusion] = []
    seen_claims: set[tuple[str, str, int, int, tuple[str, ...]]] = set()
    dominant_entity_id = _dominant_entity_id(entity_ledger)

    previous_subject_range: tuple[int, int] | None = None
    previous_subject_ids: tuple[str, ...] = ()
    for unit in route.units:
        if route.document_type == "multi_company_bulletin":
            previous_subject_range = None
            previous_subject_ids = ()
        unit_dominant_entity_id = _dominant_entity_in_text(unit.text, entity_ledger)
        sentence_ranges = _sentence_ranges(article.clean_body, unit)
        for position, (start, end) in enumerate(sentence_ranges):
            sentence = article.clean_body[start:end]
            if re.search(
                r"(?:委派|推荐|建议|免去|不再担任)[^。！？；\n]{0,80}"
                r"(?:监事会主席|监事)(?:职务)?",
                sentence,
            ):
                exclusions.append(
                    ActionScopeExclusion(
                        char_start=start,
                        char_end=end,
                        text=sentence,
                        reason="governance_role_outside_operating_director_scope",
                    )
                )
                continue
            if not _scope_allows(route, sentence, article.index.published_at):
                continue
            allowed_entities = _mentioned_entities(sentence, entity_ledger)
            contextual_entities = entity_ledger.contextual_subject_ids(start, end)
            if contextual_entities and (
                not allowed_entities
                or _ITEM_CONTEXT_REFERENCE.search(sentence)
                or _SPEAKER_LEAD.search(sentence)
            ):
                allowed_entities = tuple(
                    dict.fromkeys((*allowed_entities, *contextual_entities))
                )
            if (
                previous_subject_range is not None
                and _COREFERENCE_LEAD.search(sentence)
                and not contextual_entities
            ):
                previous_entities = _mentioned_entities(
                    article.clean_body[
                        previous_subject_range[0] : previous_subject_range[1]
                    ],
                    entity_ledger,
                )
                allowed_entities = tuple(
                    dict.fromkeys(
                        (*allowed_entities, *previous_subject_ids, *previous_entities)
                    )
                )
            if route.document_type in {"roadmap", "commentary"}:
                allowed_entities = tuple(
                    entity_id
                    for entity_id in allowed_entities
                    if _strong_route_subject(entity_id, entity_ledger)
                )
            fallback_entity_id = unit_dominant_entity_id or dominant_entity_id
            if route.document_type == "multi_company_bulletin":
                # Digest units are already bounded by adapter item scopes.
                # Never inherit the article-wide/unit-dominant company into a
                # generic pronoun sentence, even when a local scope exists.
                fallback_entity_id = ""
            explicit_ineligible_entity = _has_explicit_ineligible_entity(
                sentence, entity_ledger
            )
            can_inherit = bool(
                fallback_entity_id
                and not explicit_ineligible_entity
                and (
                    (not allowed_entities and route.document_type == "single_company_flash")
                    or route.document_type == "long_feature"
                    or _COREFERENCE_LEAD.search(sentence)
                    or _SPEAKER_LEAD.search(sentence)
                )
            )
            if can_inherit and fallback_entity_id not in allowed_entities:
                allowed_entities = (fallback_entity_id, *allowed_entities)
            explicit_subject_range = (start, end) if allowed_entities else None
            if not allowed_entities:
                continue
            if explicit_subject_range is not None:
                previous_subject_range = (start, end)
                previous_subject_ids = allowed_entities
            span_end = end
            if position + 1 < len(sentence_ranges):
                next_start, next_end = sentence_ranges[position + 1]
                continuation = article.clean_body[next_start:next_end]
                if _SUPPORT_CONTINUATION.search(continuation):
                    span_end = next_end
            for event_type, pattern in ACTION_PATTERNS.items():
                for action in pattern.finditer(sentence):
                    if not _action_scope_allows(
                        route,
                        sentence,
                        article.index.published_at,
                        action.start(),
                        action.end(),
                    ):
                        continue
                    action_start = start + action.start()
                    action_end = start + action.end()
                    action_text = article.clean_body[action_start:action_end]
                    if event_type == "open_action":
                        normalized_open = re.sub(r"\W+", "", action_text).casefold()
                        duplicates_locked_claim = any(
                            claim.event_type_hint != "open_action"
                            and claim.action_char_start < action_end
                            and claim.action_char_end > action_start
                            and (
                                normalized_open
                                in re.sub(r"\W+", "", claim.action_text).casefold()
                                or re.sub(
                                    r"\W+", "", claim.action_text
                                ).casefold()
                                in normalized_open
                            )
                            for claim in claims
                        )
                        if duplicates_locked_claim:
                            continue
                    if (
                        event_type == "executive_change"
                        and action_text.startswith("重返")
                        and re.match(
                            r"\s*(?:(?:公司|集团|实验室)\s*)?后",
                            sentence[action.end() :],
                        )
                    ):
                        continue
                    if (
                        event_type in {"executive_change", "technical_milestone", "open_action"}
                        and re.search(
                            r"(?:^|[?,])20\d{2}\u5e74|\u4efb\u804c\u671f\u95f4",
                            sentence,
                        )
                        and not re.search(
                            r"\u4eca\u5e74|\u672c\u6708|\u8fd1\u65e5|\u76ee\u524d|\u5f53\u524d|\u4eca\u5929",
                            sentence,
                        )
                    ):
                        # Biographical/history clauses should not become
                        # current operating events.
                        continue
                    if (
                        event_type == "open_action"
                        and re.search(
                            r"\u4ecd\u5904\u4e8e\u65e9\u671f\u63a2\u7d22\u9636\u6bb5|\u81f4\u529b\u4e8e\u5c06",
                            sentence,
                        )
                    ):
                        # Product background/explainer prose is not a
                        # discrete operating event.
                        continue
                    if (
                        event_type == "regulatory_or_clinical"
                        and re.search(
                            r"\u4e0d\u4f1a\u5f71\u54cd|\u7981\u4ee4|\u653f\u7b56\u53d8\u66f4|\u53d7\u7ba1\u5236",
                            sentence,
                        )
                    ):
                        # Risk/policy commentary is not a positive
                        # operating-company hiring signal.
                        continue
                    if (
                        event_type == "open_action"
                        and (
                            re.match(
                                r"(?:\u7528\u6237|\u4f01\u4e1a\u7528\u6237|\u5f00\u53d1\u8005)\s*\u4f7f\u7528",
                                sentence,
                            )
                            or (
                                "\u5de5\u5382" in sentence
                                and re.search(
                                    r"\u6269\u5efa|\u6269\u4ea7|\u90e8\u7f72\u89c4\u6a21|\u5146\u74e6",
                                    sentence,
                                )
                            )
                            or re.search(
                                r"\u6bcf\u4e2a\u4eba|\u4efb\u4f55\u4eba|\u4e0b\u8f7d\u5e76\u90e8\u7f72|\u53ef\u81ea\u7531\u4f7f\u7528",
                                sentence,
                            )
                        )
                    ):
                        # Generic user instructions describe a product
                        # use case, not an operating-company action.
                        continue
                    if (
                        event_type == "partnership"
                        and re.search(
                            r"\u5927\u6982\u7387|\u4e1a\u5185[^\u3002\uff01\uff1f\uff1b\n]{0,12}\u5224\u65ad",
                            sentence,
                        )
                    ):
                        # Analyst speculation about a possible future
                        # partnership is not a grounded current action.
                        continue
                    if (
                        event_type == "partnership"
                        and re.match(r"\u8054\u5408\u521b\u59cb\u4eba", action_text)
                    ):
                        # A role descriptor is not a bilateral operating-company partnership.
                        continue
                    if (
                        event_type == "technical_milestone"
                        and re.search(
                            r"处于[^，。；\n]{0,30}发布(?:阶段|期)",
                            sentence,
                        )
                        and "处于" in action_text
                    ):
                        continue
                    if (
                        event_type == "customer_validation"
                        and "工厂" in sentence
                        and re.search(r"扩建|扩产|部署规模|兆瓦", sentence)
                    ):
                        # A quantified factory-expansion sentence is a
                        # capacity event, not a separate customer-validation
                        # event for every named infrastructure operator.
                        continue
                    if (
                        event_type == "new_site_or_entity"
                        and re.search(
                            r"(?:尚未开始执行|尚未执行)[^。！？；\n]{0,30}租赁|"
                            r"租赁承诺|"
                            r"(?:商讨|洽谈)[^。！？；\n]{0,80}租赁[^。！？；\n]{0,80}担保|"
                            r"租赁[^。！？；\n]{0,80}提供[^。！？；\n]{0,30}担保",
                            sentence,
                        )
                    ):
                        continue
                    if (
                        event_type == "technical_milestone"
                        and re.search(r"(?:发布|披露)(?:了)?公告", sentence)
                        and re.match(r"(?:发布|披露)(?:了)?公告", action_text)
                    ):
                        continue
                    if event_type == "major_order" and re.search(
                        r"借款合同|贷款合同|授信合同|融资合同", sentence
                    ):
                        continue
                    if event_type == "new_site_or_entity" and re.search(
                        r"注册资本", sentence
                    ):
                        continue
                    if (
                        event_type == "funding"
                        and re.search(
                            r"DFI|注册批文|债务融资工具|交易商协会",
                            sentence,
                            re.I,
                        )
                    ):
                        continue
                    if (
                        event_type == "new_site_or_entity"
                        and re.search(
                            r"注册批文|债务融资工具|交易商协会",
                            sentence,
                        )
                    ):
                        continue
                    if (
                        event_type == "technical_milestone"
                        and re.search(r"(?:发布|推出|上线)的", action_text)
                        and re.search(r"正是|具象化|体现", sentence)
                    ):
                        continue
                    claim_entities, primary_entity = _action_entities(
                        event_type,
                        sentence,
                        action.start(),
                        action.end(),
                        entity_ledger,
                        allowed_entities,
                    )
                    if event_type == "partnership" and len(claim_entities) > 1:
                        if "双方" in sentence and action.group(0).startswith("达成"):
                            # For a bilateral continuation, prefer the last
                            # named company before the bilateral pronoun.
                            if "投资" in sentence:
                                investment_prefix = sentence.split("投资", 1)[0]
                                prefix_entities = _mentioned_entities(
                                    investment_prefix, entity_ledger
                                )
                                primary_entity = (
                                    prefix_entities[0]
                                    if prefix_entities
                                    else claim_entities[0]
                                )
                            else:
                                bilateral_prefix = sentence.split("双方", 1)[0]
                                prefix_entities = _mentioned_entities(
                                    bilateral_prefix, entity_ledger
                                )
                                primary_entity = (
                                    prefix_entities[-1]
                                    if prefix_entities
                                    else claim_entities[0]
                                )
                        else:
                            primary_entity = (
                                dominant_entity_id
                                if dominant_entity_id in claim_entities
                                else ""
                            )
                    # Subject inheritance carries only entity IDs.  Evidence
                    # remains the current immutable sentence instead of
                    # recursively swallowing prior paragraphs.
                    span_key = (start, span_end)
                    span = spans.get(span_key)
                    if span is None:
                        span_text = article.clean_body[start:span_end]
                        span_material = (
                            f"{article.index.source_id}\0{article.index.source_article_id}\0"
                            f"{start}\0{span_end}\0{span_text}"
                        )
                        span = ActionSpan(
                            span_id=f"as_{sha1(span_material.encode('utf-8')).hexdigest()[:14]}",
                            unit_id=unit.unit_id,
                            char_start=start,
                            char_end=span_end,
                            text=span_text,
                        )
                        spans[span_key] = span
                    subject_groups = _subject_groups_for_action(
                        event_type, sentence, claim_entities, primary_entity
                    )
                    round_hints = _funding_round_hints(event_type, action_text)
                    for subject_entities, subject_primary in subject_groups:
                        for funding_round_hint in round_hints:
                            claim_key = (
                                event_type,
                                span.span_id,
                                action_start,
                                action_end,
                                subject_entities,
                                funding_round_hint,
                            )
                            if claim_key in seen_claims:
                                continue
                            seen_claims.add(claim_key)
                            claim_material = (
                                f"{span.span_id}\0{event_type}\0{action_start}\0"
                                f"{action_end}\0{'|'.join(subject_entities)}\0"
                                f"{funding_round_hint}"
                            )
                            claims.append(
                                AtomicClaim(
                                    claim_id=(
                                        "ac_"
                                        + sha1(
                                            claim_material.encode("utf-8")
                                        ).hexdigest()[:14]
                                    ),
                                    span_id=span.span_id,
                                    event_type_hint=event_type,
                                    event_status_hint=_status_hint(
                                        sentence, action.start()
                                    ),
                                    action_text=action_text,
                                    action_char_start=action_start,
                                    action_char_end=action_end,
                                    allowed_subject_entity_ids=subject_entities,
                                    primary_subject_entity_id=subject_primary,
                                    allowed_event_types=(
                                        _open_allowed_event_types(action_text)
                                        if event_type == "open_action"
                                        else (event_type,)
                                    ),
                                    funding_round_hint=funding_round_hint,
                                    host_mandatory=_host_mandatory(
                                        event_type, action_text, sentence
                                    ),
                                    legacy_candidate_ids=_legacy_ids_for_span(
                                        span, candidates
                                    ),
                                )
                            )

    ordered_spans = tuple(
        sorted(spans.values(), key=lambda item: (item.char_start, item.char_end))
    )
    ordered_claims = tuple(
        sorted(
            claims,
            key=lambda item: (
                spans_by_id[item.span_id].char_start,
                item.action_char_start,
                item.event_type_hint,
                item.claim_id,
            ),
        )
        if (spans_by_id := {span.span_id: span for span in ordered_spans})
        else ()
    )
    ordered_claims = _suppress_headline_duplicate_claims(
        ordered_claims, {span.span_id: span for span in ordered_spans}
    )
    ordered_claims = _suppress_enterprise_duplicate_claims(
        ordered_claims, {span.span_id: span for span in ordered_spans}
    )
    return ActionSpanLedger(
        version="action-span-ledger-v1",
        source_id=article.index.source_id,
        source_article_id=article.index.source_article_id,
        document_type=route.document_type,
        spans=ordered_spans,
        claims=ordered_claims,
        exclusions=tuple(exclusions),
    )


__all__ = [
    "ACTION_PATTERNS",
    "ActionSpan",
    "ActionSpanLedger",
    "ActionScopeExclusion",
    "AtomicClaim",
    "build_action_span_ledger",
]
