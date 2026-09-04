"""Host-owned article entity ledger for strict semantic extraction.

The ledger deliberately separates *discovery* from *eligibility*.  A string may
be recorded for audit purposes, but it is not offered to the model as an
operating-company subject unless the host can point to company-shaped evidence
next to an operational action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha1
import re
import unicodedata
from typing import Any, Iterable, Mapping

from .entities import (
    canonical_company_name,
    company_alias_candidates,
    is_company_like,
)
from .models import CleanArticle, SemanticEvent


_NON_OPERATING_SUFFIX = re.compile(
    r"(?:政府|委员会|协会|管理局|办公室|研究所|大学|学院|实验室|"
    r"药监局|证监会|交易所|项目|园区|基地|会展中心|媒体|报社|联合体)$"
)
_INVESTMENT_SUFFIX = re.compile(
    r"(?:资本|基金|创投|金控|产投|资管|投资|证券|保险|"
    r"资产管理|投资管理|Capital|Ventures|Fund|Partners)$",
    re.I,
)
_INVESTMENT_INSTITUTION_NAME = re.compile(
    r"(?:资本|基金|创投|私募|资产管理|投资管理|股权投资|创业投资|资本管理|"
    r"金控|产投|财务顾问)"
)
_MEDIA_OR_ADVERTISING_COMPANY = re.compile(r"(?:传媒|广告|文化发展)")
_GENERIC_NAME = re.compile(r"^(?:某|相关|多家|一家|该)(?:公司|企业|机构)$")
_PLACEHOLDER_COMPANY = re.compile(
    r"^(?:某|一家|多家|L[0-5])[^。！？；\n]{0,20}(?:公司|企业)$",
    re.I,
)
_JOINED_SUBJECT = re.compile(r".{2,30}(?:与|和|联合|携手).{2,30}")
_REPORTING_LEAD = re.compile(
    r"^(?:刚刚|近日|日前|近期|目前|据|据悉|消息称|报道称|同时|此外)[，,:： ]*"
)
_MEDIA_LEAD = re.compile(r"^(?:[（(][^）)\n]{1,16}[）)]|【[^】\n]{1,16}】)\s*")
_MEDIA_MARKER = re.compile(r"[（(][^）)\n]{1,16}[）)]\s*")
_SUBJECT_PREFIX = re.compile(
    r"^.*?(?:旗下(?:自动驾驶|机器人|芯片)?公司|全资子公司|控股子公司|子公司|"
    r"领军企业|代表企业|初创企业|创业公司|机器人公司|芯片公司|制造商|"
    r"开发商|运营商|运营主体|(?:AI\s*Token\s*)?生产服务商|潮玩品牌|"
    r"[A-Za-z0-9\u4e00-\u9fff·+*-]{1,24}企业)"
)
_SUBJECT_BRIDGE = re.compile(
    r"(?:据悉|周[一二三四五六日天]|本周|昨日|今日|当天|日前|近日|目前|"
    r"现|刚刚|累计|已经|已|正在|正|进一步|率先|发文|宣布|今天|首次|全新|"
    r"快速|陆续|还|也|分别|数周后|公告|升级|此前|新款|连续|规模化|开始)$"
)
# Product-owner discovery can see a compact Latin product immediately after a
# Chinese clause. In prose such as “晶泰科技通过参与IEEE…推出…”, the greedy
# Chinese owner capture may include the connector and leave a malformed
# canonical surface after the ordinary trailing-"与" cleanup. Strip only these
# unambiguous participation fragments.
_ACTION_CONNECTOR_TAIL = re.compile(r"(?:通过参(?:与)?|参加|参与)$")
_NON_ENTITY_TOKEN = re.compile(
    r"^(?:我们|双方|该公司|公司|企业|机构|项目|报告|消息|公告|事项|行动|"
    r"工作|市场|行业|赛道|领域|团队|客户|产品|技术|数据|平台|方案|"
    r"本轮融资资金|融资资金|主力产品性能|版本|模型|API|Flash|GPT|"
    r"产业|产业化|商业|计算|确定|努力|场景|附件|全面|实际|新增|任务|"
    r"活动|中试|国资|有序|历史性|准营|装置|编制|深化|结语|评标|"
    r"评标小组|招标方|投标方|开标地点|典型案例|解决方案应用|"
    r"技术攻关|规模化|正式版API|高效率|一切|点评|官方|共同|快速|陆续|"
    r"一键|数周后|今天|今日|目前|刚刚|首次|之一|去年秋季|上周|本周|集团|人工智能|机器人|"
    r"工业机器人|巡检机器人|自主|务实|收益|热量|营养信息|租赁|合并|扩建|包括|"
    r"部署|美业|级Agent|首款产品与|按时保质|本次|价格|填料|产能爬坡|"
    r"重整|全方位支持|营收规模及EBITDA|发行价格不低于定价基准日|"
    r"合计|总共|从而|除非|此次|此前|应用|原型|产品形态|技术架构|概念)$|"
    r"(?:报告|消息|事项|行动|工作|市场|行业|赛道|领域|团队|客户|产品|"
    r"技术|数据|平台|方案|能力|需求|流程|路径|版本|模型|工厂|基地|"
    r"案例|费用|地点|小组|投标方|招标方|简介|亮点|目标|举措|红利期)$"
)
_NON_ENTITY_GRAMMAR = re.compile(
    r"(?:现将|以下|以上|网友|相关|情况|如果|使其|能够|无法|仍|我国|"
    r"全国|各地|各单位|申报|须|需要|必须|提供|推进|加快|制定|实施|"
    r"率先用|新增|建设筹措|供应保障|主要目标|计划|将|称|表示|终止|进入|"
    r"用户|使用|押注|专注于|有望|或由|由前|披露|接任|创始合伙人|"
    r"正式版|发文|落地首单|宣布|发布|推出|完成|获得|上线|开源|签署|签订|达成)"
)
_EDITORIAL_ENTITY_FRAGMENT = re.compile(
    r"^(?:按照|根据|每个|第[一二三四五六七八九十\d]+条|这些|该|因此|此外|"
    r"另外|以及|通过|但|并|而|为|以|在|再到|成为|推动|提高|培育|形成|"
    r"深化|支持|帮助|生成|强调|想要|有多|本轮|此轮|融资|创投|资本|行业|"
    r"国内|地区|阶段|运营|研发|管理|担任|不再|最大出资人|是一家|本期|"
    r"核心|短期|有关|其选择|前担任|A股上市公司)"
)
_LEDGER_BRAND_SUFFIX = re.compile(
    r"(?:科技|智能|机器人|半导体|生物|医疗|电子|汽车|能源|材料)$"
)
_LEGAL_NAME = re.compile(
    r"[A-Za-z0-9\u4e00-\u9fff·（）() ]{2,64}?"
    r"(?:股份有限公司|有限责任公司|有限公司)"
)
_EXPLICIT_ALIAS = re.compile(
    r"(?P<legal>[A-Za-z0-9\u4e00-\u9fff·（）() ]{2,64}?"
    r"(?:股份有限公司|有限责任公司|有限公司))\s*[（(]"
    r"(?:以下简称|(?:企业)?简称)\s*[：:]?\s*[‘’“\"']?"
    r"(?P<alias>[A-Za-z0-9\u4e00-\u9fff·.+ -]{2,32}?)"
    r"[’‘”\"']?[）)]"
)
_CONTEXT_ALIAS = re.compile(
    r"(?P<left>[A-Za-z][A-Za-z0-9 .&+*-]{1,40}|"
    r"[\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff·.+* -]{1,31})\s*[（(]"
    r"(?P<inside>[A-Za-z][A-Za-z0-9 .&+*-]{1,40}|"
    r"[\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff·.+*-]{1,31})[）)]"
    r"(?:[，,]\s*(?:简称)?\s*[‘’“\"']?"
    r"(?P<short>[A-Za-z0-9\u4e00-\u9fff·.+ -]{2,32})[’‘”\"']?)?"
)
_LISTED_TICKER = re.compile(
    r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,32})\s*[（(]"
    r"(?P<ticker>\d{6})[）)]"
)
_ORGANIZATION_ROLE = re.compile(
    r"(?:^|[。！？；;\n，,、]|\s|与|和|及)\s*"
    r"(?P<name>[A-Za-z][A-Za-z0-9 .&+*-]{1,40}|"
    r"[\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff·.+*-]{1,31}?)"
    r"(?P<role>会长|董事长|高级副总裁|副总裁|总裁|CEO|首席执行官|创始人|联合创始人)"
)
_ENGLISH_GENERIC = re.compile(
    r"^(?:AI|API|GPT|LLM|CEO|VP|APP|Agent|Assistant|Model|Platform|System|"
    r"Lab|Labs|GPU|CPU|HBM|DRAM)$",
    re.IGNORECASE,
)
_COMPANY_REFERENCE = re.compile(
    r"(?:^|[。！？；;，,\s])"
    r"(?P<name>[A-Za-z][A-Za-z0-9 .&+*-]{1,40}|"
    r"[\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff·.+*-]{1,15}?)"
    r"(?=\s*(?:官方(?:微信公众号|账号|网站)?|方面|内部|旗下|"
    r"CEO|首席执行官|董事长|高级副总裁|副总裁|总裁|创始人|联合创始人))"
)
_ENGLISH_CONTEXT_TAIL = re.compile(
    r"(?:\s*(?:\u5728[^\uff0c,\u3002\uff01\uff1f\uff1b;\n]{1,24}(?:\u4e2d|\u4e0a))?\s*[\uff0c,:：]?\s*"
    r"(?:\u4eca\u5929|\u4eca\u65e5|\u5df2|\u6b63\u5f0f|\u6210\u529f|\u7d2f\u8ba1|\u521a\u521a)?"
    r"(?:\u5b8c\u6210|\u83b7\u5f97|\u83b7\u6279|\u5ba3\u5e03|\u53d1\u5e03|\u63a8\u51fa|\u4e0a\u7ebf|\u5f00\u6e90|\u7b7e\u7f72|\u7b7e\u8ba2|"
    r"\u8fbe\u6210|\u6295\u5efa|\u6269\u4ea7|\u6295\u4ea7|\u91cf\u4ea7|\u4ea4\u4ed8|\u53d1\u8d27|\u542f\u52a8|\u4e2d\u6807|\u589e\u8d44|\u6536\u8d2d|\u5e76\u8d2d|"
    r"\u878d\u8d44|\u52df\u8d44|\u6295\u8d44|\u5237\u65b0)|\u4ee5[^\u3002\uff01\uff1f\uff1b;\n]{1,24}\u5237\u65b0)"
)
_ASCII_NAME_SEPARATORS = frozenset(" .&+-")
_ASCII_NAME_MAX_CHARS = 80


def _iter_english_context_entities(
    text: str,
) -> Iterable[tuple[str, int, int]]:
    """Yield Latin surfaces followed by a concrete operating action.

    The legacy discovery expression above can backtrack quadratically on long
    ASCII runs.  This scanner keeps the same bounded, discovery-only intent
    while making work linear and failing closed on oversized tokens.
    """

    length = len(text)
    cursor = 0
    while cursor < length:
        character = text[cursor]
        if not ("A" <= character <= "Z" or "a" <= character <= "z"):
            cursor += 1
            continue
        start = cursor
        cursor += 1
        while (
            cursor < length
            and cursor - start < _ASCII_NAME_MAX_CHARS
            and (
                "A" <= text[cursor] <= "Z"
                or "a" <= text[cursor] <= "z"
                or "0" <= text[cursor] <= "9"
            )
        ):
            cursor += 1
        for _ in range(4):
            if cursor >= length or text[cursor] not in _ASCII_NAME_SEPARATORS:
                break
            segment_start = cursor + 1
            if segment_start >= length or not (
                "A" <= text[segment_start] <= "Z"
                or "a" <= text[segment_start] <= "z"
                or "0" <= text[segment_start] <= "9"
            ):
                break
            cursor = segment_start + 1
            while (
                cursor < length
                and cursor - start < _ASCII_NAME_MAX_CHARS
                and (
                    "A" <= text[cursor] <= "Z"
                    or "a" <= text[cursor] <= "z"
                    or "0" <= text[cursor] <= "9"
                )
            ):
                cursor += 1
        end = cursor
        if end - start > _ASCII_NAME_MAX_CHARS:
            cursor = start + 1
            continue
        context = text[end : min(length, end + 96)]
        if _ENGLISH_CONTEXT_TAIL.match(context):
            yield text[start:end], start, end
        # Preserve the permissive regex's ability to start inside a longer
        # ASCII token, while ensuring each bounded scan advances.
        cursor = max(start + 1, cursor)

_INTERNAL_REFERENCE = re.compile(
    r"在(?P<name>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,16}?)(?=内部)"
)
_LEADING_INTERNAL_REFERENCE = re.compile(
    r"(?:^|[。！？；;\n，,])"
    r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,16}?)(?=内部)"
)
_ATTACHED_LATIN_OWNER = re.compile(
    r"(?:^|[。！？；;，,\s])"
    r"(?P<name>[\u4e00-\u9fff]{2,10})(?=(?P<product>"
    r"(?!(?:APP|API|AI|Agent)\b)"
    r"[A-Z][A-Za-z0-9.+-]{1,30})"
    r"[^。！？；;\n]{0,80}(?:发布|推出|上线|升级|开启|测试|训练))"
)
# In Chinese copy, a generic descriptor is often glued directly to the real
# Latin owner: ``游戏引擎公司Unity中国发布``.  The older attached-owner rule
# correctly found the descriptor but made it the canonical company.  Capture
# the concrete Latin owner as a first-class surface so downstream events use
# ``Unity中国`` (or ``Unity``) rather than ``游戏引擎公司``.
_GENERIC_CJK_LATIN_OWNER = re.compile(
    r"(?:^|[。！？；;，,\s])"
    r"(?P<descriptor>[\u4e00-\u9fff]{2,10}(?:公司|企业|厂商|品牌))"
    r"(?P<owner>[A-Z][A-Za-z0-9.+-]{1,30}(?:中国|香港|日本|美国)?)"
    r"(?=[^。！？；;\n]{0,80}(?:发布|推出|上线|升级|开启|测试|训练))"
)
_AI_PRODUCT_OWNER = re.compile(
    r"(?:^|[。！？；;，,\s])"
    r"(?P<owner>[\u4e00-\u9fff]{2,8}?)(?:全场景)?"
    r"AI\s+Agent[^。！？；;\n]{0,48}(?:发布|推出|上线|升级)"
)
_NAMED_PRODUCT_OWNER = re.compile(
    r"(?:^|[。！？；;，,\s])"
    r"(?P<owner>[A-Za-z][A-Za-z0-9.+-]{1,20})面向"
    r"(?P<product>[A-Z][A-Za-z0-9.+-]{1,20})推出"
    r"[^。！？；;\n]{0,40}(?:模型|产品|平台|服务)"
)
_ORG_UNIT_OWNER = re.compile(
    r"(?:^|[。！？；;，,\s])"
    r"(?P<owner>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,16}?)(?:已|正式)?组建"
    r"(?P<product>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,12}?)(?:办公|研发|商业化|海外)?部门"
)
_OFFICIAL_PRODUCT_OWNER = re.compile(
    r"据(?P<owner>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,16})官方"
    r"[^。！？；;\n]{0,40}[，,]\s*"
    r"(?P<product>企业?[\u4e00-\u9fff]{2,8})(?:AI|智能)"
    r"[^。！？；;\n]{0,50}(?:开启|上线|发布|推出)"
)
_QUOTED_PRODUCT_OWNER = re.compile(
    r"[“\"](?P<product>[^”\"\n]{2,30})[”\"]\s*是"
    r"(?P<owner>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,20}?)"
    r"(?P<unit>\d{2,6})?(?:最新)?打造的?"
    r"[^。！？；;\n]{0,60}(?:产品|服务|平台|模型)"
)
_CORPORATE_PRODUCT_REFERENCE = re.compile(
    r"(?:^|[。！？；;，,\s‘’“\"'是由])"
    r"(?P<name>[\u4e00-\u9fff]{2,12}?)(?=\d{2,4}"
    r"\s*(?:最新)?(?:打造|开发|推出|发布))"
)
_DESCRIPTOR_ENGLISH_ALIAS = re.compile(
    r"(?:公司|企业|实验室)\s*[（(]"
    r"(?P<english>[A-Za-z][A-Za-z0-9 .&+*-]{1,48})"
    r"(?:[，,]\s*(?:简称)?\s*(?P<short>[A-Za-z][A-Za-z0-9.+*-]{1,16}))?"
    r"[）)]"
)
_OWNER_TAIL = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9 .&+*-]{1,30})面向"
    r"[A-Za-z0-9\u4e00-\u9fff·.+*-]{1,24}$"
)
_SUBJECT_NOUN_TAIL = re.compile(
    r"(?:的)?(?:首款|新款|核心|相关|主要|该)?"
    r"(?:产品|业务|项目|工厂|基地|平台|系统)$"
)
_OPERATIONAL_SUBJECT_TAIL = re.compile(
    r"^(?P<name>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,12}?)(?:开展|推进|聚焦|布局|面向)"
    r"(?:机器人|人工智能|智能制造|半导体|商业化|产业|业务|市场|场景).*$"
)
_INVESTMENT_PAIR = re.compile(
    r"(?:^|[。！？；;\n，,])\s*"
    r"(?P<investor>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,32})"
    r"(?:向|对)(?P<target>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,32}?)"
    r"(?:进行|完成)?(?:战略)?投资"
)
_VERSIONED_BRAND = re.compile(
    r"(?P<brand>[A-Z][A-Za-z]{2,20})(?:-[A-Z]?\d|\s+[A-Z]\d)"
)
_COMPANY_SUFFIX_SURFACE = re.compile(
    r"(?:^|[。！？；;\n，,、：:\s‘’“\"'（）()])"
    r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·+*-]{2,24}?"
    r"(?:科技|智能|机器人|半导体|电子|汽车|能源|材料|生物|医药|"
    r"光电|电气|通信|航空|工业|控股|集团|股份|资本))"
    r"(?=$|[。！？；;\n，,、：:\s‘’“\"'（）()]|与|和|及|在|已|将|也|方面)"
)
_INLINE_BILINGUAL_ENTITY = re.compile(
    r"(?:^|[。！？；;\n，,、：:\s]|(?:公司|企业|服务商|品牌))"
    r"(?P<cn>[\u4e00-\u9fff]{2,12})\s*"
    r"(?P<en>[A-Z][A-Za-z0-9]*(?:[ .&+-][A-Za-z0-9]+){0,4})"
    r"(?=\s*(?:宣布|完成|获得|发布|推出|融资|已|将|在|方面|[，,。；]))"
)
_REPRESENTATIVE_COMPANY = re.compile(
    r"(?:企业|公司)(?:代表|代表企业)?\s*"
    r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·+*-]{2,24})"
    r"(?=在|与|和|及|联合|已|宣布|发布|完成|获得)"
)
_HOSTING_PARTICIPANT = re.compile(
    r"(?:^|[。！？；;\n，,、：:\s由])"
    r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·+*-]{2,24}?)"
    r"(?:联合|共同)?(?:主办|承办)"
)
_HOSTING_BY_PARTICIPANT = re.compile(
    r"(?:^|[。！？；;\n，,:：])\s*(?:大会|活动|会议)?由"
    r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·+*-]{2,24}?)"
    r"(?=(?:联合|共同)?(?:主办|承办))"
)
_EXECUTIVE_TARGET_COMPANY = re.compile(
    r"(?:任命|聘任|加入|重返|接任|出任|担任)"
    r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,20}?)"
    r"(?:公司)?(?=(?:董事长|高级副总裁|副总裁|总裁|CEO|首席执行官|"
    r"总经理|管理层|团队|[。！？；;\n]))"
)
_SPEAKER_NAME = re.compile(
    r"(?:^|[。！？；;\n]|\s)(?P<name>[\u4e00-\u9fff·]{2,8})[：:]"
)
_BULLETIN_SUBJECT = re.compile(
    r"(?:^|[。！？；;\n，,])\s*(?:【[^】\n]{1,16}】\s*)?"
    r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,32})\s*[：:]"
    r"(?P<payload>[^。！？；\n]{0,180})"
)

# Entity discovery uses a deliberately narrower verb set than event discovery.
# In particular, bare 将/计划/正 are excluded because they frequently occur in
# headings and policy prose rather than after a company subject.
_DIRECT_ACTION = re.compile(
    r"(?:已|正式|成功|累计)?(?:完成|获得|获(?!悉)|获批|获准|获核准|宣布|发布|推出|"
    r"上线|公测|开源|签署|签订|达成|投建|扩产|投产|量产|交付|发货|"
    r"搭建|开发|研制|启动|落地|中标|承诺|增资|发行|回购|收购|并购|"
    r"组建|会面|进入|带来|展示|形成|建成|打造|共建|部署|覆盖|运转|"
    r"转让|终止|变更|筹划|募资|定增|定点|租赁|任命|聘任|上任|辞任)|"
    r"(?:拟|计划)(?=[^。！？；\n]{0,24}(?:融资|募资|发行|投资|投建|扩产|"
    r"建设|租赁|收购|并购|转让|回购|量产|交付))|"
    r"被[^。！？；\n]{0,24}?立案|收到[^。！？；\n]{0,24}?(?:处罚|告知书)|表示|称"
)
_PAYLOAD_OPERATION = re.compile(
    r"融资|募资|定增|完成|获得|获(?!悉)|获批|获准|获核准|宣布|发布|推出|上线|公测|开源|"
    r"签署|签订|达成|投建|扩产|投产|量产|交付|发货|搭建|开发|研制|"
    r"启动|落地|中标|订单|合同|承诺|增资|发行|回购|收购|并购|转让|"
    r"带来|展示|形成|建成|打造|共建|部署|覆盖|运转|"
    r"组建|会面|进入|"
    r"终止|变更|筹划|停牌|复牌|定点|租赁|投资|立案|处罚|任命|聘任|上任|辞任"
)


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    digit_map = str.maketrans("零〇一二三四五六七八九", "00123456789")
    normalized = re.sub(
        r"[零〇一二三四五六七八九]{3,}",
        lambda match: match.group(0).translate(digit_map),
        normalized,
    )
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalized)


def _kind(name: str) -> str:
    if re.search(r"(?:APP|App|app)$", name):
        return "product_or_service"
    if _NON_OPERATING_NAME_MARKER.search(name):
        return "facility_or_place"
    if _NON_OPERATING_SUFFIX.search(name):
        if re.search(r"(?:研究所|大学|学院|实验室)$", name):
            return "academic_or_research_body"
        if re.search(r"(?:政府|委员会|协会|管理局|办公室)$", name):
            return "public_body"
        return "facility_or_place"
    if _INVESTMENT_SUFFIX.search(name) or _INVESTMENT_NAME_MARKER.search(name):
        return "investment_institution"
    return "operating_company"


def _eligible_name(name: str) -> bool:
    basic_shape = bool(
        is_company_like(name)
        or re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff·+*.-]{2,12}", name)
    )
    return bool(
        basic_shape
        and not re.match(r"^(?:拟|计划|将|已|正|正在)", name)
        and not _GENERIC_NAME.fullmatch(name)
        and not _PLACEHOLDER_COMPANY.fullmatch(name)
        and not _JOINED_SUBJECT.fullmatch(name)
        and not _NON_ENTITY_TOKEN.search(name)
        and not _NON_ENTITY_GRAMMAR.search(name)
        and not _EDITORIAL_ENTITY_FRAGMENT.search(name)
        and not re.match(r"^.{2,12}公司.{2,12}$", name)
        and not re.search(r"(?:的|地|得|从|为|在|于|将|正|持续|首次)$", name)
        and _kind(name) == "operating_company"
    )


def _clean_legal_name(value: str, *, strip_enumerator: bool = False) -> str:
    cleaned = canonical_company_name(value)
    # Government appointment notices often place the personnel action directly
    # in front of a legal company name without punctuation.  The generic legal
    # name regex must not absorb that action/person prefix into the entity.
    cleaned = re.sub(
        r"^(?:(?:委派|推荐|建议)[\u4e00-\u9fff·]{2,8}(?:任|担任|不再担任)|"
        r"免去[\u4e00-\u9fff·]{2,8}的)",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"^.*(?:合伙人为|出资人由|管理人为|融资由|变更为|转让给|投资于)"
        r"(?=[A-Za-z0-9\u4e00-\u9fff·（）()]{2,64}"
        r"(?:股份有限公司|有限责任公司|有限公司)$)",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"^(?:以及|及)(?=[A-Za-z0-9\u4e00-\u9fff·（）()]{2,64}"
        r"(?:股份有限公司|有限责任公司|有限公司)$)",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"^.*(?:企业代表|公司代表|现场联合|联合|联同|包括|以及)"
        r"(?=[A-Za-z0-9\u4e00-\u9fff·（）()]{2,64}"
        r"(?:股份有限公司|有限责任公司|有限公司)$)",
        "",
        cleaned,
    )
    # Parent-company prose is not part of the child legal name:
    # “奇瑞控股集团旗下安徽奇瑞智能科技有限公司”.
    cleaned = re.sub(
        r"^.*旗下(?=[A-Za-z0-9\u4e00-\u9fff·（）()]{2,64}"
        r"(?:股份有限公司|有限责任公司|有限公司)$)",
        "",
        cleaned,
    )
    if strip_enumerator:
        cleaned = re.sub(r"^(?:由|联合|以及|包括|并由|联同)", "", cleaned)
    # Editorial descriptions sometimes sit immediately in front of the legal
    # entity without punctuation: “某领域领军企业北京甲有限公司”.
    cleaned = re.sub(
        r"^.{0,48}(?:领军企业|代表企业|初创企业|创业企业)", "", cleaned
    )
    return canonical_company_name(cleaned)


def _clean_subject(value: str) -> str:
    candidate = value.strip(" \t\r\n，。；：:,、")
    candidate = _MEDIA_LEAD.sub("", candidate)
    candidate = candidate.strip(" \t\r\n，。；：:,、‘’“\"'（）()【】")
    candidate = re.sub(r"^(?:\d{1,3}[、.)）]|[（(][一二三四五六七八九十]+[）)])\s*", "", candidate)
    candidate = _REPORTING_LEAD.sub("", candidate)
    candidate = re.sub(r"^(?:并由|由|联同)", "", candidate)
    candidate = re.sub(r"^(?:另一位是|一位是|受访者是|来自)", "", candidate)
    candidate = _SUBJECT_PREFIX.sub("", candidate)
    candidate = re.sub(r"(?:据悉|报道称|消息称).*$", "", candidate)
    candidate = re.sub(
        r"(?<=[A-Za-z0-9])在(?:API|平台|系统|官网|社交媒体平台).*$",
        "",
        candidate,
    )
    candidate = re.sub(
        r"(?<=[A-Za-z0-9\u4e00-\u9fff])(?:CEO|首席执行官|董事长|会长|总经理|"
        r"创始人|联合创始人|负责人).*$",
        "",
        candidate,
    )
    location = re.fullmatch(
        r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·+*-]{2,12}?)(?:刚刚|也)?在"
        r"[\u4e00-\u9fff]{2,18}",
        candidate,
    )
    if location:
        candidate = location.group("name")
    previous = ""
    while candidate and previous != candidate:
        previous = candidate
        candidate = _SUBJECT_BRIDGE.sub("", candidate).strip()
    # Editorial action clauses can leave a future/sequence tail attached to
    # the company surface. Remove only these bounded tails; the action remains
    # in the evidence span.
    candidate = re.sub(
        r"(?:\u4e5f\u987a\u52bf|\u4e5f\u76f8\u7ee7|\u987a\u52bf|\u76f8\u7ee7|\u4e5f\u5c06|\u5373\u5c06|\u968f\u540e|\u76ee\u524d|\u5df2\u7ecf|\u5df2)$",
        "",
        candidate,
    ).strip()
    candidate = _ACTION_CONNECTOR_TAIL.sub("", candidate).strip()
    candidate = re.sub(r"^(?:公司|企业)", "", candidate).strip()
    candidate = re.sub(r"^(?:代表|企业代表|公司代表)", "", candidate).strip()
    candidate = re.sub(r"(?:内部|方面)$", "", candidate).strip()
    institution_origin = re.fullmatch(
        r"(?P<name>[A-Za-z0-9\u4e00-\u9fff·.+*-]{2,20}?(?:资本|基金|创投))"
        r"由.{2,48}(?:集团|公司)(?:内部[^。！？；\n]{0,24})?",
        candidate,
    )
    if institution_origin:
        candidate = institution_origin.group("name")
    owner_tail = _OWNER_TAIL.fullmatch(candidate)
    if owner_tail:
        candidate = owner_tail.group("name")
    operational_tail = _OPERATIONAL_SUBJECT_TAIL.fullmatch(candidate)
    if operational_tail:
        candidate = operational_tail.group("name")
    candidate = _SUBJECT_NOUN_TAIL.sub("", candidate).strip()
    candidate = re.sub(
        r"[（(][A-Za-z][A-Za-z0-9 .&+*-]{1,40}$", "", candidate
    ).strip()
    candidate = re.sub(
        r"(?:拟与|拟和|以及|并与|并和|拟|与|和|及)$", "", candidate
    ).strip()
    if "的" in candidate and len(candidate.rsplit("的", 1)[-1]) >= 2:
        candidate = candidate.rsplit("的", 1)[-1]
    # A few Chinese brands legitimately end in a numeral plus “天” (for
    # example 华大九天).  The shared editorial normalizer treats that shape as
    # a trailing duration, so preserve it when the whole direct subject is a
    # compact brand-shaped token.
    if re.fullmatch(
        r"[\u4e00-\u9fff·]{2,8}[一二三四五六七八九十百两]{1,2}天",
        candidate,
    ):
        return candidate
    return _clean_legal_name(candidate)


def _left_clause(text: str, action_start: int) -> tuple[str, int]:
    window_start = max(0, action_start - 96)
    prefix = text[window_start:action_start]
    hard_boundary = max(prefix.rfind(mark) for mark in "。！？；;\n")
    clause_start = hard_boundary + 1
    clause = prefix[clause_start:]
    soft_boundary = max(clause.rfind(mark) for mark in "，,:：")
    if soft_boundary < 0:
        start = window_start + clause_start
        return text[start:action_start], start
    tail = clause[soft_boundary + 1 :]
    if tail.strip():
        start = window_start + clause_start + soft_boundary + 1
        return text[start:action_start], start
    previous_soft = max(
        clause.rfind(mark, 0, soft_boundary) for mark in "，,:："
    )
    start = window_start + clause_start + previous_soft + 1
    return text[start:action_start], start


def _split_subjects(value: str) -> list[str]:
    # A bare “和” is common inside Chinese brands (for example 芯和半导体).
    if re.search(r"(?:、|与|联合|携手|,|，)", value):
        parts = [
            item.strip()
            for item in re.split(r"(?:、|与|联合|携手|,|，)", value)
        ]
        if all(2 <= len(item) <= 32 for item in parts):
            return parts
    return [value]


def _descriptor_anchors_subject(raw_left: str, cleaned: str) -> bool:
    """Whether a company descriptor is immediately adjacent to the subject."""

    compact_left = re.sub(r"\s+", "", raw_left)
    compact_name = re.sub(r"\s+", "", cleaned).strip("「」“”\"'（）()")
    if not compact_name:
        return False
    position = compact_left.rfind(compact_name)
    if position < 0:
        return False
    prefix = compact_left[:position].rstrip("「」“”\"'（）()，,:：")
    return bool(
        re.search(
            r"(?:公司|企业|企业代表|品牌|制造商|开发商|运营商|服务商|"
            r"解决方案提供商|研发商)$",
            prefix,
        )
    )


def _strong_entity_shape(name: str, *, source: str, occurrences: int) -> bool:
    if not _eligible_name(name):
        return False
    if source == "reporting_subject":
        return False
    if _ENGLISH_GENERIC.fullmatch(name):
        return False
    if source in {
        "legal_name",
        "explicit_alias",
        "context_alias",
        "listed_ticker",
        "bulletin_subject",
        "title_bulletin",
        "company_context",
        "organization_role",
        "english_context",
        "company_reference",
        "attached_product_owner",
        "corporate_product_reference",
        "descriptor_alias",
        "company_surface",
        "inline_bilingual_entity",
    }:
        return True
    if source == "reporting_operational_subject":
        return bool(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+-]{1,30}", name)
            or re.search(
                r"(?:科技|智能|集团|股份|电子|汽车|能源|材料|"
                r"机器人|半导体|生物|医药|航空|工业|云)$",
                name,
            )
        )
    if source == "compact_action_subject":
        return bool(
            re.fullmatch(r"[\u4e00-\u9fff·]{3,10}", name)
            and not _COMPACT_PRODUCT_SUFFIX.search(name)
        )
    if re.search(r"(?:股份有限公司|有限责任公司|有限公司)$", name):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z .&+-]{1,30}", name):
        return True
    if (
        re.search(r"[A-Za-z]", name)
        and re.search(r"[\u4e00-\u9fff]", name)
        and not re.search(r"\d", name)
        and len(name) <= 20
    ):
        return True
    if re.search(
        r"(?:科技|智能|集团|证券|汽车|钢铁|风能|检测|机器人|半导体|电子|"
        r"药业|医疗|能源|材料|航天|系统|股份|生物|光电|电气|通信|"
        r"控股|产融|洗霸|航空|工业|云)$",
        name,
    ):
        return True
    return bool(
        source == "title_action"
        and occurrences >= 1
        and 2 <= len(name) <= 16
        and re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff·+*.-]+", name)
    )


_ENTITY_SEMANTIC_NOISE = re.compile(
    r"(?:本次|此次|首轮|融资|募资|估值|患者|临床|疗效|研发|产品|大模型|"
    r"项目|平台|体系|场景|方案|能力|价值|需求|行业|产业|市场|"
    r"商业|规模化|全球|今年|目前|其中|首发|首款|核心|持续|"
    r"加速|推进|部署|填料|判断|命题|教授|院士|科研|董事长兼|"
    r"深度融合|转化医学|疗效探索|顺利联动|赋予|早日|多条|"
    r"关键跨越|自主创新|务实落地|逐步构建|携手|联合|表明|"
    r"缓解先天|依托自研|团队已提前|基因维度|技术架构|难以|"
    r"这标志着|取决于|有望|力争|用于|面向|聚焦|展示|覆盖)"
)
_CONCEPT_OR_ROLE_NAME = re.compile(
    r"^(?:AI药物|生物|半导体|智能|具身智能|人形机器人|仿生机器人|"
    r"具身智能机器人|手术机器人|轮式服务机器人|工业机器人|"
    r"包括机器人|导览这些机器人|机器人开始做了这样|"
    r"股东阵营|独家财务顾问|长期独家财务顾问|后续财务顾问|"
    r"创始人兼|董事长兼|对话|构建|复现验证以及|"
    r"上市首日涨幅|美股IPO平均|A股IPO平均)$"
)
_KNOWN_NON_OPERATING_NAMES = re.compile(
    r"^(?:投资界|动脉网|证券时报|财联社|每日经济新闻|中国科学院)$"
)
_KNOWN_INVESTOR_BRAND = re.compile(
    r"^(?:a16z|Accel|Sequoia|红杉资本|经纬创投|启明创投|高瓴资本|"
    r"DST Global|GGV Capital)$",
    re.I,
)
# These are context classes, not a hard-coded company blacklist.  They are
# used only when a surface is introduced as a participant, policy body, fund,
# or person rather than as the subject of an operating-company event.
_INVESTMENT_CONTEXT = re.compile(
    r"(?:\u6295\u8d44|\u8d44\u672c|\u57fa\u91d1|\u8d44\u7ba1|\u8d44\u4ea7\u7ba1\u7406|\u80a1\u6743|\u521b\u6295|\u91cd\u6295|\u6295\u63a7|\u56fd\u8d44|\u6bcd\u57fa\u91d1|\u5408\u4f19\u4eba|\u51fa\u8d44|\u9886\u6295|\u8ddf\u6295|\u52df\u8d44|\u9996\u5173|\u6295\u540e|LP|GP)",
    re.I,
)
_PUBLIC_CONTEXT = re.compile(
    r"(?:\u653f\u5e9c|\u53d1\u6539|\u5de5\u4fe1|\u7701\u59d4|\u5e02\u59d4|\u56fd\u8d44|\u65b0\u95fb\u529e|\u7ecf\u5f00\u533a|\u5f00\u53d1\u533a|\u56ed\u533a|\u79d1\u521b\u5e73\u53f0|\u4f1a\u5c55|\u5c55\u4f1a|\u4e3b\u529e|\u627f\u529e|\u9ad8\u6821|\u9662\u6240|\u534f\u4f1a|\u4eba\u624d\u53d1\u5c55|\u4eba\u624d\u96c6\u56e2|\u6295\u8d44\u53d1\u5c55|\u6295\u63a7|\u91cd\u6295)",
)
_PERSON_OR_EDITORIAL_CONTEXT = re.compile(
    r"(?:\u638c\u95e8\u4eba|\u521b\u59cb\u4eba|\u8054\u5408\u521b\u59cb\u4eba|\u526f\u603b\u88c1|\u603b\u88c1|\u8d1f\u8d23\u4eba|\u53d1\u8a00\u4eba|\u8bb0\u8005|\u70b9\u8d5e|\u8f6c\u6587|\u79f0|\u8868\u793a)",
)
_NON_OPERATING_NAME_MARKER = re.compile(
    r"(?:\u4f1a\u5c55|\u5c55\u89c8|\u5c55\u4f1a|\u4f1a\u8bae|\u4eba\u624d\u53d1\u5c55|\u4eba\u624d\u96c6\u56e2|\u6295\u8d44\u53d1\u5c55)$",
)
_INVESTMENT_NAME_MARKER = re.compile(
    r"(?:\u6295\u8d44|\u8d44\u672c|\u57fa\u91d1|\u8d44\u7ba1|\u8d44\u4ea7\u7ba1\u7406|\u80a1\u6743|\u521b\u6295|\u91cd\u6295|\u6295\u63a7|\u56fd\u8d44|\u6bcd\u57fa\u91d1)$",
)
_NON_OPERATING_SURFACE = re.compile(
    r"(?:\u4f1a\u5c55|\u5c55\u89c8|\u5c55\u4f1a|\u4f1a\u8bae|\u4eba\u624d\u53d1\u5c55|\u4eba\u624d\u96c6\u56e2|\u6295\u8d44\u53d1\u5c55)",
)
_HARDTECH_CONTEXT = re.compile(
    r"(?:\u534a\u5bfc\u4f53|\u82af\u7247|\u673a\u5668\u4eba|\u4eba\u5de5\u667a\u80fd|\u5927\u6a21\u578b|\u91cf\u5b50|\u6838\u805a\u53d8|\u822a\u7a7a|\u822a\u5929|\u536b\u661f|\u65e0\u4eba\u673a|\u5de5\u4e1a|\u80fd\u6e90|\u6750\u6599|\u7535\u5b50|\u7535\u6c14|\u5149\u7535|\u7b97\u529b|\u81ea\u52a8\u9a7e\u9a76|\u901a\u4fe1|\u8f6f\u4ef6|\u4fe1\u606f)",
)
_FINANCIAL_RELATION_CONTEXT = re.compile(
    r"(?:LP|GP|\u5408\u4f19\u4eba|\u51fa\u8d44|\u57fa\u91d1|\u52df\u8d44|\u9996\u5173|\u80a1\u6743\u8f6c\u8ba9|\u53c2\u80a1\u516c\u53f8)",
    re.I,
)
_COMPANY_SHAPED_ENDING = re.compile(
    r"(?:科技|智能|集团|股份|电子|汽车|能源|材料|机器人|半导体|"
    r"生物|医药|医疗|药业|光电|电气|通信|航空|工业|核电|控股|云)$"
)
_DIRECT_CLAUSE_LEAD = re.compile(
    r"^(?:(?:近日|日前|近期|目前|今日|今天|昨日|本月|今年|同时|此外|"
    r"刚刚|据悉|消息称|报道称|公告显示|资料显示|随后|其中)"
    r"[\s，,:：]*)*$"
)
_SHORT_SUBJECT_ACTION = re.compile(
    r"(?:完成|获得|获批|宣布|发布|推出|上线|开源|签署|签订|达成|投建|扩产|投产|"
    r"量产|交付|发货|启动|中标|增资|收购|并购|组建|进入|建成|打造|共建|部署|"
    r"推进|承诺|表示|投资|变更|获核准|融资|招聘|设立|成立)"
)
_SHORT_SUBJECT_PREFIX = re.compile(
    r"^(?:(?:近日|日前|近期|目前|今天|今日|昨日|据悉|据报道|记者获悉|此外|其中|"
    r"与此同时|当地时间)|[0-9年月日时分:\-\s,，、]*)*$"
)

# Digest articles frequently flatten an item heading and its first sentence
# into one line (for example ``字节跳动今天发布...`` or
# ``OpenAI在API中推出...``).  The normal clause parser deliberately refuses
# to trust that flattened prefix because it can contain the previous item's
# title.  This narrower scanner looks only for a bounded brand-shaped token
# immediately followed by a strong operating action.  It is a discovery aid;
# eligibility still applies the same noise, product-owner, and company-scope
# checks below.
_COMPACT_ACTION = (
    r"(?:完成|获得|获批|宣布|发布|推出|上线|开源|投建|扩产|投产|量产|"
    r"交付|发货|启动|中标|增资|收购|并购|组建|建成|打造|共建|部署|"
    r"推进|扩建|重返|投资|变更|获核准|融资|招聘|设立|成立|升级|刷新|"
    r"发文|上任|辞任|表示|称)"
)
_COMPACT_BRIDGE = (
    r"(?:今天|今日|昨日|当天|当地时间|今年|本月|近日|日前|近期|据悉|据报道|"
    r"记者获悉|此外|其中|与此同时|正式|公告|发文|称|表示|内部|旗下|目前|"
    r"已经|已|刚刚|正|正在|还|也|并|将|以[^。！？；;\n]{1,24}?|"
    r"向[^。！？；;\n]{1,24}?|为[^。！？；;\n]{1,24}?|"
    r"面向[A-Za-z0-9 .&+*-]{1,32}|在[A-Za-z0-9 .&+*-]{1,32}|"
    r"联合[A-Za-z0-9 .&+*-]{1,32}|[0-9年月日时分:.\-]{1,20})"
)
_COMPACT_ACTION_SUBJECT = re.compile(
    rf"(?<![A-Za-z0-9\u4e00-\u9fff])"
    rf"(?P<subject>(?:[A-Z][A-Za-z0-9]*(?:[ .&+*-][A-Za-z0-9]+){{0,3}}|"
    rf"[\u4e00-\u9fff·]{{2,10}}))"
    rf"\s*"
    rf"(?P<bridge>(?:{_COMPACT_BRIDGE}){{0,5}})"
    rf"(?P<action>{_COMPACT_ACTION})"
)
_COMPACT_SUBJECT_SUFFIX_NOISE = re.compile(
    r"(?:今天|今日|昨日|当天|当地时间|正式|公告|发文|称|表示|内部|旗下|目前|"
    r"已经|已|刚刚|正|正在|将|以|向|为|还|全场景|新款|升级|联合)$"
)
_COMPACT_SUBJECT_WEAK_TAIL = re.compile(r"(?:同时|称|表示|发文)$")
_COMPACT_SUBJECT_NOISE = re.compile(
    r"^(?:为你|强调|通过|目前|当前|这|现在|重点|第二季|能否|开放|去年|"
    r"可通过|并|而|但|其中|公司|该|总共|这起|正在|报道|计划|包括|以及|"
    r"随后|用户|官方|双方|每|已|已经|重点面|商业|前沿模型|资本估值锚|自主|"
    r"受害者|不过|老板|员工|创业者|发展|坪山|时代周报|记者|抓好|认真|"
    r"助力|国务院|新闻办|发生|此前|也是|沿黄|烟台|山东省委|工信部|"
    r"锚定|全面|建设)"
)
_COMPACT_EXTRA_NOISE = re.compile(
    r"^(?:\u4f9d\u7136\u80fd|\u53d1\u751f\u5de5\u5546|\u662f\u5168\u9762|\u5efa\u5f3a\u533a\u5e02|"
    r"\u4e0e\u9ad8\u6821\u9662\u6240|\u6293\u597d\u7f51\u7edc\u5efa\u8bbe|"
    r"\u56fd\u52a1\u9662\u65b0\u95fb\u529e\u4e3e\u884c\u65b0\u95fb|\u627f\u529e\u5355\u4f4d|"
    r"\u4e3b\u529e\u5355\u4f4d)$"
)
_COMPACT_PRODUCT_OWNER_ACTION = re.compile(
    rf"(?:发布|推出|上线|升级|开启|测试|开源|组建|成立|招聘|部署|"
    rf"{_COMPACT_ACTION})"
)
_COMPACT_PRODUCT_SUFFIX = re.compile(
    r"(?:文档|办公|助手|智能体|平台|模型|产品|服务|功能|工具|版本|注册权|"
    r"规划|全栈|硬件|软件|算法|部署|应用|系统|部门|方案|方向)$"
)
_COMPACT_REPORTING_OPERATION = re.compile(
    r"(?:表示|称)[^。！？；;\n]{0,80}(?:封禁|阻断|关闭|分享|共享|采取措施|"
    r"提高|降低|发布|推出|上线|开源|完成|获得|投资|融资|组建|成立|招聘)"
)


def _direct_clause_company_anchor(
    *,
    text: str,
    title: str,
    clause_start: int,
    action_start: int,
    raw_start: int,
    cleaned: str,
    descriptor_context: bool,
    joined_subject: bool,
) -> bool:
    """Return whether a direct-action parse is itself a company anchor.

    Discovery remains broad, but eligibility can only inherit from an action
    parse when the candidate is the clean grammatical subject at a clause
    boundary.  This prevents objects and editorial fragments from becoming
    companies merely because an action verb appears later in the sentence.
    """

    if raw_start < clause_start or not _eligible_name(cleaned):
        return False
    if (
        _ENTITY_SEMANTIC_NOISE.search(cleaned)
        or _KNOWN_NON_OPERATING_NAMES.fullmatch(cleaned)
        or _INVESTMENT_INSTITUTION_NAME.search(cleaned)
    ):
        return False
    prefix = text[clause_start:raw_start].strip(" \t\r\n，,:：‘’“\"'（）()【】")
    suffix = text[raw_start + len(cleaned) : action_start].strip()
    company_shaped = bool(
        _COMPANY_SHAPED_ENDING.search(cleaned)
        or (
            re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{1,40}", cleaned)
            and not _ENGLISH_GENERIC.fullmatch(cleaned)
        )
        or (
            re.search(r"[A-Za-z]", cleaned)
            and re.search(r"[\u4e00-\u9fff]", cleaned)
            and len(cleaned) <= 24
        )
    )
    if descriptor_context and company_shaped:
        return True
    if joined_subject:
        # A joined clause may leave the other subject in prefix/suffix.  Bare
        # Chinese personal-name-shaped tokens still require title support.
        return bool(
            company_shaped
            or (
                re.fullmatch(r"[\u4e00-\u9fff·]{2,8}", cleaned)
                and (
                    cleaned in title
                    or len(text) <= 160
                )
            )
        )
    if suffix and not re.fullmatch(
        r"(?:(?:官方|方面|内部|今日|今天|昨日|目前|近日|日前|近期|"
        r"刚刚|已经|已|正|正在|也|还|累计|首次|全新|开始|规模化)\s*)+",
        suffix,
    ):
        return False
    if prefix and not _DIRECT_CLAUSE_LEAD.fullmatch(prefix):
        return False
    if company_shaped:
        return True
    return bool(
        re.fullmatch(r"[\u4e00-\u9fff·]{2,8}", cleaned)
        and (
            cleaned in title
            or len(text) <= 160
        )
    )


def _short_subject_has_operational_clause(
    record: Mapping[str, Any],
    *,
    body: str,
) -> bool:
    """Require a short brand to be a clause subject of a concrete action."""

    allowed_sources = {
        "action_subject",
        "direct_clause_company",
        "company_context",
        "reporting_operational_subject",
        "english_context",
    }
    surfaces = tuple(
        dict.fromkeys(
            str(surface)
            for surface in (
                record.get("canonical_name", ""),
                *tuple(record.get("aliases", ())),
            )
            if len(str(surface)) >= 2
        )
    )
    positions: list[tuple[int, int, str]] = []
    for mention in record.get("mentions", ()):
        if (
            mention.region == "body"
            and mention.discovery_source in allowed_sources
            and mention.char_start >= 0
            and mention.char_end > mention.char_start
        ):
            positions.append((mention.char_start, mention.char_end, mention.text))
    # The direct-action parser can miss a clean short subject when an earlier
    # clause contains a descriptor.  Re-scan the exact canonical/alias surface
    # at clause boundaries; this is still deterministic and bounded, unlike a
    # global company-name lookup.
    for surface in surfaces:
        positions.extend(
            (match.start(), match.end(), surface)
            for match in re.finditer(re.escape(surface), body)
        )
    seen: set[tuple[int, int]] = set()
    for start, end, _text in positions:
        if (start, end) in seen:
            continue
        seen.add((start, end))
        clause_start = max(
            body.rfind(mark, 0, start) for mark in "。！？；;\n"
        ) + 1
        prefix = body[clause_start:start].strip(" \t,，、：:")
        if not _SHORT_SUBJECT_PREFIX.fullmatch(prefix):
            continue
        clause_tail = body[end : end + 96]
        clause_tail = re.split(r"[。！？；;\n]", clause_tail, maxsplit=1)[0]
        if _SHORT_SUBJECT_ACTION.search(clause_tail):
            return True
    return False


def _compact_subject_name(raw: str) -> str:
    """Normalize a bounded flattened-heading subject, failing closed."""

    candidate = _clean_subject(raw)
    previous = ""
    while candidate and previous != candidate:
        previous = candidate
        # A digest heading may flatten ``公司旗下新成立...`` into the token
        # captured before the action. Remove this connective before applying
        # the ordinary suffix-noise loop; do not strip a standalone brand that
        # merely happens to end in ``新``.
        candidate = re.sub(r"旗下新$", "", candidate)
        candidate = _COMPACT_SUBJECT_SUFFIX_NOISE.sub("", candidate).strip()
    if not candidate or len(candidate) > 40:
        return ""
    if _COMPACT_SUBJECT_WEAK_TAIL.search(raw):
        return ""
    if _COMPACT_SUBJECT_NOISE.search(candidate) or _COMPACT_EXTRA_NOISE.search(
        candidate
    ):
        return ""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]*", candidate) and re.search(
        r"\d", candidate
    ):
        return ""
    # Two-character Chinese strings are overwhelmingly products, departments,
    # or generic labels in flattened digests.  They can still be recovered via
    # an explicit owner/product relation; this scanner only promotes a longer
    # company-shaped subject (or an unambiguous Latin brand).
    if re.fullmatch(r"[\u4e00-\u9fff·]+", candidate):
        if not 3 <= len(candidate) <= 10:
            return ""
    if (
        not _eligible_name(candidate)
        or _ENTITY_SEMANTIC_NOISE.search(candidate)
        or _NON_ENTITY_GRAMMAR.search(candidate)
        or _EDITORIAL_ENTITY_FRAGMENT.search(candidate)
    ):
        return ""
    if _ENGLISH_GENERIC.fullmatch(candidate):
        return ""
    return candidate


_INLINE_CN_SUFFIX_NOISE = re.compile(
    r"(?:今天|今日|昨日|正式|公告|发文|称|表示|内部|旗下|目前|已经|已|刚刚|"
    r"正|正在|还|也|并|发布|推出|上线|升级|联合)$"
)
_INLINE_CN_PREFIX_NOISE = re.compile(
    r"^(?:去年|今年|上周|本周|目前|当前|已发布|可通过|通过|相比|他希望|我们|"
    r"强调|马斯克称|因此|这起|三步|收割|话术|受害者|虚假)"
)


def _inline_bilingual_chinese_surface(raw: str) -> str:
    """Keep only a clean Chinese surface from a concatenated CN/Latin name.

    Aggregators often place an editorial lead or an action phrase immediately
    before a Latin product (``去年秋季OpenAI``). Treating that lead as the
    Chinese entity corrupts the canonical record and hides the actual Latin
    company behind an alias. Trim bounded action suffixes and reject obvious
    temporal/editorial prefixes before the bilingual alias is attached.
    """

    candidate = _clean_subject(raw)
    previous = ""
    while candidate and candidate != previous:
        previous = candidate
        candidate = _INLINE_CN_SUFFIX_NOISE.sub("", candidate).strip()
    if not candidate or _INLINE_CN_PREFIX_NOISE.search(candidate):
        return ""
    if _ENTITY_SEMANTIC_NOISE.search(candidate) or _NON_ENTITY_GRAMMAR.search(
        candidate
    ):
        return ""
    return candidate


def _discover_compact_action_subjects(
    *,
    article: CleanArticle,
    add: Any,
) -> None:
    """Add exact subjects from flattened digest headings.

    The callback is the local ledger ``add`` function.  Keeping discovery in a
    separate helper makes the rule easy to audit and prevents the broad regex
    from changing canonicalization or merge order elsewhere in the ledger.
    """

    body = article.clean_body
    for match in _COMPACT_ACTION_SUBJECT.finditer(body):
        raw_subject = match.group("subject")
        subject = _compact_subject_name(raw_subject)
        if not subject:
            continue
        raw_start = match.start("subject")
        # A cleaned subject can only be a prefix of the bounded raw token; do
        # not search globally and accidentally attach a later duplicate.
        if not raw_subject.startswith(subject):
            continue
        add(
            subject,
            "compact_action_subject",
            start=raw_start,
            end=raw_start + len(subject),
        )


def _record_has_operating_anchor(
    record: Mapping[str, Any],
    *,
    body: str,
    title: str,
) -> bool:
    """Return whether a discovery record has an independent company seed.

    Lexical surfaces, action subjects, products and scope hints are deliberately
    discovery-only.  They may merge into a grounded seed, but they cannot make
    themselves eligible by reinforcing one another.
    """

    canonical = str(record["canonical_name"])
    sources = set(record["sources"])
    if _ENGLISH_GENERIC.fullmatch(canonical):
        return False
    if "bulletin_unit_company" in sources and _eligible_name(canonical):
        item_action = re.compile(
            re.escape(canonical)
            + r"[^\u3002\uff01\uff1f\uff1b;\n]{0,96}"
            r"(?:\u81ea\u4e3b\u7814\u53d1|\u91cf\u4ea7|\u5b98\u5ba3|\u5ba3\u5e03|\u5b8c\u6210|\u53d1\u5e03|\u63a8\u51fa|\u83b7\u5f97|\u878d\u8d44|\u6269\u5efa|\u5c06\u5728|\u65b0\u589e|\u4ea4\u4ed8|\u5ba3\u5e03)"
        )
        if item_action.search(body):
            return True
    all_surfaces = tuple(
        dict.fromkeys(
            surface
            for surface in (canonical, *tuple(record.get("aliases", ())))
            if len(str(surface)) >= 2
        )
    )
    compact_surfaces = tuple(
        surface
        for surface in all_surfaces
        if _compact_subject_name(str(surface))
    )
    def surface_windows(surface: str, *, before: int = 72, after: int = 96):
        for match in re.finditer(re.escape(str(surface)), body, flags=re.I):
            yield body[
                max(0, match.start() - before) : min(len(body), match.end() + after)
            ]

    def has_context(pattern: re.Pattern[str], *, before: int = 72, after: int = 96):
        return any(
            pattern.search(window)
            for surface in all_surfaces
            for window in surface_windows(str(surface), before=before, after=after)
        )

    def has_primary_context(
        pattern: re.Pattern[str], *, before: int = 72, after: int = 96
    ):
        return any(
            pattern.search(window)
            for window in surface_windows(canonical, before=before, after=after)
        )

    def has_local_fund_vehicle_context() -> bool:
        """Match fund-vehicle wording in the same sentence as the surface."""
        for surface in all_surfaces:
            for match in re.finditer(re.escape(str(surface)), body, flags=re.I):
                window = body[
                    max(0, match.start() - 40) : min(len(body), match.end() + 96)
                ]
                if re.search(
                    r"(?:基金|募资|首关|(?<![A-Za-z])(?:LP|GP)(?![A-Za-z])|出资人|合伙人|私募股权|创投机构)",
                    window,
                    flags=re.I,
                ):
                    return True
        return False

    def has_local_operating_action_context() -> bool:
        """Find an action sentence that is not itself a fund-vehicle clause."""
        action_pattern = re.compile(
            r"(?:宣布|发布|推出|上线|完成|获得|扩建|交付|签署|设立|新增|组建|"
            r"融资|扩产|投建|成立|刷新|披露|承诺|投入|支出|申购|挂牌)"
        )
        fund_pattern = re.compile(
            r"(?:基金|募资|首关|(?<![A-Za-z])(?:LP|GP)(?![A-Za-z])|出资人|合伙人|私募股权|创投机构)",
            re.I,
        )
        for surface in all_surfaces:
            for match in re.finditer(re.escape(str(surface)), body, flags=re.I):
                window = body[
                    max(0, match.start() - 40) : min(len(body), match.end() + 96)
                ]
                if action_pattern.search(window) and not fund_pattern.search(window):
                    return True
        return False

    # A Latin surface introduced by a fund-vehicle event is not an
    # operating-company lead merely because the compact/direct scanner sees
    # the action verb before it. SevenX completing a fund first close is the
    # canonical example: the action is real, but the subject is the vehicle.
    # Keep this relation rule generic and preserve explicit investment targets.
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{2,40}", canonical)
        and has_local_fund_vehicle_context()
        and not has_local_operating_action_context()
        and not re.search(
            r"(?:科技|智能|机器人|半导体|电子|电气|航空|航天|量子|核|能源|材料|芯|光电|工业|软件|信息|通信|算力|自动驾驶)",
            canonical,
            re.I,
        )
        and not sources
        & {
            "investment_target_company",
            "descriptor_alias",
            "descriptor_company",
            "descriptor_context_alias",
            "investment_actor_company",
        }
    ):
        return False

    # Hard exclusions that are unambiguous from the local grammar.  They are
    # intentionally relation-based rather than a list of company names.
    if re.match(r"^(?:\u627f\u529e\u5355\u4f4d|\u4e3b\u529e\u5355\u4f4d)", canonical):
        return False
    for surface in all_surfaces:
        for window in surface_windows(str(surface), before=8, after=48):
            if re.search(
                rf"{re.escape(str(surface))}\s*(?:与|和|及)[^。！!；;\n]{{0,20}}"
                r"(?:宣布|发布|完成|签署|扩建|投资)",
                window,
            ) and not re.search(
                rf"{re.escape(str(surface))}\s*(?:宣布|发布|完成|签署|扩建|投资)",
                window,
            ):
                # A coordinated participant can be a customer or investor,
                # but is not automatically the operating-company subject.
                if (
                    not sources
                    & {
                    "action_subject",
                    "compact_action_subject",
                    "company_surface",
                    "investment_target_company",
                    }
                    and re.search(
                        rf"[、,，]\s*{re.escape(str(surface))}\s*(?:与|和|及)",
                        body,
                    )
                ):
                    return False
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{2,40}", canonical)
        and has_primary_context(_FINANCIAL_RELATION_CONTEXT, before=40, after=64)
        and not sources
        & {
            "investment_target_company",
            "investment_actor_company",
            "descriptor_alias",
            "descriptor_company",
            "descriptor_context_alias",
            "action_subject",
            "compact_action_subject",
            "direct_clause_company",
            "company_surface",
        }
        and any(
            re.search(
                rf"{re.escape(str(surface))}[^。！!；;\n]{{0,48}}"
                r"(?:基金|募资|首关|LP|GP)",
                body,
                flags=re.I,
            )
            for surface in (canonical,)
        )
    ):
        return False
    if (
        re.search(r"(?:投|资本|基金|资管|资产管理|股权)", canonical)
        and has_primary_context(_FINANCIAL_RELATION_CONTEXT, before=80, after=100)
        and "investment_target_company" not in sources
    ):
        return False
    if (
        re.match(r"^前", canonical)
        and sources <= {"organization_role", "company_reference", "reporting_subject"}
        and re.search(
            rf"(?:由|来自|曾任|原任)\s*{re.escape(canonical)}[^。！!；;\n]{{0,16}}"
            r"(?:副总裁|总裁|董事长|创始人|负责人)",
            body,
        )
    ):
        return False

    short_cjk = bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", canonical))
    # A compact action can still capture a person's name (for example,
    # ``马斯克称``).  A short CJK surface next to a person/editorial role is
    # not an operating company unless the article also gives explicit company
    # evidence.
    if (
        short_cjk
        and not sources
        & {
            "company_reference",
            "organization_role",
            "action_subject",
            "direct_clause_company",
            "company_surface",
            "legal_name",
            "explicit_alias",
            "listed_ticker",
            "investment_target_company",
        }
        and not sources
        & {
            "attached_product_owner",
            "grounded_product_owner",
            "grounded_product_child",
            "grounded_parent_alias",
            "corporate_product_reference",
        }
        and has_primary_context(_PERSON_OR_EDITORIAL_CONTEXT)
    ):
        return False
    # ``前地平线副总裁`` and similar career-history phrases are organization
    # references, not evidence that the preceding token is a company subject.
    if (
        short_cjk
        and sources <= {"organization_role", "company_reference", "reporting_subject"}
        and any(
            re.search(
                rf"(?:前|原)\s*{re.escape(str(surface))}[^。！!；;\n]{{0,12}}"
                r"(?:副总裁|总裁|董事长|创始人|负责人)",
                window,
            )
            for surface in all_surfaces
            for window in surface_windows(str(surface), before=8, after=40)
        )
    ):
        return False
    # Public bodies, industrial parks, talent platforms, and event hosts can
    # have an organization-role or legal-name surface, but they are not the
    # operating hard-tech company sought by the lead radar.
    public_operational_sources = {
        "action_subject",
        "compact_action_subject",
        "direct_clause_company",
        "company_surface",
        "listed_ticker",
        "investment_target_company",
        "direct_hosting_company",
    }
    if has_primary_context(_PUBLIC_CONTEXT) and not sources & public_operational_sources:
        return False
    if has_primary_context(re.compile(r"(?:主办单位|承办单位|联合承办|参展|展位)")) and not (
        sources
        & {
            "action_subject",
            "compact_action_subject",
            "company_surface",
            "direct_hosting_company",
        }
    ):
        return False
    def has_direct_subject_action() -> bool:
        action = r"(?:宣布|发布|完成|获得|推出|上线|交付|融资|扩产|投建|成立|组建|刷新|披露|签署|承诺|支出|投入|挂牌|申购)"
        for surface in all_surfaces:
            for window in surface_windows(str(surface), before=12, after=72):
                if re.search(
                    rf"{re.escape(str(surface))}(?!\s*[与、,，])\s*"
                    rf"(?:[（(][^）)]{{0,24}}[）)])?[^。！!；;\n]{{0,18}}{action}",
                    window,
                ):
                    return True
        return False

    def has_local_funding_signal() -> bool:
        """Keep explicit company financing phrases eligible in long prose."""
        return any(
            re.search(
                r"(?:融资|融资纪录|刷新[^。！？\n]{0,24}融资|完成[^。！？\n]{0,16}融资)",
                window,
            )
            for surface in all_surfaces
            for window in surface_windows(str(surface), before=32, after=80)
        )

    subsidiary_anchor = has_context(
        re.compile(r"(?:\u5168\u8d44\u5b50\u516c\u53f8|\u5b50\u516c\u53f8)")
    ) and has_context(_HARDTECH_CONTEXT, before=140, after=140)
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{2,40}", canonical)
        and
        has_local_funding_signal()
        and not has_local_fund_vehicle_context()
        and not sources
        & {"investment_actor_company", "investment_target_company"}
    ):
        return True
    if (
        sources == {"listed_ticker"}
        and re.search(r"(?:新股|申购|挂牌上市|上市首日)", body)
        and not has_primary_context(_FINANCIAL_RELATION_CONTEXT, before=40, after=64)
    ):
        return True
    # Keep a listed operating company in the semantic ledger even when the
    # article is a passive equity-transfer notice.  The lead-scope flag below
    # will exclude it from proactive hard-tech lead generation.
    if "listed_ticker" in sources and re.search(
        r"(?:转让|参股公司|股权)", body
    ):
        return True
    # Investment actors and fund vehicles are useful as investor metadata but
    # must not become company leads.  Keep an explicit investment target (or
    # a clear operating action) while rejecting bare funds/LPs and coordinated
    # participants such as Brookfield and SevenX.
    if has_primary_context(_INVESTMENT_CONTEXT, before=40, after=64):
        hardtech_name = bool(
            re.search(
                r"(?:科技|智能|机器人|半导体|电子|电气|航空|航天|量子|核|能源|材料|芯|光电|工业|软件|信息|通信|算力|自动驾驶)",
                canonical,
            )
        )
        if "investment_target_company" not in sources:
            if (
                sources
                <= {
                    "direct_clause_company",
                    "company_context",
                    "company_reference",
                    "organization_role",
                    "reporting_subject",
                    "english_context",
                    "action_subject",
                    "compact_action_subject",
                }
                and not hardtech_name
                and has_primary_context(
                    _FINANCIAL_RELATION_CONTEXT, before=40, after=64
                )
            ):
                if not has_direct_subject_action() and not subsidiary_anchor:
                    return False
            if (
                sources
                & {"legal_name", "explicit_alias", "listed_ticker"}
                and not sources
                & {"company_surface", "direct_clause_company", "action_subject"}
                and not hardtech_name
            ):
                return False
            target_like_sources = {
                "investment_target_company",
                "descriptor_alias",
                "descriptor_company",
                "descriptor_context_alias",
            }
            if (
                has_primary_context(_FINANCIAL_RELATION_CONTEXT, before=40, after=64)
                and not hardtech_name
                and not has_direct_subject_action()
                and not subsidiary_anchor
                and not sources & target_like_sources
            ):
                return False
            if (
                re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{2,40}", canonical)
                and not sources
                & {
                    "company_surface",
                    "investment_actor_company",
                    "descriptor_alias",
                    "descriptor_company",
                    "descriptor_context_alias",
                }
                and has_primary_context(_FINANCIAL_RELATION_CONTEXT, before=40, after=64)
                and not has_direct_subject_action()
            ):
                return False
            if (
                re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{2,40}", canonical)
                and has_primary_context(_FINANCIAL_RELATION_CONTEXT, before=40, after=64)
                and "investment_actor_company" not in sources
                and not sources & target_like_sources
                and not has_direct_subject_action()
            ):
                return False
    if _COMPACT_EXTRA_NOISE.search(canonical):
        return False
    canonical_invalid = (
        not _eligible_name(canonical)
        or _KNOWN_NON_OPERATING_NAMES.fullmatch(canonical)
        or _KNOWN_INVESTOR_BRAND.fullmatch(canonical)
        or _INVESTMENT_INSTITUTION_NAME.search(canonical)
        or _CONCEPT_OR_ROLE_NAME.fullmatch(canonical)
        or _ENTITY_SEMANTIC_NOISE.search(canonical)
        or _COMPACT_SUBJECT_NOISE.search(canonical)
        or _COMPACT_EXTRA_NOISE.search(canonical)
        or _COMPACT_SUBJECT_WEAK_TAIL.search(canonical)
        or re.match(
            r"^(?:控股股东|大会由|数周后|未来能否|这意味着|本质上|"
            r"无论是|也正是|用来|包括|代表)",
            canonical,
        )
        or re.search(r"(?:新任|接任|担任|同时|称|表示|发文)$", canonical)
    )
    if canonical_invalid and (
        not compact_surfaces or "compact_action_subject" not in sources
    ):
        return False
    if (
        "reporting_operational_subject" in sources
        and "compact_action_subject" not in sources
        and not sources
        & {
            "action_subject",
            "direct_clause_company",
            "company_context",
            "company_reference",
            "english_context",
            "organization_role",
            "legal_name",
            "explicit_alias",
            "listed_ticker",
            "employee_title_company",
        }
        and not any(
            re.search(
                rf"{re.escape(str(surface))}\s*(?:表示|宣布|发布|推出|上线|完成|获得|刷新|融资|将于)",
                body,
            )
            for surface in all_surfaces
        )
    ):
        return False
    # A product/child relation is useful for binding the parent company, but
    # the child surface itself is never an operating-company seed unless the
    # article independently gives it a legal or explicit company identity.
    if (
        "grounded_product_child" in sources
        and not sources
        & {
            "legal_name",
            "explicit_alias",
            "listed_ticker",
            "employee_title_company",
        }
        and not (
            "attached_product_owner" in sources
            and "action_subject" in sources
        )
    ):
        return False

    # The compact scanner promotes only the exact bounded subject it found
    # next to a concrete action. Product children remain discovery records
    # unless a separate owner relation also identifies them as a company.
    if "compact_action_subject" in sources and compact_surfaces:
        compact_action = rf"(?:{_COMPACT_BRIDGE}){{0,5}}{_COMPACT_ACTION}"
        compact_has_independent_seed = bool(
            sources
            & {
                "action_subject",
                "direct_clause_company",
                "english_context",
                "company_reference",
                "investment_actor_company",
                "investment_target_company",
                "organization_role",
            }
        )
        compact_reporting_seed = any(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{1,40}", str(surface))
            and re.search(
                rf"{re.escape(surface)}[^。！？；;\n]{{0,4}}"
                rf"{_COMPACT_REPORTING_OPERATION.pattern}",
                body,
            )
            for surface in compact_surfaces
        )
        if (
            (compact_has_independent_seed or compact_reporting_seed)
            and (
            "grounded_product_child" not in sources
            or "attached_product_owner" in sources
            )
        ) and any(
            re.search(
                rf"(?<![A-Za-z0-9\u4e00-\u9fff]){re.escape(surface)}"
                rf"{compact_action}",
                body,
            )
            for surface in compact_surfaces
            if not _COMPACT_PRODUCT_SUFFIX.search(str(surface))
        ):
            return True

    # These sources encode an explicit organization assertion in the article.
    if sources & {
        "legal_name",
        "explicit_alias",
        "listed_ticker",
        "employee_title_company",
    }:
        return True
    if sources & {
        "descriptor_alias",
        "descriptor_context_alias",
        "descriptor_company",
    }:
        descriptor_surfaces = tuple(
            dict.fromkeys(
                surface
                for surface in (canonical, *tuple(record.get("aliases", ())))
                if surface
            )
        )
        descriptor_action = rf"(?:{_COMPACT_BRIDGE}){{0,5}}{_COMPACT_ACTION}"
        descriptor_anchor = any(
            re.search(
                rf"(?:公司|企业|开发商|厂商|平台|品牌)[^。！？；;\n]{{0,16}}"
                rf"{re.escape(surface)}[^。！？；;\n]{{0,32}}"
                rf"{descriptor_action}",
                body,
            )
            for surface in descriptor_surfaces
        )
        if descriptor_anchor:
            return True
        descriptor_identity_anchor = any(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{3,40}", surface)
            and not _ENGLISH_GENERIC.fullmatch(surface)
            and re.search(
                rf"(?:公司|企业)[（(\s]*{re.escape(surface)}",
                body,
            )
            for surface in descriptor_surfaces
        )
        if descriptor_identity_anchor:
            return True
    # Article-local product-owner relations are strong when the parent itself
    # is attached to a bounded product action (for example,
    # ``腾讯WorkBuddy推出`` or ``蚂蚁阿福升级``).  A product record carries
    # ``grounded_product_owner`` but does not carry the parent source, so this
    # branch promotes only the owner side of that relation.
    if (
        "attached_product_owner" in sources
        and (
            "grounded_product_owner" in sources
            or "grounded_product_child" in sources
        )
    ):
        owner_surfaces = tuple(
            dict.fromkeys(
                (canonical, *tuple(record.get("aliases", ())))
            )
        )
        if any(
            re.search(
                rf"{re.escape(surface)}[^。！？；;\n]{{0,36}}"
                rf"{_COMPACT_PRODUCT_OWNER_ACTION.pattern}",
                body,
            )
            for surface in owner_surfaces
            if surface
        ):
            return True
    if (
        sources
        <= {
            "reporting_subject",
            "reporting_operational_subject",
            "company_reference",
        }
        and re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", canonical)
    ):
        return False
    if {"versioned_brand", "title_brand"} <= sources:
        return True

    surfaces = tuple(
        dict.fromkeys(
            surface
            for surface in (canonical, *tuple(record["aliases"]))
            if len(surface) >= 2
        )
    )
    action_words = (
        r"(?:已|正式|成功|刚刚|今日|近日|日前|今天|也|还|将|计划|拟)?"
        r"(?:完成|获得|获批|宣布|发布|推出|上线|开源|签署|签订|"
        r"达成|投建|扩产|投产|量产|交付|发货|启动|中标|增资|"
        r"收购|并购|组建|进入|带来|展示|建成|打造|共建|部署|"
        r"会面|推进|承诺|表示|称|扩建|重返|投资|变更|获核准)"
    )
    near_action_anchor = any(
        re.search(
            rf"{re.escape(surface)}[^。！？；\n]{{0,40}}{action_words}",
            body,
            re.I,
        )
        for surface in surfaces
    )
    role_or_official_anchor = any(
        re.search(
            rf"{re.escape(surface)}(?:官方|方面|内部|CEO|首席执行官|"
            r"董事长|会长|高级副总裁|副总裁|总裁|创始人|联合创始人)",
            body,
            re.I,
        )
        for surface in surfaces
    )
    employee_title_anchor = any(
        re.search(
            rf"{re.escape(surface)}[^。！？；\n]{{0,24}}"
            r"(?:部|部门|事业部|研究院|中心)(?:总经理|负责人|总监|院长)",
            body,
            re.I,
        )
        for surface in surfaces
    )
    explicit_concept_definition = any(
        re.search(
            rf"(?:名为[“”「」\"']?{re.escape(surface)}|"
            rf"{re.escape(surface)}[^。！？；\n]{{0,18}}(?:崭新)?概念)",
            body,
        )
        for surface in surfaces
    )
    company_shaped = bool(
        _COMPANY_SHAPED_ENDING.search(canonical)
        or (
            re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{1,40}", canonical)
            and not _ENGLISH_GENERIC.fullmatch(canonical)
        )
        or re.fullmatch(
            r"[A-Za-z][A-Za-z0-9 .&+*-]{1,32}[\u4e00-\u9fff]{1,8}",
            canonical,
        )
    )

    # Editorial titles are leads, not evidence.  Require a second body-level
    # action/role source for a title surface to become a seed.
    if (
        canonical in title
        and near_action_anchor
        and sources
        & {
            "title_action",
            "direct_clause_company",
            "company_context",
            "organization_role",
            "bulletin_unit_company",
            "inline_bilingual_entity",
        }
        and (
            company_shaped
            or "compact_action_subject" in sources
            or sources
            & {
                "legal_name",
                "explicit_alias",
                "listed_ticker",
                "organization_role",
            }
        )
    ):
        return True
    if (
        "organization_role" in sources
        and role_or_official_anchor
        and not re.search(r"(?:创始人|董事长|总裁|主任|中心|科研)$", canonical)
    ):
        return True
    if employee_title_anchor:
        return True
    if explicit_concept_definition:
        return False
    if (
        company_shaped
        and near_action_anchor
        and sources
        & {
            "action_subject",
            "direct_clause_company",
            "company_context",
            "reporting_operational_subject",
        }
    ):
        return True
    # A number of real operating companies use a short brand rather than a
    # legal suffix (for example ``九科信息`` or ``白犀牛``).  In a long article
    # their first grammatical mention can still be an unambiguous company
    # subject: the name is followed in the same clause by a concrete
    # operational predicate (发布、推出、完成融资、组建团队, ...).  Keep this
    # rule deliberately narrow: it requires an action-subject discovery,
    # bounded CJK/Latin name shape, and the same body-level action anchor; it
    # does not promote reporting-only, title-only, product, or editorial
    # fragments.
    short_subject_sources = {
        "action_subject",
        "direct_clause_company",
        "english_context",
        "candidate_action_subject",
    }
    short_cjk = re.fullmatch(r"[\u4e00-\u9fff路]{2,8}", canonical)
    short_latin = re.fullmatch(
        r"[A-Za-z][A-Za-z0-9 .&+*-]{1,40}", canonical
    )
    short_mixed = re.fullmatch(
        r"[A-Za-z][A-Za-z0-9 .&+*-]{1,32}[\u4e00-\u9fff]{1,8}",
        canonical,
    )
    if (
        short_subject_sources & sources
        and _short_subject_has_operational_clause(record, body=body)
        and (
            short_cjk
            or (short_latin and not _ENGLISH_GENERIC.fullmatch(canonical))
            or short_mixed
        )
        and not _COMPACT_PRODUCT_SUFFIX.search(canonical)
    ):
        return True
    # Compact synthetic/flash documents contain little room for a second
    # organization assertion.  A clean direct grammatical subject is enough;
    # long articles still require one of the independent anchors above.
    if (
        len(body) <= 160
        and sources
        & {
            "direct_clause_company",
            "company_context",
            "context_alias",
            "internal_company_reference",
            "direct_bulletin_company",
            "direct_hosting_company",
            "investment_actor_company",
            "investment_target_company",
            "reporting_operational_subject",
            "english_context",
            "attached_product_owner",
            "action_subject",
        }
    ):
        return True
    if (
        "inline_bilingual_entity" in sources
        and near_action_anchor
        and not re.search(r"(?:发布|交付|融资|产品|模型|平台|系统)", canonical)
        and (
            re.fullmatch(r"[\u4e00-\u9fff·]{3,12}", canonical)
            or re.fullmatch(r"[A-Z][A-Za-z0-9 .&+*-]{1,40}", canonical)
        )
        and bool(
            sources
            & {
                "bulletin_unit_company",
                "descriptor_company",
                "direct_clause_company",
                "organization_role",
            }
        )
    ):
        return True
    if (
        "bulletin_unit_company" in sources
        and near_action_anchor
        and not re.search(r"(?:模型|产品|平台|系统|机器人)$", canonical)
        and (
            company_shaped
            or re.fullmatch(r"[\u4e00-\u9fff·]{2,8}", canonical)
        )
    ):
        fund_only = any(
            re.search(
                rf"{re.escape(surface)}[^。！？；\n]{{0,20}}"
                r"(?:宣布[^。！？；\n]{0,12})?"
                r"(?:旗下[^。！？；\n]{0,12})?"
                r"(?:募资|完成[^。！？；\n]{0,8}(?:首关|终关)|做LP)",
                body,
                re.I,
            )
            for surface in surfaces
        )
        if fund_only and not company_shaped:
            return False
        return True
    return False


def _record_is_out_of_scope_media(
    record: Mapping[str, Any],
    *,
    body: str,
) -> bool:
    """Exclude media/advertising operators and their explicitly named children.

    A legal company suffix is strong evidence that a string is an organization,
    but it is not evidence that the organization belongs to Lead Radar's
    hard-tech operating-company scope. The second rule is deliberately
    relation based: it only propagates the exclusion when the article itself
    says that the candidate is the wholly owned subsidiary of a media or
    advertising company. Industrial companies that happen to make an
    investment therefore remain eligible.
    """

    surfaces = tuple(
        dict.fromkeys(
            str(surface)
            for surface in (record["canonical_name"], *tuple(record["aliases"]))
            if len(str(surface)) >= 2
        )
    )
    if any(_MEDIA_OR_ADVERTISING_COMPANY.search(surface) for surface in surfaces):
        return True
    if any(_NON_OPERATING_SURFACE.search(surface) for surface in surfaces):
        return True
    if any(
        re.search(
            rf"(?:主办单位|承办单位|联合承办|参展|展位)[^。！!；;\n]{{0,40}}"
            rf"{re.escape(surface)}",
            body,
        )
        for surface in surfaces
    ):
        return True
    return any(
        re.search(
            rf"(?:传媒|广告|文化发展)[^。！？；\n]{{0,96}}"
            rf"(?:其|的)?全资子公司{re.escape(surface)}",
            body,
        )
        for surface in surfaces
    )


@dataclass(frozen=True)
class EntityMention:
    text: str
    char_start: int
    char_end: int
    region: str
    discovery_source: str


@dataclass(frozen=True)
class EntityScope:
    scope_id: str
    char_start: int
    char_end: int
    entity_ids: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class ArticleEntity:
    entity_id: str
    canonical_name: str
    entity_kind: str
    operating_subject_eligible: bool
    aliases: tuple[str, ...]
    mentions: tuple[EntityMention, ...]
    discovery_sources: tuple[str, ...]
    lead_scope_eligible: bool = True

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "canonical_name": self.canonical_name,
            "entity_kind": self.entity_kind,
            "operating_subject_eligible": self.operating_subject_eligible,
            "lead_scope_eligible": self.lead_scope_eligible,
            "aliases": list(self.aliases),
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArticleEntityLedger:
    version: str
    source_id: str
    source_article_id: str
    entities: tuple[ArticleEntity, ...]
    scopes: tuple[EntityScope, ...] = ()

    def by_id(self) -> dict[str, ArticleEntity]:
        return {entity.entity_id: entity for entity in self.entities}

    def eligible(self) -> tuple[ArticleEntity, ...]:
        return tuple(
            entity for entity in self.entities if entity.operating_subject_eligible
        )

    def lead_eligible(self) -> tuple[ArticleEntity, ...]:
        return tuple(entity for entity in self.entities if entity.lead_scope_eligible)

    def entity_for_name(self, value: str) -> ArticleEntity | None:
        query_keys = {_key(item) for item in company_alias_candidates(value)}
        matches = []
        for entity in self.entities:
            entity_keys = {
                _key(alias)
                for name in (entity.canonical_name, *entity.aliases)
                for alias in company_alias_candidates(name)
            }
            if query_keys & entity_keys:
                matches.append(entity)
        return matches[0] if len(matches) == 1 else None

    def to_prompt_rows(self) -> list[dict[str, Any]]:
        return [entity.to_prompt_dict() for entity in self.entities]

    def contextual_subject_ids(
        self, char_start: int, char_end: int
    ) -> tuple[str, ...]:
        # A dedicated adapter boundary is authoritative for a multi-item
        # bulletin. Do not union a broad media-marker scope with it: that is
        # exactly how a financing claim in one digest item can inherit the
        # subject of a later policy/news item.
        adapter_scopes = tuple(
            scope for scope in self.scopes if scope.source == "adapter_item_subject"
        )
        scopes = adapter_scopes if adapter_scopes else self.scopes
        return tuple(
            dict.fromkeys(
                entity_id
                for scope in scopes
                if scope.char_start <= char_start < scope.char_end
                or scope.char_start < char_end <= scope.char_end
                for entity_id in scope.entity_ids
            )
        )


def build_article_entity_ledger(
    article: CleanArticle,
    candidates: Iterable[Mapping[str, Any]],
    rule_events: Iterable[SemanticEvent],
) -> ArticleEntityLedger:
    candidate_rows = [dict(item) for item in candidates]
    rule_rows = list(rule_events)
    records: dict[str, dict[str, Any]] = {}
    alias_owner: dict[str, str] = {}

    def merge_record(source_key: str, target_key: str) -> None:
        if source_key == target_key or source_key not in records:
            return
        source = records.pop(source_key)
        target = records.setdefault(
            target_key,
            {
                "canonical_name": source["canonical_name"],
                "aliases": set(),
                "mentions": [],
                "sources": set(),
            },
        )
        target["aliases"].update(source["aliases"])
        target["aliases"].add(source["canonical_name"])
        target["mentions"].extend(
            mention for mention in source["mentions"] if mention not in target["mentions"]
        )
        target["sources"].update(source["sources"])

    def add(
        raw_name: str,
        source: str,
        *,
        start: int = -1,
        end: int = -1,
        region: str = "body",
        canonical_override: str = "",
    ) -> str:
        raw = _clean_subject(raw_name)
        canonical = canonical_override or raw
        if not canonical or len(canonical) > 64:
            return ""
        normalized = _key(canonical)
        if not normalized:
            return ""
        owner = alias_owner.get(normalized, normalized)
        record = records.setdefault(
            owner,
            {
                "canonical_name": canonical,
                "aliases": set(),
                "mentions": [],
                "sources": set(),
            },
        )
        record["sources"].add(source)
        if raw != record["canonical_name"]:
            record["aliases"].add(raw)
        if start >= 0 and end > start:
            mention = EntityMention(raw, start, end, region, source)
            if mention not in record["mentions"]:
                record["mentions"].append(mention)
        return owner

    def attach_alias(
        canonical_raw: str,
        alias_raw: str,
        source: str,
        *,
        canonical_start: int = -1,
        canonical_end: int = -1,
        alias_start: int = -1,
        alias_end: int = -1,
        region: str = "body",
    ) -> str:
        canonical = _clean_subject(canonical_raw)
        alias = _clean_subject(alias_raw)
        if (
            not canonical
            or not alias
            or _key(canonical) == _key(alias)
            or not _eligible_name(canonical)
            or not _eligible_name(alias)
            or _ENGLISH_GENERIC.fullmatch(canonical)
            or _ENGLISH_GENERIC.fullmatch(alias)
        ):
            return ""
        owner = add(
            canonical,
            source,
            start=canonical_start,
            end=canonical_end,
            region=region,
        )
        if not owner:
            return ""
        alias_key = _key(alias)
        old_key = alias_owner.get(alias_key, alias_key)
        alias_owner[alias_key] = owner
        merge_record(old_key, owner)
        records[owner]["aliases"].add(alias)
        records[owner]["sources"].add(source)
        if alias_start >= 0 and alias_end > alias_start:
            mention = EntityMention(alias, alias_start, alias_end, region, source)
            if mention not in records[owner]["mentions"]:
                records[owner]["mentions"].append(mention)
        return owner

    body = article.clean_body

    # Explicit aliases are processed first so every later short-name mention is
    # deterministically attached to the legal owner.
    for match in _EXPLICIT_ALIAS.finditer(body):
        legal = _clean_legal_name(match.group("legal"))
        legal_key = add(
            legal,
            "legal_name",
            start=match.start("legal") + match.group("legal").rfind(legal),
            end=match.start("legal") + match.group("legal").rfind(legal) + len(legal),
        )
        alias = _clean_subject(match.group("alias"))
        if legal_key and alias:
            alias_key = _key(alias)
            old_key = alias_owner.get(alias_key, alias_key)
            alias_owner[alias_key] = legal_key
            merge_record(old_key, legal_key)
            records[legal_key]["aliases"].add(alias)
            records[legal_key]["sources"].add("explicit_alias")
            records[legal_key]["mentions"].append(
                EntityMention(
                    alias,
                    match.start("alias"),
                    match.end("alias"),
                    "body",
                    "explicit_alias",
                )
            )

    for match in _CONTEXT_ALIAS.finditer(body):
        left = match.group("left")
        inside = match.group("inside")
        left_start = match.start("left")
        # Descriptor prose may be captured together with the actual Latin
        # organization: “生命科学公司Repligen（瑞普利金）”.  Keep the
        # company token, not the descriptor, as the canonical surface.
        latin_tail = re.search(
            r"[A-Za-z][A-Za-z0-9 .&+*-]{1,40}$",
            left,
        )
        if (
            latin_tail
            and re.search(r"公司|企业|开发商|制造商|服务商", left[: latin_tail.start()])
        ):
            left_start += latin_tail.start()
            left = latin_tail.group(0)
        if not (
            (re.search(r"[A-Za-z]", left) and re.search(r"[\u4e00-\u9fff]", inside))
            or (re.search(r"[\u4e00-\u9fff]", left) and re.search(r"[A-Za-z]", inside))
        ):
            continue
        english_side = inside if re.search(r"[A-Za-z]", inside) else left
        if not re.search(r"[A-Z]", english_side):
            continue
        if (
            re.fullmatch(r"[A-Z]{2,8}", re.sub(r"[^A-Za-z]", "", english_side))
            and re.search(
                r"方法|模型|技术|系统|平台|数字孪生|替代|指标|疗法",
                left + inside,
            )
        ):
            continue
        before = body[max(0, match.start() - 48) : match.start()]
        after = body[match.end() : min(len(body), match.end() + 80)]
        after = re.split(r"[。！？；;\n]", after, maxsplit=1)[0]
        if re.search(r"首席科学家|研究员|教授|博士", left) or re.match(
            r"\s*(?:运营|任职|担任|供职|就职)的", after
        ):
            continue
        if not (
            re.search(r"(?:公司|企业|开发商|制造商|运营商)\s*$", before)
            or _PAYLOAD_OPERATION.search(after)
        ):
            continue
        descriptor_grounded = bool(
            re.search(
                r"(?:公司|企业|品牌|制造商|开发商|运营商|服务商)\s*$",
                before,
            )
            or left_start > match.start("left")
        )
        owner = attach_alias(
            left,
            inside,
            "descriptor_context_alias" if descriptor_grounded else "context_alias",
            canonical_start=left_start,
            canonical_end=left_start + len(left),
            alias_start=match.start("inside"),
            alias_end=match.end("inside"),
        )
        short = match.group("short") or ""
        if owner and short:
            attach_alias(
                records[owner]["canonical_name"],
                short,
                "descriptor_context_alias" if descriptor_grounded else "context_alias",
                alias_start=match.start("short"),
                alias_end=match.end("short"),
            )

    for match in _DESCRIPTOR_ENGLISH_ALIAS.finditer(body):
        english = match.group("english").strip()
        owner = add(
            english,
            "descriptor_alias",
            start=match.start("english"),
            end=match.end("english"),
        )
        short = (match.group("short") or "").strip()
        if owner and short and not _ENGLISH_GENERIC.fullmatch(short):
            attach_alias(
                english,
                short,
                "descriptor_alias",
                alias_start=match.start("short"),
                alias_end=match.end("short"),
            )

    for match in _LISTED_TICKER.finditer(body):
        name = _clean_subject(match.group("name"))
        name = re.sub(r"^(?:为)?(?:创业板|科创板|主板|北交所)(?:的)?", "", name)
        raw = match.group("name")
        offset = raw.rfind(name)
        add(
            name,
            "listed_ticker",
            start=match.start("name") + max(0, offset),
            end=match.start("name") + max(0, offset) + len(name),
        )

    for match in _LEGAL_NAME.finditer(body):
        raw = match.group(0)
        lead_context = body[max(0, match.start() - 20) : match.start()]
        strip_enumerator = bool(
            re.search(r"(?:牵头[，,]?|包括|(?:^|[，,；;])由|联同)\s*$", lead_context)
        )
        legal = _clean_legal_name(raw, strip_enumerator=strip_enumerator)
        offset = raw.rfind(legal)
        add(
            legal,
            "legal_name",
            start=match.start() + max(offset, 0),
            end=match.start() + max(offset, 0) + len(legal),
        )

    # Entity recall must not depend on a perfect parse of the clause to the
    # left of an action verb.  Company-shaped surfaces are safe bounded
    # candidates; they still need an action Claim and semantic acceptance
    # before an event can be emitted.
    for match in _COMPANY_SUFFIX_SURFACE.finditer(body):
        company_surface = _clean_subject(match.group("name"))
        if _ENTITY_SEMANTIC_NOISE.search(company_surface):
            continue
        add(
            company_surface,
            "company_surface",
            start=match.start("name"),
            end=match.start("name") + len(company_surface),
        )

    for match in _REPRESENTATIVE_COMPANY.finditer(body):
        add(
            match.group("name"),
            "company_surface",
            start=match.start("name"),
            end=match.end("name"),
        )

    for match in _HOSTING_PARTICIPANT.finditer(body):
        add(
            match.group("name"),
            "direct_hosting_company",
            start=match.start("name"),
            end=match.end("name"),
        )
    for match in _HOSTING_BY_PARTICIPANT.finditer(body):
        add(
            match.group("name"),
            "direct_hosting_company",
            start=match.start("name"),
            end=match.end("name"),
        )

    # Some aggregators concatenate a Chinese brand and an English brand
    # without parentheses (for example, "甲辰Alpha Robotics宣布...").  Bind
    # both observed surfaces to one article-local entity instead of relying on
    # a global alias knowledge base.
    for match in _INLINE_BILINGUAL_ENTITY.finditer(body):
        chinese = _inline_bilingual_chinese_surface(match.group("cn"))
        if not chinese:
            continue
        english = match.group("en")
        combined = f"{chinese}{english}"
        owner = add(
            chinese,
            "inline_bilingual_entity",
            start=match.start("cn"),
            end=match.end("cn"),
        )
        if owner:
            for alias, start, end in (
                (english, match.start("en"), match.end("en")),
                (combined, match.start("cn"), match.end("en")),
            ):
                alias_key = _key(alias)
                old_key = alias_owner.get(alias_key, alias_key)
                alias_owner[alias_key] = owner
                merge_record(old_key, owner)
                records[owner]["aliases"].add(alias)
                records[owner]["sources"].add("inline_bilingual_entity")
                records[owner]["mentions"].append(
                    EntityMention(
                        alias,
                        start,
                        end,
                        "body",
                        "inline_bilingual_entity",
                    )
                )

    def discover_bulletins(text: str, *, region: str) -> None:
        for match in _BULLETIN_SUBJECT.finditer(text):
            if not _PAYLOAD_OPERATION.search(match.group("payload")):
                continue
            name = _clean_subject(match.group("name"))
            add(
                name,
                (
                    "title_bulletin"
                    if region == "title"
                    else "direct_bulletin_company"
                ),
                start=match.start("name") if region == "body" else -1,
                end=match.end("name") if region == "body" else -1,
                region=region,
            )

    discover_bulletins(body, region="body")
    discover_bulletins(article.index.title, region="title")

    for region, text in (("body", body), ("title", article.index.title)):
        for match in _ORGANIZATION_ROLE.finditer(text):
            raw = match.group("name")
            english_tail = re.search(r"[A-Za-z][A-Za-z0-9 .&+*-]{1,40}$", raw)
            if english_tail and re.search(r"[\u4e00-\u9fff]", raw[: english_tail.start()]):
                raw = english_tail.group(0)
            if re.search(r"原|副|新任|合伙人|担任|接任|多家", raw):
                continue
            name = _clean_subject(raw)
            offset = match.group("name").rfind(name)
            add(
                name,
                "organization_role",
                start=(match.start("name") + max(0, offset)) if region == "body" else -1,
                end=(
                    match.start("name") + max(0, offset) + len(name)
                    if region == "body"
                    else -1
                ),
                region=region,
            )

    for pattern, source in (
        (_COMPANY_REFERENCE, "company_reference"),
        (_INTERNAL_REFERENCE, "internal_company_reference"),
        (_LEADING_INTERNAL_REFERENCE, "internal_company_reference"),
        (_ATTACHED_LATIN_OWNER, "attached_product_owner"),
        (_CORPORATE_PRODUCT_REFERENCE, "corporate_product_reference"),
        (_AI_PRODUCT_OWNER, "attached_product_owner"),
    ):
        for match in pattern.finditer(body):
            if (
                pattern is _ATTACHED_LATIN_OWNER
                and _GENERIC_CJK_LATIN_OWNER.match(body, match.start())
            ):
                # The generic descriptor is only a grammatical lead-in to a
                # concrete Latin owner captured by the dedicated rule below.
                continue
            group_name = "owner" if pattern is _AI_PRODUCT_OWNER else "name"
            name = _clean_subject(match.group(group_name))
            if name:
                add(
                    name,
                    source,
                    start=match.start(group_name),
                    end=match.start(group_name) + len(name),
                )

    for match in _GENERIC_CJK_LATIN_OWNER.finditer(body):
        owner = _clean_subject(match.group("owner"))
        if owner:
            add(
                owner,
                "attached_product_owner",
                start=match.start("owner"),
                end=match.end("owner"),
            )

    # Bind explicitly grounded product/department surfaces to their operating
    # owner before the generic prefix merge runs.  These are article-local
    # aliases, not a global brand knowledge base.
    for match in _ATTACHED_LATIN_OWNER.finditer(body):
        if _GENERIC_CJK_LATIN_OWNER.match(body, match.start()):
            continue
        product = match.group("product")
        if product.upper() in {"AI", "API", "APP", "AGENT"}:
            continue
        attach_alias(
            _clean_subject(match.group("name")),
            product,
            "grounded_product_owner",
            alias_start=match.start("product"),
            alias_end=match.end("product"),
        )
    for match in _NAMED_PRODUCT_OWNER.finditer(body):
        attach_alias(
            match.group("owner"),
            match.group("product"),
            "grounded_product_owner",
            alias_start=match.start("product"),
            alias_end=match.end("product"),
        )
    for match in _ORG_UNIT_OWNER.finditer(body):
        attach_alias(
            _clean_subject(match.group("owner")),
            match.group("product"),
            "grounded_product_owner",
            alias_start=match.start("product"),
            alias_end=match.end("product"),
        )
    for match in _OFFICIAL_PRODUCT_OWNER.finditer(body):
        owner = _clean_subject(match.group("owner"))
        product = match.group("product")
        attach_alias(
            owner,
            product,
            "grounded_product_owner",
            alias_start=match.start("product"),
            alias_end=match.end("product"),
        )
        if product.startswith("企业") and len(product) > 3:
            short_product = product[2:]
            attach_alias(
                owner,
                short_product,
                "grounded_product_owner",
                alias_start=match.end("product") - len(short_product),
                alias_end=match.end("product"),
            )
    for match in _QUOTED_PRODUCT_OWNER.finditer(body):
        owner = _clean_subject(match.group("owner"))
        product = re.sub(r"\s+", "", match.group("product"))
        attach_alias(
            owner,
            product,
            "grounded_product_owner",
            alias_start=match.start("product"),
            alias_end=match.end("product"),
        )
        owner_aliases = company_alias_candidates(owner)
        short_owner = min(owner_aliases, key=len) if owner_aliases else owner
        if len(short_owner) >= 2:
            attach_alias(owner, short_owner + product, "grounded_product_owner")
            unit = match.group("unit") or ""
            if unit:
                attach_alias(owner, short_owner + unit, "grounded_product_owner")

    for match in _INVESTMENT_PAIR.finditer(body):
        for group in ("investor", "target"):
            raw = match.group(group)
            if group == "investor":
                raw = re.sub(r"将$", "", raw)
            name = _clean_subject(raw)
            offset = raw.rfind(name)
            add(
                name,
                (
                    "investment_actor_company"
                    if group == "investor"
                    else "investment_target_company"
                ),
                start=match.start(group) + max(0, offset),
                end=match.start(group) + max(0, offset) + len(name),
            )

    for match in _EXECUTIVE_TARGET_COMPANY.finditer(body):
        name = _clean_subject(match.group("name"))
        if name:
            add(
                name,
                "executive_target_company",
                start=match.start("name"),
                end=match.start("name") + len(name),
            )

    def discover_direct(text: str, *, region: str) -> None:
        for match in _DIRECT_ACTION.finditer(text):
            left, clause_start = _left_clause(text, match.start())
            subject = _clean_subject(left)
            if not subject:
                continue
            descriptor_context = bool(_SUBJECT_PREFIX.search(left))
            reporting_only = match.group(0) in {"表示", "称"}
            reporting_operational = bool(
                reporting_only
                and re.search(
                    r"(?:已|正式|成功)?(?:封禁|阻断|关闭|发布|推出|上线|"
                    r"开源|签署|完成|获得|投建|扩产|交付)",
                    text[match.end() : match.end() + 80],
                )
            )
            parts = _split_subjects(subject)
            for part in parts:
                if len(parts) > 1:
                    part = re.sub(r"组成联合体$", "", part)
                    if re.match(r"^联合", part) and re.search(
                        r"(?:股份有限公司|有限责任公司|有限公司)$", part
                    ):
                        part = re.sub(r"^联合", "", part)
                cleaned = _clean_subject(part)
                if not cleaned:
                    continue
                raw_start = text.rfind(cleaned, clause_start, match.start())
                direct_anchor = bool(
                    region == "body"
                    and not reporting_only
                    and _direct_clause_company_anchor(
                        text=text,
                        title=article.index.title,
                        clause_start=clause_start,
                        action_start=match.start(),
                        raw_start=raw_start,
                        cleaned=cleaned,
                        descriptor_context=descriptor_context,
                        joined_subject=len(parts) > 1,
                    )
                )
                source = (
                    "descriptor_company"
                    if descriptor_context
                    and _descriptor_anchors_subject(left, cleaned)
                    else (
                        "company_context"
                        if descriptor_context
                        else (
                            "direct_clause_company"
                            if direct_anchor
                            else (
                                "reporting_operational_subject"
                                if reporting_operational
                                else (
                                    "reporting_subject"
                                    if reporting_only
                                    else "action_subject"
                                )
                            )
                        )
                    )
                )
                add(
                    cleaned,
                    (
                        "title_action"
                        if region == "title"
                        else source
                    ),
                    start=raw_start if region == "body" else -1,
                    end=(raw_start + len(cleaned)) if raw_start >= 0 and region == "body" else -1,
                    region=region,
                )

    discover_direct(body, region="body")
    discover_direct(article.index.title, region="title")
    _discover_compact_action_subjects(article=article, add=add)

    # Multi-company digests often use a plain section heading followed by one
    # company action and omit legal suffixes.  Promote only the earliest
    # grammatical action subject in each routed item; later names remain
    # discovery-only investors, customers or commentary fragments.
    from .document_router import route_document

    routed = route_document(article)
    item_subject_rows = tuple(
        item
        for item in (article.structured_data or {}).get("item_subjects") or []
        if isinstance(item, Mapping)
    )

    def adapter_subject(value: Any) -> str:
        # Adapter metadata is already bounded to a headline; do not run the
        # broad editorial normalizer, which can strip legitimate short brands
        # such as ????. Only trim punctuation/quote noise at the edges.
        return str(value or "").strip(
            " \t\r\n\u3000\u3001\u3002\uff0c\uff1b\uff1a:?????\"'\u201c\u201d\u2018\u2019\u300c\u300d"
        )

    # Dedicated adapters have already made a bounded, article-local company
    # decision from the listing/detail DOM. Inject that decision into the
    # ledger before broad editorial scanners are scored. Without this bridge,
    # a malformed fragment can acquire an action
    # anchor while the adapter's exact company remains discovery-only.
    # ``company`` is the primary target; ``company_mentions`` are retained as
    # aliases so a legal name and its short brand resolve to one entity.
    metadata = {
        **dict(article.index.structured_data or {}),
        **dict(article.structured_data or {}),
    }
    metadata_company = adapter_subject(metadata.get("company"))
    # A digest-level ``company`` is usually only the listing's first item.
    # Item-local subjects are authoritative in this route, so do not constrain
    # the whole article to that one company.
    if item_subject_rows or routed.document_type == "multi_company_bulletin":
        metadata_company = ""
    metadata_mentions = metadata.get("company_mentions") or ()
    if isinstance(metadata_mentions, str):
        metadata_mentions = (metadata_mentions,)
    if not isinstance(metadata_mentions, (list, tuple, set)):
        metadata_mentions = ()
    # Official policy documents expose their issuing authority in the same
    # ``company`` field. It is a source authority, not an operating-company
    # lead, so never promote it into the article entity ledger.
    metadata_is_issuer = bool(
        routed.document_family == "policy_market"
        or article.index.source_id.startswith("miit-")
        or metadata.get("issuing_authority")
    )

    def metadata_company_valid(value: str) -> bool:
        if not value or len(value) < 3 or len(value) > 40:
            return False
        if not _eligible_name(value):
            return False
        # A short bare place/grammar token is not a safe adapter target. A
        # legal suffix or a recognizable Latin/brand surface may still pass.
        if len(value) <= 3 and not re.search(
            r"(?:\u80a1\u4efd\u6709\u9650\u516c\u53f8|\u6709\u9650\u8d23\u4efb\u516c\u53f8|\u6709\u9650\u516c\u53f8)$", value
        ):
            return False
        return value in body or value in article.index.title

    if metadata_company and not metadata_is_issuer and metadata_company_valid(
        metadata_company
    ):
        primary_owner = add(
            metadata_company,
            "adapter_metadata_company",
            canonical_override=metadata_company,
            start=body.find(metadata_company),
            end=body.find(metadata_company) + len(metadata_company)
            if metadata_company in body
            else -1,
        )
        if primary_owner:
            for raw_mention in metadata_mentions:
                mention = adapter_subject(raw_mention)
                if (
                    not mention
                    or mention == metadata_company
                    or not metadata_company_valid(mention)
                    or mention not in body
                ):
                    continue
                add(
                    mention,
                    "adapter_metadata_mention",
                    canonical_override=metadata_company,
                    start=body.find(mention),
                    end=body.find(mention) + len(mention),
                )

    if routed.document_type == "multi_company_bulletin":
        # Dedicated adapters may expose a bounded subject for each DOM item.
        # Seed those exact surfaces before eligibility scoring so a flattened
        # digest cannot force every action onto the listing/title company.
        for item in item_subject_rows:
            subject = adapter_subject(item.get("subject"))
            try:
                start = int(item.get("char_start"))
                end = int(item.get("char_end"))
            except (TypeError, ValueError):
                continue
            if (
                not subject
                or start < 0
                or end <= start
                or end > len(body)
                or subject not in body[start:end]
            ):
                continue
            owner = add(
                subject,
                "bulletin_unit_company",
                start=body.find(subject, start, end),
                end=body.find(subject, start, end) + len(subject),
            )
            # The exact adapter subject is the safest article-local canonical
            # surface. Replace a malformed action-parser surface (for example
            # a trailing quote or a headline tail) while retaining it as an
            # alias for provenance and deterministic ID stability.
            if owner and owner in records:
                current = str(records[owner].get("canonical_name") or "")
                if current and current != subject:
                    records[owner]["aliases"].add(current)
                    records[owner]["canonical_name"] = subject

        # Unit zero is the editorial summary.  It repeats many later stories
        # and contains aggregate phrases that are not company subjects.
        for unit in routed.units[1:]:
            candidates_in_unit: list[tuple[int, dict[str, Any]]] = []
            for record in records.values():
                if not set(record["sources"]) & {
                    "action_subject",
                    "direct_clause_company",
                    "company_context",
                    "reporting_operational_subject",
                }:
                    continue
                starts = [
                    mention.char_start
                    for mention in record["mentions"]
                    if mention.region == "body"
                    and unit.char_start <= mention.char_start < unit.char_end
                ]
                if starts:
                    candidates_in_unit.append((min(starts), record))
            if not candidates_in_unit:
                continue
            first_start = min(item[0] for item in candidates_in_unit)
            if first_start > unit.char_start + min(320, len(unit.text)):
                continue
            for start, record in candidates_in_unit:
                if start == first_start:
                    record["sources"].add("bulletin_unit_company")

    # A direct-action regex can capture "公司简称+人名" around an executive
    # title.  Ground the compact surface back to the organization discovered
    # by the explicit role grammar instead of exposing the person as a company.
    for region, text in (("body", body), ("title", article.index.title)):
        for match in _ORGANIZATION_ROLE.finditer(text):
            person = re.match(
                r"[\u4e00-\u9fff·]{2,6}", text[match.end("role") :]
            )
            if not person:
                continue
            person_name = person.group(0).rstrip("与和及")
            if len(person_name) < 2:
                continue
            organization = _clean_subject(match.group("name"))
            short_candidates = list(company_alias_candidates(organization))
            stripped = _LEDGER_BRAND_SUFFIX.sub("", organization)
            if stripped != organization:
                short_candidates.append(stripped)
            for short in dict.fromkeys(short_candidates):
                if len(short) < 2:
                    continue
                compact = short + person_name
                compact_key = alias_owner.get(_key(compact), _key(compact))
                organization_key = add(organization, "organization_role")
                if compact_key in records and organization_key:
                    merge_record(compact_key, organization_key)
                    alias_owner[_key(compact)] = organization_key
                    records[organization_key]["aliases"].add(compact)

    for region, text in (("body", body), ("title", article.index.title)):
        for name, name_start, name_end in _iter_english_context_entities(text):
            name = _clean_subject(name)
            if (
                _ENGLISH_GENERIC.fullmatch(name)
                or re.search(r"\d", name)
                or re.fullmatch(r"[A-Z]{2,4}", name)
            ):
                continue
            add(
                name,
                "english_context",
                start=name_start if region == "body" else -1,
                end=name_end if region == "body" else -1,
                region=region,
            )

    for match in _VERSIONED_BRAND.finditer(body):
        add(
            match.group("brand"),
            "versioned_brand",
            start=match.start("brand"),
            end=match.end("brand"),
        )
    for match in _VERSIONED_BRAND.finditer(article.index.title):
        add(match.group("brand"), "title_brand", region="title")

    for candidate in candidate_rows:
        hint = str(candidate.get("subject_hint") or "")
        if hint:
            start = body.find(hint, max(0, int(candidate.get("char_start", 0))))
            add(
                hint,
                "candidate_subject",
                start=start,
                end=start + len(hint) if start >= 0 else -1,
            )

    # Recover a bounded operating subject when the broad action scanner misses
    # a long-feature lead but the immutable claim quote starts with a company
    # followed by a concrete verb.
    candidate_action_subject = re.compile(
        r"^[\s\(??\[<]*(?:[^\)??\]>]{0,24}[\)??\]>][\s]*)?"
        r"(?P<name>[A-Za-z0-9\u4e00-\u9fff?&.+* -]{2,32}?)"
        r"(?=(?:\u9488\u5bf9|\u5ba3\u5e03|\u63a8\u51fa|\u53d1\u5e03|\u5b8c\u6210|\u7ec4\u5efa|\u6210\u7acb|\u6269\u5efa|\u4e0a\u7ebf|\u4ea4\u4ed8|\u878d\u8d44|\u6295\u8d44|\u8fbe\u6210|\u7b7e\u7f72|\u83b7\u5f97|\u542f\u52a8|\u62ab\u9732|\u5f00\u6e90))"
    )
    for candidate in candidate_rows:
        quote = str(candidate.get("quote") or "").strip()
        match = candidate_action_subject.match(quote)
        if not match:
            continue
        name = _clean_subject(match.group("name"))
        if not name or not _eligible_name(name):
            continue
        start = body.find(name, max(0, int(candidate.get("char_start", 0))))
        add(
            name,
            "candidate_action_subject",
            start=start,
            end=start + len(name) if start >= 0 else -1,
        )

    for event in rule_rows:
        name = event.canonical_company
        start = body.find(name)
        if start >= 0:
            add(name, "rule_seed", start=start, end=start + len(name))

    # In compact headlines a company brand and its Chinese product can be
    # concatenated (蚂蚁阿福升级...).  If the suffix is later referenced as the
    # product/object (让阿福...), the remaining prefix is a grounded owner cue.
    for record in list(records.values()):
        combined = record["canonical_name"]
        if not re.fullmatch(r"[\u4e00-\u9fff]{4,12}", combined):
            continue
        for split in range(2, len(combined) - 1):
            owner_name = combined[:split]
            product_name = combined[split:]
            product_reference = re.search(
                rf"(?:让|由|在|与|使用|通过|调用|打开)\s*{re.escape(product_name)}",
                body,
            )
            if not product_reference:
                continue
            record["sources"].add("grounded_product_child")
            owner_start = body.find(combined)
            owner_key = add(
                owner_name,
                "attached_product_owner",
                start=owner_start,
                end=owner_start + len(owner_name) if owner_start >= 0 else -1,
            )
            if owner_key and owner_key in records:
                records[owner_key]["sources"].add("grounded_product_owner")
            break

    # Grounded business-suffix aliases (甲辰科技 -> 甲辰) are useful when a
    # later sentence uses only the short brand.  Merge an already discovered
    # short record into the canonical owner to keep lookup unambiguous.
    for owner, record in list(records.items()):
        # Earlier snapshot entries can have been merged by this same pass.
        # Never dereference a deleted owner from the snapshot.
        if owner not in records:
            continue
        canonical = record["canonical_name"]
        possible_aliases = list(company_alias_candidates(canonical)[1:])
        for known_name in (canonical, *tuple(record["aliases"])):
            shortened = _LEDGER_BRAND_SUFFIX.sub("", known_name)
            if shortened != known_name:
                possible_aliases.append(shortened)
        for alias in dict.fromkeys(possible_aliases):
            if len(alias) < 2 or alias not in body:
                continue
            alias_key = alias_owner.get(_key(alias), _key(alias))
            if alias_key != owner:
                merge_record(alias_key, owner)
            alias_owner[_key(alias)] = owner
            records[owner]["aliases"].add(alias)
            records[owner]["sources"].add("grounded_alias")

    # When an article explicitly binds a short alias to a legal company, a
    # longer surface rooted at that alias is normally either the same brand
    # with a business suffix or one of its products.  Merge only these bounded
    # article-local descendants; do not create a global alias dictionary.
    grounded_owner_sources = {"legal_name", "explicit_alias"}
    descendant_suffix = re.compile(
        r"^(?:科技|智能|机器人|半导体|电子|生物|医药|医疗|"
        r".{0,16}(?:产品|接口|设备|平台|系统|模型|解决方案|扫描仪|"
        r"过滤器|药物|管线))$"
    )
    for owner, owner_record in list(records.items()):
        if owner not in records or not (
            set(owner_record["sources"]) & grounded_owner_sources
        ):
            continue
        roots = tuple(
            sorted(
                {
                    alias
                    for alias in (
                        owner_record["canonical_name"],
                        *tuple(owner_record["aliases"]),
                    )
                    if 2 <= len(alias) <= 16
                },
                key=len,
                reverse=True,
            )
        )
        for other, other_record in list(records.items()):
            if other == owner or other not in records:
                continue
            other_name = other_record["canonical_name"]
            matched_root = next(
                (
                    root
                    for root in roots
                    if other_name.startswith(root)
                    and descendant_suffix.fullmatch(other_name[len(root) :])
                ),
                "",
            )
            legal_stem = re.sub(
                r"(?:股份有限公司|有限责任公司|有限公司)$",
                "",
                owner_record["canonical_name"],
            )
            # Legal names often put a city/province in front of the public
            # brand and a business suffix after it (e.g. 合肥+星能玄光+科技).
            # Compare against the suffix-stripped stem as well, so the brand
            # and legal name collapse to one article-local entity.
            legal_brand_stem = re.sub(
                r"(?:科技|智能|机器人|半导体|电子|电气|航空|航天|能源|材料|工业|信息|通信)$",
                "",
                legal_stem,
            )
            location_prefixed_alias = bool(
                3 <= len(other_name) < len(legal_stem)
                and (
                    legal_stem.endswith(other_name)
                    or legal_brand_stem.endswith(other_name)
                )
                and 1 <= len(legal_stem[: -len(other_name)]) <= 6
                and _eligible_name(other_name)
            )
            if not matched_root and not location_prefixed_alias:
                continue
            merge_record(other, owner)
            alias_owner[_key(other_name)] = owner
            records[owner]["aliases"].add(other_name)
            records[owner]["sources"].add("grounded_alias_descendant")

    # A short editorial brand may be the unique grounded prefix of a longer
    # company surface in the same article (字节 -> 字节跳动).  Merge only when
    # there is exactly one possible owner; ambiguous prefixes such as 阿里 in
    # an article that mentions both 阿里巴巴 and 阿里云 remain separate.
    # A directly grounded corporate parent wins over an ambiguous family of
    # product/sub-brand names (for example, 阿里巴巴 versus 阿里云/阿里享造).
    for parent_key, parent_record in list(records.items()):
        if parent_key not in records or "corporate_product_reference" not in parent_record["sources"]:
            continue
        parent_name = parent_record["canonical_name"]
        prefix_rows = [
            (key, record)
            for key, record in list(records.items())
            if key != parent_key
            and re.fullmatch(r"[\u4e00-\u9fff]{2,6}", record["canonical_name"])
            and parent_name.startswith(record["canonical_name"])
        ]
        if not prefix_rows:
            continue
        short_key, short_record = max(
            prefix_rows, key=lambda item: len(item[1]["canonical_name"])
        )
        short_name = short_record["canonical_name"]
        merge_record(short_key, parent_key)
        alias_owner[_key(short_name)] = parent_key
        records[parent_key]["aliases"].add(short_name)
        records[parent_key]["sources"].add("grounded_parent_alias")

    for short_key, short_record in list(records.items()):
        if short_key not in records:
            continue
        short = short_record["canonical_name"]
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,6}", short):
            continue
        owners = [
            owner
            for owner, record in records.items()
            if owner != short_key
            and record["canonical_name"].startswith(short)
            and len(record["canonical_name"]) > len(short)
            and _eligible_name(record["canonical_name"])
            and any(
                _strong_entity_shape(
                    record["canonical_name"],
                    source=source,
                    occurrences=len(
                        re.findall(
                            re.escape(record["canonical_name"]),
                            body,
                            flags=re.IGNORECASE,
                        )
                    ),
                )
                for source in record["sources"]
                if source
                not in {
                    "candidate_subject",
                    "rule_seed",
                    "versioned_brand",
                    "title_brand",
                }
            )
        ]
        if len(owners) != 1:
            continue
        owner = owners[0]
        owner_name = records[owner]["canonical_name"]
        suffix = owner_name[len(short) :]
        short_occurrences = len(re.findall(re.escape(short), body))
        prefer_short_company = bool(
            (
                re.search(r"[A-Za-z0-9]", suffix)
                or re.search(r"文档|办公|助手|智能体|平台|模型|产品|联合", suffix)
                or "grounded_product_child" in records[owner]["sources"]
            )
            and _eligible_name(short)
            and (
                short_occurrences >= 2
                or bool(
                    set(short_record["sources"])
                    & {
                        "action_subject",
                        "company_reference",
                        "organization_role",
                        "reporting_operational_subject",
                        "attached_product_owner",
                        "grounded_product_owner",
                    }
                )
            )
            and not (set(records[owner]["sources"]) & {"legal_name", "explicit_alias"})
            and (
                "corporate_product_reference" not in records[owner]["sources"]
                or "attached_product_owner" in short_record["sources"]
            )
        )
        if prefer_short_company:
            merge_record(owner, short_key)
            alias_owner[_key(owner_name)] = short_key
            records[short_key]["aliases"].add(owner_name)
            records[short_key]["sources"].add("grounded_product_parent")
        else:
            merge_record(short_key, owner)
            alias_owner[_key(short)] = owner
            records[owner]["aliases"].add(short)
            records[owner]["sources"].add("grounded_unique_prefix_alias")

    # The same digest may spell a Latin organization once with and once
    # without its final corporate word (``Thinking Machines`` / ``Thinking
    # Machines Lab``).  Merge an unambiguous word-prefix duplicate locally;
    # this prevents a person or fund reference from becoming a second company
    # lead.  Also merge truncated legal suffixes such as ``...计算有限`` into
    # the article's full ``...计算有限公司`` record.
    for short_key, short_record in list(records.items()):
        if short_key not in records:
            continue
        short = short_record["canonical_name"]
        for owner, owner_record in list(records.items()):
            if owner == short_key or owner not in records:
                continue
            long_name = owner_record["canonical_name"]
            latin_prefix = bool(
                re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+*-]{2,40}", short)
                and re.fullmatch(
                    rf"{re.escape(short)}\s+[A-Za-z][A-Za-z0-9 .&+*-]{{2,24}}",
                    long_name,
                )
                and set(owner_record["sources"]) & {
                    "company_reference",
                    "organization_role",
                    "direct_clause_company",
                    "action_subject",
                    "compact_action_subject",
                    "english_context",
                }
            )
            short_legal_fragment = bool(
                re.search(r"(?:\u6709\u9650|\u80a1\u4efd|\u8d23\u4efb)$", short)
                and "legal_name" in owner_record["sources"]
                and long_name.startswith(short)
                and len(long_name) > len(short)
                and re.search(
                    r"(?:\u6709\u9650\u8d23\u4efb\u516c\u53f8|\u6709\u9650\u516c\u53f8|\u80a1\u4efd\u6709\u9650\u516c\u53f8)$",
                    long_name,
                )
            )
            if latin_prefix or short_legal_fragment:
                merge_record(short_key, owner)
                alias_owner[_key(short)] = owner
                records[owner]["aliases"].add(short)
                records[owner]["sources"].add("grounded_alias_duplicate")
                break

    # OCR can insert a space after the first CJK character of an explicitly
    # bound alias (“中 美瑞康”).  A role regex may then discover only the
    # suffix (“美瑞康”).  Merge that suffix only when exactly one legal seed
    # has an explicit alias ending in it and the missing prefix is at most two
    # characters; otherwise keep it ambiguous and ineligible.
    for short_key, short_record in list(records.items()):
        if short_key not in records:
            continue
        short = short_record["canonical_name"]
        if not (
            re.fullmatch(r"[\u4e00-\u9fff·]{3,8}", short)
            and set(short_record["sources"])
            <= {"organization_role", "company_reference", "reporting_subject"}
        ):
            continue
        owners = []
        for owner, owner_record in records.items():
            if owner == short_key or not (
                set(owner_record["sources"]) & {"legal_name", "explicit_alias"}
            ):
                continue
            surfaces = (
                owner_record["canonical_name"],
                *tuple(owner_record["aliases"]),
            )
            if any(
                surface.endswith(short)
                and 1 <= len(surface) - len(short) <= 2
                for surface in surfaces
            ):
                owners.append(owner)
        if len(owners) != 1:
            continue
        owner = owners[0]
        merge_record(short_key, owner)
        alias_owner[_key(short)] = owner
        records[owner]["aliases"].add(short)
        records[owner]["sources"].add("ocr_suffix_alias")

    # Canonicalization/alias passes can later merge the exact adapter record
    # into a longer action-parser surface. Restore the adapter's canonical
    # spelling after those passes; the malformed surface remains an alias for
    # audit but must not be emitted as the lead company.
    if metadata_company and not metadata_is_issuer and metadata_company_valid(
        metadata_company
    ):
        for owner, record in records.items():
            if "adapter_metadata_company" not in record["sources"]:
                continue
            current = str(record.get("canonical_name") or "")
            if current and current != metadata_company:
                record["aliases"].add(current)
                record["canonical_name"] = metadata_company
            alias_owner[_key(metadata_company)] = owner
            break

    role_person_fragments: set[str] = set()
    for text in (body, article.index.title):
        for match in _ORGANIZATION_ROLE.finditer(text):
            person = re.match(
                r"[\u4e00-\u9fff·]{2,6}", text[match.end("role") :]
            )
            if not person:
                continue
            person_name = person.group(0).rstrip("与和及")
            if len(person_name) < 2:
                continue
            organization = _clean_subject(match.group("name"))
            shorts = list(company_alias_candidates(organization))
            stripped = _LEDGER_BRAND_SUFFIX.sub("", organization)
            if stripped != organization:
                shorts.append(stripped)
            role_person_fragments.update(
                short + person_name for short in shorts if len(short) >= 2
            )

    speaker_names = {
        _clean_subject(match.group("name"))
        for match in _SPEAKER_NAME.finditer(body)
    }
    bulletin_subject_keys = {
        _key(adapter_subject(item.get("subject")))
        for item in item_subject_rows
        if adapter_subject(item.get("subject"))
    }
    metadata_target_keys = set()
    if metadata_company and not metadata_is_issuer and metadata_company_valid(
        metadata_company
    ):
        metadata_target_keys.update(
            _key(alias)
            for alias in company_alias_candidates(metadata_company)
            if alias
        )
    entities: list[ArticleEntity] = []
    for record in records.values():
        canonical = record["canonical_name"]
        sources = tuple(sorted(record["sources"]))
        looks_like_company_speaker = bool(
            re.search(
                r"(?:科技|智能|集团|股份|资本|机器人|半导体|电子|"
                r"生物|医药|医疗|能源|材料|汽车|航空|工业)$",
                canonical,
            )
            or set(sources)
            & {
                "legal_name",
                "explicit_alias",
                "listed_ticker",
                "bulletin_subject",
                "title_bulletin",
                "direct_hosting_company",
            }
            or (
                "direct_bulletin_company" in sources
                and (
                    canonical in article.index.title
                    or len(body) <= 160
                )
            )
        )
        entity_kind = (
            "operating_company"
            if "bulletin_unit_company" in sources
            and (
                canonical not in speaker_names
                or _key(canonical) in bulletin_subject_keys
            )
            else (
                "person_or_role_reference"
                if canonical in role_person_fragments
                or (canonical in speaker_names and not looks_like_company_speaker)
                else _kind(canonical)
            )
        )
        record_surface_keys = {
            _key(alias)
            for surface in (canonical, *tuple(record["aliases"]))
            for alias in company_alias_candidates(surface)
        }
        has_adapter_anchor = bool(
            set(sources)
            & {"adapter_metadata_company", "adapter_metadata_mention"}
        )
        has_operating_anchor = has_adapter_anchor or _record_has_operating_anchor(
            record, body=body, title=article.index.title
        )
        strong_company_sources = {
            "adapter_metadata_company",
            "adapter_metadata_mention",
            "legal_name",
            "explicit_alias",
            "descriptor_company",
            "descriptor_alias",
            "descriptor_context_alias",
            "company_reference",
            "company_surface",
            "investment_target_company",
            "investment_actor_company",
            "rule_seed",
            "candidate_subject",
            "candidate_action_subject",
            "bulletin_unit_company",
            "direct_bulletin_company",
            "title_action",
        }
        title_grounded_subject = bool(
            any(
                surface and surface in article.index.title
                for surface in (canonical, *tuple(record["aliases"]))
            )
            and not re.search(r"\d", canonical)
        )
        explicit_company_descriptor = any(
            re.search(
                rf"{re.escape(surface)}(?:\u516c\u53f8|\u4f01\u4e1a)(?:\u662f|\u5750\u843d|\u8fd8|\u53d1\u5e03|\u62e5\u6709|\u6210\u4e3a)",
                body,
            )
            for surface in (canonical, *tuple(record["aliases"]))
            if surface
        )
        long_feature_weak_entity = (
            routed.document_family in {"long_feature", "interview_commentary", "commentary"}
            and not (set(sources) & strong_company_sources)
            and not title_grounded_subject
            and not explicit_company_descriptor
        )
        eligible = bool(
            entity_kind == "operating_company"
            and has_operating_anchor
            and not long_feature_weak_entity
            and not _record_is_out_of_scope_media(record, body=body)
            and (
                not item_subject_rows
                or _key(canonical) in bulletin_subject_keys
            )
            # When a dedicated adapter supplied a primary company, it is a
            # bounded target constraint: free-form fragments, products, and
            # counterparties remain in the audit ledger but cannot become
            # separate operating-company leads.
            and (
                not metadata_target_keys
                or bool(record_surface_keys & metadata_target_keys)
            )
        )
        lead_scope_eligible = bool(
            eligible
            and not (
                "listed_ticker" in record["sources"]
                and re.search(r"(?:转让|参股公司|股权)", body)
                and not record["sources"]
                & {
                    "action_subject",
                    "compact_action_subject",
                    "company_surface",
                    "investment_target_company",
                }
            )
        )
        material = (
            f"{article.index.source_id}\0{article.index.source_article_id}\0{_key(canonical)}"
        )
        entities.append(
            ArticleEntity(
                entity_id=f"ae_{sha1(material.encode('utf-8')).hexdigest()[:14]}",
                canonical_name=canonical,
                entity_kind=entity_kind,
                operating_subject_eligible=eligible,
                aliases=tuple(sorted(record["aliases"])),
                mentions=tuple(
                    sorted(
                        set(record["mentions"]),
                        key=lambda item: (item.char_start, item.char_end, item.text),
                    )
                ),
                discovery_sources=sources,
                lead_scope_eligible=lead_scope_eligible,
            )
        )
    ordered_entities = tuple(sorted(entities, key=lambda item: item.entity_id))
    eligible_ids = {
        entity.entity_id
        for entity in ordered_entities
        if entity.operating_subject_eligible
    }
    scopes: list[EntityScope] = []
    for item in item_subject_rows:
        subject = adapter_subject(item.get("subject"))
        try:
            scope_start = int(item.get("char_start"))
            scope_end = int(item.get("char_end"))
        except (TypeError, ValueError):
            continue
        if scope_start < 0 or scope_end <= scope_start or scope_end > len(body):
            continue
        query_keys = {_key(alias) for alias in company_alias_candidates(subject)}
        matches = [
            entity
            for entity in ordered_entities
            if entity.operating_subject_eligible
            and query_keys
            & {
                _key(alias)
                for surface in (entity.canonical_name, *entity.aliases)
                for alias in company_alias_candidates(surface)
            }
        ]
        if len(matches) != 1:
            continue
        entity_id = matches[0].entity_id
        material = (
            f"{article.index.source_id}\0{article.index.source_article_id}\0"
            f"adapter-item\0{scope_start}\0{scope_end}\0{entity_id}"
        )
        scopes.append(
            EntityScope(
                scope_id=f"es_{sha1(material.encode('utf-8')).hexdigest()[:14]}",
                char_start=scope_start,
                char_end=scope_end,
                entity_ids=(entity_id,),
                source="adapter_item_subject",
            )
        )

    marker_matches = list(_MEDIA_MARKER.finditer(body))
    for index, marker in enumerate(marker_matches):
        scope_start = marker.end()
        scope_end = (
            marker_matches[index + 1].start()
            if index + 1 < len(marker_matches)
            else len(body)
        )
        scoped_ids = tuple(
            dict.fromkeys(
                entity.entity_id
                for entity in ordered_entities
                if entity.entity_id in eligible_ids
                and any(
                    scope_start <= mention.char_start < scope_end
                    for mention in entity.mentions
                )
            )
        )
        if not scoped_ids:
            continue
        material = (
            f"{article.index.source_id}\0{article.index.source_article_id}\0"
            f"{scope_start}\0{scope_end}\0{'|'.join(scoped_ids)}"
        )
        scopes.append(
            EntityScope(
                scope_id=f"es_{sha1(material.encode('utf-8')).hexdigest()[:14]}",
                char_start=scope_start,
                char_end=scope_end,
                entity_ids=scoped_ids,
                source="media_item",
            )
        )
    role_matches = list(_ORGANIZATION_ROLE.finditer(body))
    speaker_matches = list(_SPEAKER_NAME.finditer(body))
    speaker_entity_ids: dict[str, str] = {}
    for marker in role_matches:
        raw_name = marker.group("name")
        english_tail = re.search(r"[A-Za-z][A-Za-z0-9 .&+*-]{1,40}$", raw_name)
        if english_tail and re.search(r"[\u4e00-\u9fff]", raw_name[: english_tail.start()]):
            raw_name = english_tail.group(0)
        organization = _clean_subject(raw_name)
        query_keys = {_key(item) for item in company_alias_candidates(organization)}
        matches = [
            entity
            for entity in ordered_entities
            if entity.entity_id in eligible_ids
            and query_keys
            & {
                _key(alias)
                for surface in (entity.canonical_name, *entity.aliases)
                for alias in company_alias_candidates(surface)
            }
        ]
        if len(matches) != 1:
            continue
        intro_tail = body[marker.end("role") : min(len(body), marker.end("role") + 120)]
        for speaker in speaker_matches:
            speaker_name = _clean_subject(speaker.group("name"))
            if speaker_name and speaker_name in intro_tail:
                speaker_entity_ids[speaker_name] = matches[0].entity_id

    # Interview transcripts switch subject through speaker labels.  Bind only
    # labels whose person was explicitly introduced with an organization role;
    # generic speakers, reporters and hosts never inherit a company.
    for index, speaker in enumerate(speaker_matches):
        speaker_name = _clean_subject(speaker.group("name"))
        entity_id = speaker_entity_ids.get(speaker_name)
        if not entity_id:
            continue
        scope_start = speaker.end()
        scope_end = (
            speaker_matches[index + 1].start()
            if index + 1 < len(speaker_matches)
            else len(body)
        )
        if scope_end <= scope_start:
            continue
        material = (
            f"{article.index.source_id}\0{article.index.source_article_id}\0"
            f"speaker-label\0{scope_start}\0{scope_end}\0{entity_id}"
        )
        scopes.append(
            EntityScope(
                scope_id=f"es_{sha1(material.encode('utf-8')).hexdigest()[:14]}",
                char_start=scope_start,
                char_end=scope_end,
                entity_ids=(entity_id,),
                source="organization_speaker_label",
            )
        )
    return ArticleEntityLedger(
        version="article-entity-ledger-v3",
        source_id=article.index.source_id,
        source_article_id=article.index.source_article_id,
        entities=ordered_entities,
        scopes=tuple(scopes),
    )


def bind_candidate_subjects(
    candidates: Iterable[Mapping[str, Any]],
    ledger: ArticleEntityLedger,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in candidates:
        candidate = dict(source)
        quote = str(candidate.get("quote") or "")
        hint = str(candidate.get("subject_hint") or "")
        hinted = ledger.entity_for_name(hint) if hint else None
        allowed = {
            entity.entity_id
            for entity in ledger.eligible()
            if any(
                alias and alias in quote
                for alias in (entity.canonical_name, *entity.aliases)
            )
        }
        if hinted and hinted.operating_subject_eligible:
            allowed.add(hinted.entity_id)
        candidate["allowed_subject_entity_ids"] = sorted(allowed)
        candidate["primary_subject_entity_id"] = (
            hinted.entity_id
            if hinted and hinted.entity_id in allowed
            else (next(iter(allowed)) if len(allowed) == 1 else "")
        )
        candidate["subject_binding"] = (
            "hinted" if hinted and hinted.entity_id in allowed else "mention_set"
        )
        output.append(candidate)
    return output


def entity_contract_filter(
    events: Iterable[SemanticEvent],
    candidates: Iterable[Mapping[str, Any]],
    ledger: ArticleEntityLedger,
) -> tuple[list[SemanticEvent], list[str]]:
    candidate_by_claim = {
        str(claim_id): candidate
        for candidate in candidates
        for claim_id in candidate.get("required_claim_ids") or []
    }
    entities = ledger.by_id()
    accepted: list[SemanticEvent] = []
    issues: list[str] = []
    for event in events:
        entity = entities.get(event.subject_entity_id)
        if entity is None or not entity.operating_subject_eligible:
            issues.append(f"unknown_or_ineligible_entity:{event.subject_entity_id}")
            continue
        if _key(event.canonical_company) not in {
            _key(entity.canonical_name),
            *map(_key, entity.aliases),
        }:
            issues.append(f"entity_company_mismatch:{event.subject_entity_id}")
            continue
        allowed = {
            str(entity_id)
            for claim_id in event.claim_ids
            for entity_id in candidate_by_claim.get(claim_id, {}).get(
                "allowed_subject_entity_ids", []
            )
        }
        if event.claim_ids and event.subject_entity_id not in allowed:
            issues.append(f"claim_entity_mismatch:{event.subject_entity_id}")
            continue
        accepted.append(replace(event, canonical_company=entity.canonical_name))
    return accepted, issues


__all__ = [
    "ArticleEntity",
    "ArticleEntityLedger",
    "EntityMention",
    "EntityScope",
    "bind_candidate_subjects",
    "build_article_entity_ledger",
    "entity_contract_filter",
]
