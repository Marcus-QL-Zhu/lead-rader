"""Evidence-bound MiniMax semantic and ambiguity processor."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from hashlib import sha1
import json
import re
from typing import Any, Protocol

from json_repair import repair_json

from .body_scope import clean_semantic_body_scope
from .entities import canonical_company_name, company_alias_candidates, is_company_like
from .models import CleanArticle, SemanticEvent, SourceChannel


PROMPT_VERSION = "aggregate-semantic-v22"
ALLOWED_EVENT_TYPES = frozenset(
    {
        "funding",
        "executive_change",
        "factory_or_capacity",
        "major_order",
        "partnership",
        "technical_milestone",
        "new_site_or_entity",
        "regulatory_or_clinical",
        "policy_or_standard",
        "procurement_tender",
        "customer_validation",
        "merger_acquisition",
        "ipo_or_listing",
        "enterprise_system",
        "other",
    }
)
ALLOWED_CONFIDENCE = frozenset({"high", "medium", "low", "unknown"})
ALLOWED_EVENT_STATUS = frozenset({"completed", "started", "target", "cumulative"})
_UNNAMED_INVESTOR = re.compile(
    r"^(?:"
    r"\u77e5\u540d\u4ea7\u4e1a\u65b9|"
    r".{0,20}\u65e9\u671f\u5929\u4f7f\u6295\u8d44\u4eba|"
    r"\u591a\u5bb6.{0,20}(?:\u673a\u6784|\u4ea7\u4e1a\u65b9)|"
    r"\u6570\u5bb6.{0,20}(?:\u673a\u6784|\u4ea7\u4e1a\u65b9)|"
    r"\u90e8\u5206\u8001\u80a1\u4e1c|"
    r"\u8001\u80a1\u4e1c|"
    r"\u672a\u62ab\u9732|"
    r"\u6295\u8d44\u65b9"
    r")$"
)


class PromptRunner(Protocol):
    def run(
        self,
        prompt: str,
        *,
        session_id: str,
        system_prompt: str = "",
    ) -> str: ...


class SemanticOutputError(ValueError):
    pass


SYSTEM_PROMPT = (
    "\u4f60\u662f\u805a\u5408\u65b0\u95fb\u4e8b\u5b9e\u62bd"
    "\u53d6\u5668\u3002\u53ea\u6d88\u89e3\u8bed\u4e49\u548c"
    "\u4e3b\u4f53\u6b67\u4e49\uff0c\u4e0d\u8865\u5145\u5916"
    "\u90e8\u77e5\u8bc6\u3002"
    "\u4ec5\u8f93\u51fa\u4e00\u4e2aJSON\u5bf9\u8c61\uff1a"
    '{"events":[...],"ambiguities":[...]}\u3002'
    "\u6bcf\u4e2aevent\u5b57\u6bb5\uff1acompany,event_type,"
    "industry_tags,funding_round,funding_amount,"
    "cumulative_funding_amount,investors,event_status,"
    "event_summary,evidence_quotes,confidence\u3002"
    "event_type\u53ea\u80fd\u4ecefunding,executive_change,"
    "factory_or_capacity,major_order,partnership,"
    "technical_milestone,new_site_or_entity,"
    "regulatory_or_clinical,policy_or_standard,"
    "procurement_tender,customer_validation,"
    "merger_acquisition,ipo_or_listing,enterprise_system,"
    "other\u4e2d\u9009\u62e9\u3002"
    "\u5bf9\u4e00\u7bc7\u6587\u7ae0\u4e2d\u6bcf\u4e2a\u72ec"
    "\u7acb\u4e3b\u4f53\u548c\u72ec\u7acb\u8f6e\u6b21\u5206"
    "\u522b\u751f\u6210event\uff0c\u4e0d\u5f97\u9057\u6f0f\u3002"
    "event_status\u53ea\u7528completed/started/target/cumulative\u3002"
    "funding_amount\u53ea\u8868\u793a\u672c\u6b21\u4e8b\u4ef6"
    "\u91d1\u989d\uff1b\u7d2f\u8ba1\u53e3\u5f84\u53ea\u653e"
    "\u5728cumulative_funding_amount\u3002"
    "\u53ea\u6709\u53ef\u8bc6\u522b\u7684\u5177\u4f53\u4eba"
    "\u540d\u6216\u673a\u6784\u540d\u53ef\u8fdbinvestors\uff1b"
    "\u5982\u539f\u6587\u53ea\u8bf4\u67d0\u673a\u6784\u65d7\u4e0b\u672a\u5177\u540d\u57fa\u91d1\uff0c"
    "\u53ea\u8f93\u51fa\u53ef\u8bc6\u522b\u7684\u6bcd\u673a\u6784\uff0c\u4e0d\u628a\u65d7\u4e0b\u57fa\u91d1\u53e6\u7b97\u4e00\u4e2a\u6295\u8d44\u65b9\uff1b"
    "\u5982\u539f\u6587\u660e\u786e\u5199\u6bcd\u673a\u6784\u65d7\u4e0b\u7684\u5177\u540d\u5b50\u673a\u6784\u6216\u57fa\u91d1\uff0c"
    "investors\u53ea\u586b\u5177\u540d\u5b50\u673a\u6784\u6216\u57fa\u91d1\uff0c\u4e0d\u586b\u6bcd\u673a\u6784\uff1b"
    "\u53ea\u6709\u5b50\u673a\u6784\u672a\u5177\u540d\u65f6\u624d\u586b\u6bcd\u673a\u6784\u3002"
    "\u672a\u5177\u540d\u63cf\u8ff0\u653eambiguities\u3002"
    "company\u5fc5\u987b\u662f\u627f\u62c5\u8be5\u4e8b\u4ef6\u7684\u516c\u53f8\u4e3b\u4f53\uff0c"
    "\u4e0d\u5f97\u586b\u65f6\u95f4\u3001\u91d1\u989d\u3001\u5a92\u4f53\u3001\u5206\u6790\u5e08\u6216\u6295\u8d44\u65b9\u3002"
    "company\u3001\u91d1\u989d\u3001\u8f6e\u6b21\u3001"
    "\u6295\u8d44\u4eba\u548cevidence_quotes\u5fc5\u987b"
    "\u9010\u5b57\u51fa\u73b0\u5728\u8f93\u5165\u539f\u6587\u3002"
    "\u65e0\u6cd5\u786e\u5b9a\u65f6\u4e0d\u751f\u6210event\uff0c"
    "\u5728ambiguities\u8bf4\u660e\u3002"
    "ambiguities\u5fc5\u987b\u662f\u5b57\u7b26\u4e32\u6570\u7ec4\uff0c"
    "\u6bcf\u4e2a\u5143\u7d20\u7528\u4e00\u53e5\u8bdd\u8bf4\u660e\u6b67\u4e49\u3002"
    "evidence_quotes\u7684\u7b2c\u4e00\u6761\u5fc5\u987b\u662f\u4e8b\u4ef6\u4e3b\u8bc1\u636e\uff0c"
    "\u5305\u542b\u4e3b\u4f53\u548c\u4e8b\u4ef6\u52a8\u4f5c\uff1b\u5176\u4ed6\u5f15\u6587\u53ea\u80fd\u8865\u5145\u540c\u4e00\u4e8b\u4ef6\u3002"
    "rule_seed\u53ea\u662f\u53ef\u80fd\u9519\u8bef\u6216\u4e0d\u5b8c\u6574\u7684\u5019\u9009\uff0c"
    "\u5fc5\u987b\u4ee5\u6b63\u6587\u4e3a\u51c6\uff1b\u7ea0\u6b63\u9519\u8befseed\uff0c"
    "\u5e76\u8f93\u51fa\u6ca1\u6709seed\u4f46\u6b63\u6587\u660e\u786e\u652f\u6301\u7684\u5168\u90e8\u4e8b\u4ef6\u3002"
    "\u4e0d\u5f97\u4ece\u5ef6\u5c55\u9605\u8bfb\u3001\u76f8\u5173\u9605\u8bfb\u6216\u63a8\u8350\u9605\u8bfb\u7684\u6807\u9898\u4e2d\u62bd\u53d6\u4e8b\u4ef6\u3002"
    "candidate_ledger\u662f\u786e\u5b9a\u6027\u89c4\u5219\u627e\u5230\u7684\u5f85\u6838\u5019\u9009\uff1b"
    "\u6bcf\u4e2a\u5019\u9009\u90fd\u5fc5\u987b\u8f93\u51fa\u5bf9\u5e94event\u3002"
    "rule_seed\u4e2d\u6bcf\u4e2aseed\u4e5f\u5fc5\u987b\u88abevent\u8986\u76d6\u6216\u7ea0\u6b63\uff0c"
    "\u4e0d\u5f97\u9759\u9ed8\u5ffd\u7565seed\u3002"
    "\u5408\u4f5c\u4e8b\u4ef6\u7684company\u53ea\u586b\u4e00\u4e2a\u53ef\u80fd\u4ea7\u751f\u62db\u8058\u7684\u7ecf\u8425\u4e3b\u4f53\uff1b"
    "\u4e0d\u5f97\u628a\u591a\u4e2a\u5408\u4f5c\u65b9\u7528\u2018\u4e0e\u2019\u6216\u2018\u548c\u2019\u62fc\u6210company\u3002"
    "\u82e5\u5408\u4f5c\u53cc\u65b9\u90fd\u662f\u653f\u5e9c\u3001\u59d4\u5458\u4f1a\u3001\u534f\u4f1a\u6216\u975e\u7ecf\u8425\u6027\u516c\u5171\u673a\u6784\uff0c"
    "\u5219\u4e0d\u751f\u6210event\u3002"
)


class MiniMaxSemanticProcessor:
    def __init__(self, runner: PromptRunner | None) -> None:
        self.runner = runner
        self.model_identity = self._runner_identity(runner)
        self.last_audit: dict[str, Any] = {}

    @property
    def cache_key(self) -> str:
        return f"{PROMPT_VERSION}|{self.model_identity}"

    @staticmethod
    def _runner_identity(runner: PromptRunner | None) -> str:
        if runner is None:
            return "rules-only"
        config = getattr(runner, "config", None)
        provider = str(getattr(config, "provider", "") or "").strip()
        model = str(getattr(config, "model", "") or "").strip()
        if provider and model:
            return f"{provider}/{model}"
        if model:
            return model
        return type(runner).__name__

    def process(
        self,
        channel: SourceChannel,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        article = replace(
            article,
            clean_body=clean_semantic_body_scope(article.clean_body),
        )
        rule_events = self._normalize_rule_events(article, rule_events)
        chunks = self._semantic_chunks(article.clean_body)
        candidates = self._event_candidates(article.clean_body)
        audit: dict[str, Any] = {
            "source_id": article.index.source_id,
            "source_article_id": article.index.source_article_id,
            "prompt_version": PROMPT_VERSION,
            "model_identity": self.model_identity,
            "cache_key": self.cache_key,
            "index_content_hash": article.index.content_hash,
            "article_content_hash": article.content_hash,
            "status": "rules_only",
            "first_response": "",
            "repair_response": "",
            "error": "",
            "rule_seed_count": len(rule_events),
            "final_event_count": len(rule_events),
            "rules_preserved_count": len(rule_events),
            "omissions_detected": 0,
            "chunk_count": len(chunks),
            "chunk_statuses": [],
            "candidate_count": len(candidates),
            "unmapped_candidate_count": 0,
            "unmapped_candidate_ids": [],
        }
        self.last_audit = audit
        if self.runner is None:
            if not rule_events:
                audit["status"] = "no_rule_seed"
            return rule_events
        all_events: list[SemanticEvent] = []
        first_responses: list[str] = []
        repair_responses: list[str] = []
        errors: list[str] = []
        statuses: list[str] = []
        model_ambiguities: list[str] = []
        for chunk_index, chunk in enumerate(chunks, start=1):
            chunk_index_record = article.index
            if chunk_index > 1:
                chunk_index_record = replace(
                    article.index,
                    title="",
                    summary="",
                )
            chunk_article = replace(
                article,
                index=chunk_index_record,
                clean_body=chunk,
            )
            chunk_rules = self._rules_for_chunk(rule_events, chunk_article)
            prompt = self._prompt(channel, chunk_article, chunk_rules)
            response = ""
            repair = ""
            first_error_text = ""
            try:
                response = self.runner.run(
                    prompt,
                    session_id=(
                        f"aggregate:{article.index.source_id}:"
                        f"{article.index.source_article_id}:"
                        f"{article.content_hash[:12]}:chunk-{chunk_index}"
                    ),
                    system_prompt=SYSTEM_PROMPT,
                )
                payload = self._parse_json(response)
                events = self._validate_payload(
                    chunk_article,
                    payload,
                    chunk_rules,
                )
                ambiguities = [
                    value
                    for value in payload.get("ambiguities", [])
                    if isinstance(value, str)
                ]
                self._assert_chunk_adjudicated(
                    events,
                    chunk_rules,
                    self._event_candidates(chunk),
                )
                model_ambiguities.extend(
                    ambiguities
                )
                events = self._salvage_grounded_investors(
                    chunk_article,
                    events,
                    (response,),
                    "",
                )
                statuses.append("accepted")
                all_events.extend(events)
                first_responses.append(response)
                repair_responses.append("")
                continue
            except Exception as first_error:
                first_error_text = (
                    f"{type(first_error).__name__}: {first_error}"
                )
            try:
                repair = self.runner.run(
                    "The prior output failed JSON or evidence grounding validation. "
                    "Return one corrected strict JSON object under the original system "
                    "constraints. Do not use Markdown fences. Escape every ASCII double "
                    "quote inside string values as \\\"; prefer Chinese corner quotes "
                    "inside summaries. Do not repeat a rejected field.\n"
                    f"Original input: {prompt}\n"
                    f"Prior output: {response[:4000]}\n"
                    f"Validation error: {first_error_text}",
                    session_id=(
                        f"aggregate-repair:{article.index.source_id}:"
                        f"{article.index.source_article_id}:"
                        f"{article.content_hash[:12]}:chunk-{chunk_index}"
                    ),
                    system_prompt=SYSTEM_PROMPT,
                )
                payload = self._parse_json(repair, allow_syntax_repair=True)
                events = self._validate_payload(
                    chunk_article,
                    payload,
                    chunk_rules,
                )
                ambiguities = [
                    value
                    for value in payload.get("ambiguities", [])
                    if isinstance(value, str)
                ]
                self._assert_chunk_adjudicated(
                    events,
                    chunk_rules,
                    self._event_candidates(chunk),
                )
                model_ambiguities.extend(
                    ambiguities
                )
                events = self._salvage_grounded_investors(
                    chunk_article,
                    events,
                    (response, repair),
                    "",
                )
                statuses.append("repaired")
                all_events.extend(events)
            except Exception as repair_error:
                statuses.append("fallback_to_rules")
                errors.append(
                    f"chunk {chunk_index}: {first_error_text}; repair "
                    f"{type(repair_error).__name__}: {repair_error}"
                )
                marker = (
                    f"minimax_validation_failed:"
                    f"{type(repair_error).__name__}"
                )
                all_events.extend(
                    self._salvage_grounded_investors(
                        chunk_article,
                        chunk_rules,
                        (response, repair),
                        marker,
                    )
                )
            first_responses.append(response)
            repair_responses.append(repair)

        events = self._normalize_final_events(all_events)
        audit["chunk_statuses"] = statuses
        audit["first_response"] = self._audit_responses(first_responses)
        audit["repair_response"] = self._audit_responses(repair_responses)
        audit["error"] = "; ".join(errors)
        if "fallback_to_rules" in statuses:
            audit["status"] = "fallback_to_rules"
        elif "repaired" in statuses:
            audit["status"] = "repaired"
        else:
            audit["status"] = "accepted"
        self._complete_audit(
            audit,
            events,
            rule_events,
            candidates=candidates,
            model_ambiguities=model_ambiguities,
        )
        if audit["unmapped_candidate_count"]:
            audit["status"] = "fallback_to_rules"
            suffix = (
                "full-article candidate ledger remained incomplete: "
                f"{audit['unmapped_candidate_ids']}"
            )
            audit["error"] = "; ".join(
                value for value in (audit["error"], suffix) if value
            )
            events = self._normalize_final_events(rule_events)
            audit["final_event_count"] = len(events)
            audit["rules_preserved_count"] = len(events)
        return events

    @staticmethod
    def _audit_responses(responses: list[str]) -> str:
        if len(responses) == 1:
            return responses[0]
        return json.dumps(responses, ensure_ascii=False)

    @staticmethod
    def _semantic_chunks(body: str, *, max_chars: int = 7000) -> list[str]:
        """Split only long digests, keeping ordinary articles single-call."""

        if len(body) <= 10000:
            return [body]
        units = [
            value.strip()
            for value in re.split(r"(?<=[。！？；;])\s*", body)
            if value.strip()
        ]
        chunks: list[str] = []
        current = ""
        for unit in units:
            if len(unit) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                overlap = min(400, max_chars // 5)
                step = max(1, max_chars - overlap)
                chunks.extend(
                    unit[start : start + max_chars]
                    for start in range(0, len(unit), step)
                )
                continue
            candidate = f"{current} {unit}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = unit
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks or [body]

    @staticmethod
    def _rules_for_chunk(
        rule_events: list[SemanticEvent],
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        text = (
            f"{article.index.title}\n{article.index.summary}\n"
            f"{article.clean_body}"
        )
        return [
            event
            for event in rule_events
            if any(
                quote and (quote in text or text.find(quote[:80]) >= 0)
                for quote in event.evidence_quotes
            )
        ]

    @staticmethod
    def _event_candidates(body: str) -> list[dict[str, str]]:
        """Build a conservative, auditable recall ledger for strong signals."""

        patterns = {
            "funding": (
                r"(?:完成|获得|斩获|宣布完成|启动|开启|开始|筹集|募集|募资|"
                r"洽谈|谈判|拟).{0,100}(?:融资|Pre[ -]?IPO|"
                r"[A-H](?:\+{1,2})?轮|天使轮|种子轮)"
            ),
            "executive_change": (
                r"(?:任命|聘任|出任|担任|加入|离任|辞任|升任|接任)"
                r".{0,60}(?:董事长|总裁|CEO|首席|总经理|负责人|一号位|"
                r"副总裁|VP|总监)"
            ),
            "ipo_or_listing": (
                r"(?:递表|提交上市申请|启动IPO|港股IPO|完成上市|正式上市|挂牌)"
            ),
            "major_order": (
                r"(?:中标|签订|签署|获得).{0,80}(?:订单|合同|采购项目)"
            ),
            "factory_or_capacity": (
                r"(?:开工|投产|扩产|建成|落地).{0,80}(?:工厂|产线|基地|产能)|"
                r"(?:工厂|产线|基地|产能).{0,80}(?:开工|投产|扩产|建成|落地)"
            ),
            "partnership": (
                r"(?:达成|签署|建立).{0,60}(?:战略合作|合作协议|合资)"
            ),
            "technical_milestone": (
                r"(?:发布|推出|首发|量产|交付|获批).{0,100}"
                r"(?:芯片|机器人|模型|产品|平台|设备|系统|药物|卫星|火箭|技术)|"
                r"(?:芯片|机器人|模型|产品|平台|设备|系统|药物|卫星|火箭|技术)"
                r".{0,100}(?:发布|推出|首发|量产|交付|下线|获批)"
            ),
            "new_site_or_entity": (
                r"(?:成立|设立|注册|落地|启用).{0,80}"
                r"(?:公司|子公司|中心|基地|实验室|研究院)"
            ),
            "regulatory_or_clinical": (
                r"(?:获批|批准|受理|取得|获得).{0,80}"
                r"(?:许可|认证|资质|临床|注册证|测试牌照|测试许可)"
            ),
            "policy_or_standard": (
                r"(?:发布|印发|出台|实施|征求意见).{0,100}"
                r"(?:政策|标准|办法|条例|规范|通知)"
            ),
            "procurement_tender": (
                r"(?:启动|发布|参与|完成).{0,60}(?:招标|采购)|"
                r"(?:招标|采购).{0,60}(?:启动|发布|中标|入围)"
            ),
            "customer_validation": (
                r"(?:客户|车企|医院|高校|实验室).{0,100}"
                r"(?:采用|导入|验证|定点|复购)"
            ),
            "merger_acquisition": (
                r"(?:拟|宣布|完成|同意).{0,80}(?:收购|并购|合并|出售|受让)|"
                r"(?:收购|并购|合并).{0,80}(?:完成|获批|交割)"
            ),
            "enterprise_system": (
                r"(?:上线|部署|启用|建设|升级).{0,100}"
                r"(?:ERP|MES|CRM|企业系统|管理系统|业务平台)"
            ),
        }
        sentences = [
            value.strip()
            for value in re.split(r"(?<=[。！？；;])\s*", body)
            if value.strip()
        ]
        windows: list[str] = []
        for sentence in sentences:
            if len(sentence) <= 1400:
                windows.append(sentence)
                continue
            window_size = 1400
            overlap = 300
            windows.extend(
                sentence[start : start + window_size]
                for start in range(0, len(sentence), window_size - overlap)
            )
        candidates: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for sentence in windows:
            if MiniMaxSemanticProcessor._is_historical_background(sentence):
                continue
            if re.search(
                r"(?:例如|比如|典型案例|历史上|过去).{0,160}"
                r"(?:获得融资|完成融资|资本注入)",
                sentence,
            ):
                continue
            for event_type, pattern in patterns.items():
                pattern_match = re.search(pattern, sentence, re.I)
                if not pattern_match:
                    continue
                if MiniMaxSemanticProcessor._funding_use_only_nonfunding(
                    event_type,
                    sentence,
                ):
                    continue
                if event_type == "technical_milestone" and re.search(
                    r"\u4ea7\u54c1(?:\u4ecb\u7ecd|\u5ba3\u4f20|\u56de\u987e)",
                    sentence,
                ):
                    continue
                if event_type == "customer_validation" and re.search(
                    r"\u5ba2\u6237\u9a8c\u8bc1(?:\u9636\u6bb5|\u671f|\u4e2d)",
                    sentence,
                ):
                    continue
                if event_type == "new_site_or_entity" and re.search(
                    r"(?:\u79c1\u52df|\u80a1\u6743|\u4ea7\u4e1a)\u6295\u8d44\u57fa\u91d1|"
                    r"\u57fa\u91d1\u5408\u4f19\u4f01\u4e1a",
                    sentence,
                ):
                    continue
                if event_type == "partnership" and (
                    re.search(
                        r"(?:\u59d4\u5458\u4f1a|\u534f\u4f1a|\u653f\u5e9c|\u8ba4\u53ef\u673a\u6784)",
                        sentence,
                    )
                    and not re.search(
                        r"(?:\u516c\u53f8|\u96c6\u56e2|\u80a1\u4efd|\u79d1\u6280|\u7535\u5b50|"
                        r"\u673a\u5668\u4eba|\u667a\u80fd|\u533b\u7597|\u5236\u836f|\u6e2f\u52a1)",
                        sentence,
                    )
                ):
                    continue
                rounds = tuple(
                    dict.fromkeys(
                        re.findall(
                        r"(?:Pre[ -]?)?[A-H](?:\+{1,2})?\u8f6e|"
                        r"\u5929\u4f7f\u8f6e|\u79cd\u5b50\u8f6e|Pre[ -]?IPO",
                        sentence,
                        re.I,
                        )
                    )
                )
                if re.search(
                    r"[A-H](?:\+{1,2})?\u8f6e[\uff08(]Pre[ -]?IPO\u8f6e?[\uff09)]",
                    sentence,
                    re.I,
                ):
                    rounds = tuple(
                        value
                        for value in rounds
                        if not re.fullmatch(
                            r"Pre[ -]?IPO(?:\u8f6e)?",
                            value,
                            re.I,
                        )
                    )
                subject = re.split(
                    r"(?:\u5b8c\u6210|\u83b7\u5f97|\u65a9\u83b7|\u5ba3\u5e03|\u542f\u52a8|\u5f00\u542f|"
                    r"\u5f00\u59cb|\u7b79\u96c6|\u52df\u96c6|\u52df\u8d44|\u6d3d\u8c08|\u8c08\u5224|"
                    r"\u4efb\u547d|\u8058\u4efb|\u53d1\u5e03|\u63a8\u51fa|\u53d6\u5f97)",
                    sentence,
                    maxsplit=1,
                )[0][-40:]
                subject_hint = MiniMaxSemanticProcessor._candidate_subject_hint(
                    subject
                )
                partner_subject_hints: tuple[str, ...] = ()
                if event_type == "funding" and "\u5206\u522b" in subject:
                    partner_subject_hints = tuple(
                        value
                        for value in (
                            MiniMaxSemanticProcessor._candidate_subject_hint(partner)
                            for partner in re.split(
                                r"\u4e0e|\u548c",
                                subject.replace("\u5206\u522b", ""),
                            )
                        )
                        if value
                    )
                    if len(partner_subject_hints) > 1:
                        subject_hint = ""
                candidate_rounds = (
                    rounds if event_type == "funding" and rounds else ("",)
                )
                quote_start = max(0, pattern_match.start() - 240)
                quote_end = min(len(sentence), pattern_match.end() + 240)
                candidate_quote = sentence[quote_start:quote_end]
                for round_name in candidate_rounds:
                    normalized_round = MiniMaxSemanticProcessor._normalize_round(
                        round_name
                    )
                    key = (
                        event_type,
                        f"{subject}|{normalized_round}",
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    digest = sha1(
                        f"{event_type}\0{normalized_round}\0{candidate_quote}".encode(
                            "utf-8"
                        )
                    ).hexdigest()[:10]
                    candidate_record = {
                            "id": f"c_{digest}",
                            "event_type": event_type,
                            "funding_round": normalized_round,
                            "quote": candidate_quote,
                        }
                    if event_type == "funding" and subject_hint:
                        candidate_record["subject_hint"] = subject_hint
                    candidates.append(candidate_record)
                    for partner_subject_hint in partner_subject_hints:
                        partner_key = (
                            event_type,
                            f"{partner_subject_hint}|{normalized_round}",
                        )
                        if partner_key in seen:
                            continue
                        seen.add(partner_key)
                        partner_digest = sha1(
                            f"{event_type}\0{normalized_round}\0"
                            f"{partner_subject_hint}\0{candidate_quote}".encode(
                                "utf-8"
                            )
                        ).hexdigest()[:10]
                        candidates.append(
                            {
                                "id": f"c_{partner_digest}",
                                "event_type": event_type,
                                "funding_round": normalized_round,
                                "subject_hint": partner_subject_hint,
                                "quote": candidate_quote,
                            }
                        )
                if event_type == "funding":
                    clauses = [
                        value.strip()
                        for value in re.split(r"[\uff0c,]", sentence)
                        if value.strip()
                    ]
                    repeated_assertions = list(
                        re.finditer(
                            r"(?P<subject>[A-Za-z0-9\u4e00-\u9fff\uff08\uff09()\u00b7. -]{2,80}?)"
                            r"(?:\u4e5f|\u6b63|\u5df2|\u62df|\u5c06)?"
                            r"(?:\u5b8c\u6210|\u83b7\u5f97|\u65a9\u83b7|\u5ba3\u5e03\u5b8c\u6210|"
                            r"\u542f\u52a8|\u5f00\u542f|\u5f00\u59cb|\u7b79\u96c6|\u52df\u96c6|"
                            r"\u52df\u8d44|\u6d3d\u8c08|\u8c08\u5224)"
                            r"[^\u3002\uff1b]{0,100}?"
                            r"(?:\u878d\u8d44|Pre[ -]?IPO|"
                            r"[A-H](?:\+{1,2})?\u8f6e|\u5929\u4f7f\u8f6e|\u79cd\u5b50\u8f6e)",
                            sentence,
                            re.I,
                        )
                    )
                    if len(repeated_assertions) > 1:
                        clauses.extend(
                            match.group(0).strip()
                            for match in repeated_assertions
                        )
                    clauses = list(dict.fromkeys(clauses))
                    for clause in clauses:
                        if not re.search(pattern, clause, re.I):
                            continue
                        clause_rounds = tuple(
                            dict.fromkeys(
                                re.findall(
                                    r"(?:Pre[ -]?)?[A-H](?:\+{1,2})?\u8f6e|"
                                    r"\u5929\u4f7f\u8f6e|\u79cd\u5b50\u8f6e|"
                                    r"Pre[ -]?IPO",
                                    clause,
                                    re.I,
                                )
                            )
                        ) or ("",)
                        if re.search(
                            r"[A-H](?:\+{1,2})?\u8f6e[\uff08(]Pre[ -]?IPO\u8f6e?[\uff09)]",
                            clause,
                            re.I,
                        ):
                            clause_rounds = tuple(
                                value
                                for value in clause_rounds
                                if not re.fullmatch(
                                    r"Pre[ -]?IPO(?:\u8f6e)?",
                                    value,
                                    re.I,
                                )
                            )
                        clause_subject = re.split(
                            r"(?:\u5b8c\u6210|\u83b7\u5f97|\u65a9\u83b7|\u5ba3\u5e03|"
                            r"\u542f\u52a8|\u5f00\u542f|\u5f00\u59cb|\u7b79\u96c6|"
                            r"\u52df\u96c6|\u52df\u8d44|\u6d3d\u8c08|\u8c08\u5224)",
                            clause,
                            maxsplit=1,
                        )[0][-40:]
                        clause_subject_hint = (
                            MiniMaxSemanticProcessor._candidate_subject_hint(
                                clause_subject
                            )
                        )
                        for clause_round in clause_rounds:
                            normalized_clause_round = (
                                MiniMaxSemanticProcessor._normalize_round(
                                    clause_round
                                )
                            )
                            clause_key = (
                                event_type,
                                f"{clause_subject}|{normalized_clause_round}",
                            )
                            if clause_key in seen:
                                continue
                            seen.add(clause_key)
                            clause_digest = sha1(
                                f"{event_type}\0{normalized_clause_round}\0{clause}".encode(
                                    "utf-8"
                                )
                            ).hexdigest()[:10]
                            candidates.append(
                                {
                                    "id": f"c_{clause_digest}",
                                    "event_type": event_type,
                                    "funding_round": normalized_clause_round,
                                    "quote": clause[:500],
                                }
                            )
                            if clause_subject_hint:
                                candidates[-1]["subject_hint"] = clause_subject_hint
        return candidates

    @staticmethod
    def _candidate_subject_hint(value: str) -> str:
        subject = value.strip(
            " \t\n\u4e5f\u53c8\u5e76\u540c\u65f6\u4e14\u5df2\u6b63\u62df\u5c06"
        )
        if not subject or re.match(
            r"(?:\u56e0|\u672c\u8f6e|\u672c\u6b21|\u539f\u5b9a|\u8be5\u8f6e|\u5176|\u6b64\u524d)",
            subject,
        ):
            return ""
        return subject if is_company_like(subject) else ""

    @classmethod
    def _assert_chunk_adjudicated(
        cls,
        events: list[SemanticEvent],
        rule_events: list[SemanticEvent],
        candidates: list[dict[str, str]],
    ) -> None:
        missing_candidates = [
            candidate["id"]
            for candidate in candidates
            if not cls._candidate_is_covered(candidate, events)
        ]
        missing_seeds = [
            cls._rule_seed_id(seed)
            for seed in rule_events
            if not cls._seed_is_adjudicated(seed, events, rule_events)
        ]
        if missing_candidates or missing_seeds:
            raise SemanticOutputError(
                "unadjudicated semantic candidates: "
                f"candidates={missing_candidates}, seeds={missing_seeds}"
            )

    @classmethod
    def _candidate_is_covered(
        cls,
        candidate: dict[str, str],
        events: list[SemanticEvent],
    ) -> bool:
        return any(
            event.event_type == candidate["event_type"]
            and (
                not candidate.get("funding_round")
                or cls._normalize_round(event.funding_round)
                == candidate["funding_round"]
            )
            and (
                not candidate.get("subject_hint")
                or cls._canonical_company(str(candidate["subject_hint"]))
                in cls._canonical_company(event.canonical_company)
                or cls._canonical_company(event.canonical_company)
                in cls._canonical_company(str(candidate["subject_hint"]))
            )
            and cls._quotes_overlap(
                (candidate["quote"],),
                event.evidence_quotes[:1],
            )
            for event in events
        )

    @classmethod
    def _seed_is_adjudicated(
        cls,
        seed: SemanticEvent,
        events: list[SemanticEvent],
        rule_events: list[SemanticEvent],
    ) -> bool:
        for event in events:
            if cls._matching_seed(
                [seed],
                event.canonical_company,
                event.event_type,
                event.funding_round,
                event.event_status,
            ) is not None:
                return True
            if event.event_type != seed.event_type:
                continue
            same_company = (
                cls._canonical_company(seed.canonical_company)
                == cls._canonical_company(event.canonical_company)
            )
            same_round = (
                cls._normalize_round(seed.funding_round)
                == cls._normalize_round(event.funding_round)
            )
            if (
                same_company
                and same_round
                and seed.event_status == event.event_status
                and cls._quotes_overlap(
                    seed.evidence_quotes,
                    event.evidence_quotes,
                )
            ):
                return True
            correction_marker = (
                f"minimax_corrected_rule_company:{seed.canonical_company}"
            )
            if (
                correction_marker in event.ambiguities
                and event.event_type == seed.event_type
                and (
                    not seed.funding_round
                    or not event.funding_round
                    or cls._normalize_round(seed.funding_round)
                    == cls._normalize_round(event.funding_round)
                )
                and cls._quotes_overlap(
                    seed.evidence_quotes,
                    event.evidence_quotes,
                )
            ):
                return True
            exact_other_seed = any(
                other != seed
                and cls._matching_seed(
                    [other],
                    event.canonical_company,
                    event.event_type,
                    event.funding_round,
                    event.event_status,
                )
                is not None
                for other in rule_events
            )
            if (
                exact_other_seed
                and event.event_type == seed.event_type
                and cls._quotes_overlap(
                    seed.evidence_quotes,
                    event.evidence_quotes,
                )
                and not cls._company_event_subject_grounded(
                    seed.canonical_company,
                    event.evidence_quotes[0],
                    seed.event_type,
                )
            ):
                return True
        overlap_pairs = [
            (rule, event)
            for rule in rule_events
            for event in events
            if rule.event_type == event.event_type == seed.event_type
            and rule.event_status == event.event_status == seed.event_status
            and cls._normalize_round(rule.funding_round)
            == cls._normalize_round(event.funding_round)
            and cls._quotes_overlap(rule.evidence_quotes, event.evidence_quotes)
        ]
        if len(overlap_pairs) == 1 and overlap_pairs[0][0] == seed:
            return True
        seed_amount = cls._comparable_amount(seed.funding_amount)
        if not seed_amount:
            return False
        same_rule_amount = [
            item
            for item in rule_events
            if item.event_type == seed.event_type
            and cls._comparable_amount(item.funding_amount) == seed_amount
        ]
        same_model_amount = [
            item
            for item in events
            if item.event_type == seed.event_type
            and cls._comparable_amount(item.funding_amount) == seed_amount
        ]
        if len(same_rule_amount) == len(same_model_amount) == 1:
            return True
        return False

    @staticmethod
    def _comparable_amount(value: str) -> str:
        return re.sub(r"^(?:\u7ea6|\u8d85|\u8d85\u8fc7|\u8fd1)", "", value.strip())

    @staticmethod
    def _quotes_overlap(
        left_quotes: tuple[str, ...],
        right_quotes: tuple[str, ...],
    ) -> bool:
        return any(
            left in right or right in left
            for left in left_quotes
            for right in right_quotes
            if left and right
        )

    @staticmethod
    def _rule_seed_id(event: SemanticEvent) -> str:
        material = "\0".join(
            (
                event.canonical_company,
                event.event_type,
                event.funding_round,
                event.event_status,
                event.evidence_quotes[0] if event.evidence_quotes else "",
            )
        )
        return f"rs_{sha1(material.encode('utf-8')).hexdigest()[:10]}"

    def prefiltered_audit(
        self,
        article: CleanArticle,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Build a cache-valid audit record for a deterministic noise reject."""

        audit: dict[str, Any] = {
            "source_id": article.index.source_id,
            "source_article_id": article.index.source_article_id,
            "prompt_version": PROMPT_VERSION,
            "model_identity": self.model_identity,
            "cache_key": self.cache_key,
            "index_content_hash": article.index.content_hash,
            "article_content_hash": article.content_hash,
            "status": "prefiltered",
            "first_response": "",
            "repair_response": "",
            "error": "",
            "rule_seed_count": 0,
            "final_event_count": 0,
            "rules_preserved_count": 0,
            "omissions_detected": 0,
            "prefilter_reason": reason,
        }
        self.last_audit = audit
        return audit

    @classmethod
    def _salvage_grounded_investors(
        cls,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
        responses: tuple[str, ...],
        marker: str,
    ) -> list[SemanticEvent]:
        text = (
            f"{article.index.title}\n{article.index.summary}\n"
            f"{clean_semantic_body_scope(article.clean_body)}"
        )
        investor_updates: dict[
            tuple[str, str, str, str],
            dict[str, str],
        ] = {}
        for response in responses:
            if not response:
                continue
            try:
                payload = cls._parse_json(response)
            except SemanticOutputError:
                continue
            raw_events = payload.get("events")
            if not isinstance(raw_events, list):
                continue
            for raw in raw_events:
                if not isinstance(raw, dict):
                    continue
                company = str(raw.get("company") or "").strip()
                event_type = str(raw.get("event_type") or "").strip()
                event_status = str(raw.get("event_status") or "completed").strip()
                round_name = cls._normalize_round(
                    str(raw.get("funding_round") or "").strip()
                )
                seed = cls._matching_seed(
                    rule_events,
                    company,
                    event_type,
                    round_name,
                    event_status,
                )
                investors = raw.get("investors")
                if seed is None or not isinstance(investors, list):
                    continue
                grounded: dict[str, str] = {}
                for value in (str(item).strip() for item in investors):
                    if (
                        len(value) < 2
                        or value not in text
                        or _UNNAMED_INVESTOR.fullmatch(value)
                    ):
                        continue
                    quote = cls._current_investor_quote(
                        text,
                        value,
                        seed,
                    )
                    if quote:
                        grounded[cls._normalize_investor_name(value, quote)] = quote
                if grounded:
                    investor_updates.setdefault(
                        cls._event_key(seed),
                        {},
                    ).update(grounded)
        output: list[SemanticEvent] = []
        for event in rule_events:
            additions = investor_updates.get(cls._event_key(event), {})
            missing_additions = {
                investor: quote
                for investor, quote in additions.items()
                if investor not in event.investors
            }
            ambiguities = [*event.ambiguities]
            if marker:
                ambiguities.append(marker)
            if missing_additions:
                ambiguities.append("minimax_grounded_investors_salvaged")
            output.append(
                replace(
                    event,
                    investors=tuple(
                        dict.fromkeys((*event.investors, *missing_additions))
                    ),
                    evidence_quotes=tuple(
                        dict.fromkeys(
                            (
                                *event.evidence_quotes,
                                *missing_additions.values(),
                            )
                        )
                    ),
                    ambiguities=tuple(ambiguities),
                )
            )
        return output

    @classmethod
    def _normalize_investor_name(
        cls,
        value: str,
        quote_text: str = "",
    ) -> str:
        phrase = value.strip()
        explicit = re.fullmatch(
            r"(?P<parent>.{2,60}?)\u65d7\u4e0b(?P<child>.+)",
            phrase,
        )
        if explicit:
            child = explicit.group("child").strip()
            if cls._is_named_investor_entity(child):
                return child
            return explicit.group("parent").strip()
        if quote_text:
            owned = re.search(
                re.escape(phrase)
                + r"\u65d7\u4e0b"
                + r"(?P<child>[A-Za-z0-9\u4e00-\u9fff\uff08\uff09()]{2,60}?"
                + r"(?:\u96c6\u56e2|\u57fa\u91d1|\u8d44\u672c|\u521b\u6295|"
                + r"\u6295\u8d44|\u516c\u53f8|\u5408\u4f19\u4f01\u4e1a))"
                + r"(?=[\uff0c\u3002\uff1b\u3001,\s]|$)",
                quote_text,
            )
            if owned and cls._is_named_investor_entity(owned.group("child")):
                return owned.group("child").strip()
        return phrase

    @staticmethod
    def _is_named_investor_entity(value: str) -> bool:
        return bool(
            value
            and not re.fullmatch(
                r"(?:(?:\u67d0|\u4e00\u53ea|\u672a\u5177\u540d|"
                r"\u591a\u5bb6|\u6570\u5bb6)?"
                r"(?:\u57fa\u91d1|\u5b50\u57fa\u91d1|\u673a\u6784))",
                value,
            )
            and re.search(
                r"(?:\u96c6\u56e2|\u57fa\u91d1|\u8d44\u672c|\u521b\u6295|"
                r"\u6295\u8d44|\u516c\u53f8|\u5408\u4f19\u4f01\u4e1a)$",
                value,
            )
        )

    @classmethod
    def _current_investor_quote(
        cls,
        text: str,
        investor: str,
        seed: SemanticEvent,
    ) -> str:
        candidates: list[tuple[int, int, str]] = []
        sentences = [
            value.strip()
            for value in re.split(r"(?<=[\u3002\uff01\uff1f\uff1b])", text)
            if value.strip()
        ]
        for position, quote in enumerate(sentences):
            if investor not in quote or cls._is_historical_background(quote):
                continue
            if not re.search(
                r"\u672c\u8f6e|\u672c\u6b21|\u6295\u8d44\u65b9|"
                r"\u9886\u6295|\u8ddf\u6295|\u53c2\u4e0e.{0,8}"
                r"\u878d\u8d44|\u7531.{0,80}\u6295\u8d44",
                quote,
            ):
                continue
            identity_quote = quote
            if not cls._company_event_subject_grounded(
                seed.canonical_company,
                identity_quote,
                seed.event_type,
            ):
                if not (
                    position > 0
                    and re.match(r"^(?:\u672c\u8f6e|\u672c\u6b21)", quote)
                    and cls._company_event_subject_grounded(
                        seed.canonical_company,
                        sentences[position - 1],
                        seed.event_type,
                    )
                ):
                    continue
                identity_quote = f"{sentences[position - 1]}{quote}"
            round_pattern = (
                r"(?:Pre-)?[A-Z](?:\+{1,2})?\u8f6e|"
                r"\u5929\u4f7f(?:\+{1,2})?\u8f6e|"
                r"\u79cd\u5b50(?:\+{1,2})?\u8f6e|\u6218\u7565\u878d\u8d44"
            )
            current_rounds = {
                cls._normalize_round(value)
                for value in re.findall(round_pattern, quote)
            }
            normalized_seed_round = cls._normalize_round(seed.funding_round)
            if (
                normalized_seed_round
                and current_rounds
                and normalized_seed_round not in current_rounds
            ):
                continue
            quote_rounds = {
                cls._normalize_round(value)
                for value in re.findall(round_pattern, identity_quote)
            }
            if (
                seed.funding_round
                and quote_rounds
                and normalized_seed_round not in quote_rounds
            ):
                continue
            score = 3
            if seed.funding_round and seed.funding_round in quote:
                score += 2
            if seed.funding_amount and seed.funding_amount in quote:
                score += 2
            if "\u672c\u8f6e" in quote or "\u672c\u6b21" in quote:
                score += 2
            candidates.append(
                (score, -len(identity_quote), identity_quote[:500])
            )
        if not candidates:
            return ""
        return max(candidates)[2]

    @staticmethod
    def _complete_audit(
        audit: dict[str, Any],
        events: list[SemanticEvent],
        rule_events: list[SemanticEvent],
        *,
        candidates: list[dict[str, str]] | None = None,
        model_ambiguities: list[str] | None = None,
    ) -> None:
        audit["final_event_count"] = len(events)
        audit["rules_preserved_count"] = sum(
            event.processor.startswith("rules") for event in events
        )
        candidates = candidates or []
        model_ambiguities = model_ambiguities or []
        unmapped: list[str] = []
        for candidate in candidates:
            candidate_id = candidate["id"]
            candidate_quote = candidate["quote"]
            candidate_type = candidate["event_type"]
            covered = MiniMaxSemanticProcessor._candidate_is_covered(
                {
                    "id": candidate_id,
                    "event_type": candidate_type,
                    "quote": candidate_quote,
                },
                events,
            )
            if not covered:
                unmapped.append(candidate_id)
        audit["unmapped_candidate_count"] = len(unmapped)
        audit["unmapped_candidate_ids"] = unmapped
        audit["omissions_detected"] = len(unmapped)
        exact_seed_count = sum(
            any(
                MiniMaxSemanticProcessor._matching_seed(
                    [seed],
                    event.canonical_company,
                    event.event_type,
                    event.funding_round,
                    event.event_status,
                )
                is not None
                for event in events
            )
            for seed in rule_events
        )
        adjudicated_seed_count = sum(
            MiniMaxSemanticProcessor._seed_is_adjudicated(
                seed,
                events,
                rule_events,
            )
            for seed in rule_events
        )
        corrected_seed_count = max(0, adjudicated_seed_count - exact_seed_count)
        audit["model_only_count"] = max(
            0, len(events) - exact_seed_count - corrected_seed_count
        )
        audit["rejected_seed_count"] = max(
            0, len(rule_events) - adjudicated_seed_count
        )
        audit["corrected_seed_count"] = corrected_seed_count

    @staticmethod
    def _prompt(
        channel: SourceChannel,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
    ) -> str:
        seed = [
            {
                "id": MiniMaxSemanticProcessor._rule_seed_id(item),
                "company": item.canonical_company,
                "event_type": item.event_type,
                "funding_round": item.funding_round,
                "funding_amount": item.funding_amount,
                "cumulative_funding_amount": item.cumulative_funding_amount,
                "event_status": item.event_status,
                "evidence_quotes": list(item.evidence_quotes),
            }
            for item in rule_events
        ]
        example = {
            "events": [
                {
                    "company": "\u661f\u6cb3\u82af\u7247",
                    "event_type": "funding",
                    "industry_tags": ["semiconductor"],
                    "funding_round": "A\u8f6e",
                    "funding_amount": "1\u4ebf\u5143",
                    "cumulative_funding_amount": "",
                    "investors": ["\u8fdc\u5c71\u8d44\u672c"],
                    "event_status": "completed",
                    "event_summary": (
                        "\u661f\u6cb3\u82af\u7247\u5b8c\u62101"
                        "\u4ebf\u5143A\u8f6e\u878d\u8d44"
                    ),
                    "evidence_quotes": [
                        "\u661f\u6cb3\u82af\u7247\u5b8c\u62101"
                        "\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
                        "\u8fdc\u5c71\u8d44\u672c\u9886\u6295\u3002"
                    ],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        }
        payload = {
            "source_prior": list(channel.event_prior),
            "title": article.index.title,
            "published_at": article.index.published_at,
            "structured": article.structured_data,
            "rule_seed": seed,
            "candidate_ledger": MiniMaxSemanticProcessor._event_candidates(
                article.clean_body
            ),
            "article": clean_semantic_body_scope(article.clean_body)[:24000],
            "historical_counterexample": {
                "input": (
                    "\u672c\u65e5\u8bbf\u8c08\u56de\u987e\u4e0a\u6708"
                    "\u5df2\u5b8c\u6210\u7684\u878d\u8d44"
                ),
                "expected_output": {
                    "events": [],
                    "ambiguities": ["historical_background_only"],
                },
            },
        }
        return (
            f"\u793a\u4f8b\uff1a"
            f"{json.dumps(example, ensure_ascii=False)}\n"
            f"\u8f93\u5165\uff1a"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    @staticmethod
    def _parse_json(
        value: str,
        *,
        allow_syntax_repair: bool = False,
    ) -> dict[str, Any]:
        candidate = value.strip()
        fenced = re.search(
            r"```(?:json)?\s*(\{.*\})\s*```",
            candidate,
            re.S,
        )
        if fenced:
            candidate = fenced.group(1)
        else:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start >= 0 and end > start:
                candidate = candidate[start : end + 1]
        if not candidate.startswith("{") or not candidate.endswith("}"):
            try:
                non_object = json.loads(candidate)
            except json.JSONDecodeError as error:
                raise SemanticOutputError(
                    "semantic output has no complete JSON object"
                ) from error
            if not isinstance(non_object, dict):
                raise SemanticOutputError("semantic output must be an object")
            raise SemanticOutputError("semantic output has no complete JSON object")
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as error:
            if not allow_syntax_repair:
                raise SemanticOutputError(f"invalid JSON: {error}") from error
            try:
                payload = json.loads(repair_json(candidate))
            except (TypeError, ValueError, json.JSONDecodeError) as repair_error:
                raise SemanticOutputError(
                    f"invalid JSON after syntax repair: {repair_error}"
                ) from repair_error
        if not isinstance(payload, dict):
            raise SemanticOutputError("semantic output must be an object")
        return payload

    @classmethod
    def _validate_payload(
        cls,
        article: CleanArticle,
        payload: dict[str, Any],
        rule_events: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        raw_events = payload.get("events")
        ambiguities = payload.get("ambiguities", [])
        if (
            not isinstance(raw_events, list)
            or not isinstance(ambiguities, list)
            or any(not isinstance(item, str) for item in ambiguities)
        ):
            raise SemanticOutputError(
                "events must be a list and ambiguities must be strings"
            )
        text = (
            f"{article.index.title}\n{article.index.summary}\n"
            f"{clean_semantic_body_scope(article.clean_body)}"
        )

        model_events: list[SemanticEvent] = []
        subject_mismatch_count = 0
        for raw in raw_events:
            if not isinstance(raw, dict):
                raise SemanticOutputError("event must be an object")
            company = str(raw.get("company") or "").strip()
            event_type = str(raw.get("event_type") or "").strip()
            if event_type not in ALLOWED_EVENT_TYPES:
                raise SemanticOutputError("company/event_type failed validation")
            if event_type == "other":
                continue
            if not company or company not in text or not is_company_like(company):
                raise SemanticOutputError("company/event_type failed validation")
            round_name = cls._normalize_round(
                str(raw.get("funding_round") or "").strip()
            )
            event_status = str(raw.get("event_status") or "completed").strip()
            if event_status not in ALLOWED_EVENT_STATUS:
                raise SemanticOutputError("invalid event_status")
            preliminary_seed = cls._matching_seed(
                rule_events,
                company,
                event_type,
                round_name,
                event_status,
            )
            raw_quotes = raw.get("evidence_quotes")
            if (
                not isinstance(raw_quotes, list)
                or not raw_quotes
                or any(
                    not isinstance(quote, str) or not quote.strip()
                    for quote in raw_quotes
                )
            ):
                raise SemanticOutputError("evidence quote is not a string")
            quotes: list[str] = []
            used_seed_quote = False
            for position, raw_quote in enumerate(raw_quotes):
                quote = cls._ground_quote(text, str(raw_quote).strip())
                if quote:
                    quotes.append(quote)
                    continue
                if (
                    position == 0
                    and preliminary_seed is not None
                    and preliminary_seed.evidence_quotes
                ):
                    quotes.extend(preliminary_seed.evidence_quotes)
                    used_seed_quote = True
            if not quotes:
                # Model-only events are never allowed to poison grounded rule
                # seeds.  The final merge already suppresses unmatched events.
                continue
            if preliminary_seed is None:
                preliminary_seed = cls._correction_seed(
                    rule_events,
                    event_type,
                    round_name,
                    event_status,
                    quotes,
                )
            if not cls._company_subject_grounded(company, quotes[0]):
                expanded_primary = cls._expand_pronominal_subject_context(
                    text,
                    quotes[0],
                    company,
                )
                if expanded_primary:
                    quotes[0] = expanded_primary
            quote_text = "\n".join(quotes)
            if not cls._company_subject_grounded(company, quotes[0]):
                subject_mismatch_count += 1
                continue
            if cls._funding_use_only_nonfunding(event_type, quote_text):
                continue
            if cls._event_evidence_is_historical(
                text,
                [str(quote).strip() for quote in quotes],
                article.index.published_at,
            ):
                continue
            raw_summary = str(raw.get("event_summary") or "").strip()
            if raw_summary and (
                cls._is_historical_event_quote(
                    raw_summary,
                    article.index.published_at,
                )
                or cls._summary_names_prior_year(
                    raw_summary,
                    article.index.published_at,
                )
            ):
                continue
            amount = str(raw.get("funding_amount") or "").strip()
            cumulative_amount = str(raw.get("cumulative_funding_amount") or "").strip()
            investors = raw.get("investors") or []
            if not isinstance(investors, list):
                raise SemanticOutputError("investors must be a list")
            removed_fields: list[str] = []
            funding_fields = {
                "funding_round": round_name,
                "funding_amount": amount,
                "cumulative_funding_amount": cumulative_amount,
            }
            for field, value in funding_fields.items():
                if not value or value in quote_text:
                    continue
                if preliminary_seed is None:
                    raise SemanticOutputError(
                        "funding fact is absent from evidence quote"
                    )
                seed_value = str(getattr(preliminary_seed, field))
                funding_fields[field] = seed_value if seed_value in quote_text else ""
                removed_fields.append(field)
            round_name = funding_fields["funding_round"]
            amount = funding_fields["funding_amount"]
            cumulative_amount = funding_fields["cumulative_funding_amount"]
            event_status = cls._normalize_event_status(
                event_type,
                event_status,
                quotes[0],
            )
            if (
                event_status == "cumulative"
                and event_type != "funding"
                and not re.search(
                    r"\u7d2f\u8ba1|\u603b\u8ba1|\u5408\u8ba1|"
                    r"\u8fc4\u4eca|\u622a\u81f3",
                    quotes[0],
                )
            ):
                event_status = "completed"
            if amount and cls._is_cumulative_context(quote_text, amount):
                cumulative_amount = cumulative_amount or amount
                amount = ""
            if amount and cls._is_valuation_context(quote_text, amount):
                amount = ""
                removed_fields.append("funding_amount_is_valuation")
            named_investors: list[str] = []
            unnamed_investors: list[str] = []
            ungrounded_investors: list[str] = []
            for investor in map(str, investors):
                value = investor.strip()
                if not value:
                    continue
                if _UNNAMED_INVESTOR.fullmatch(value):
                    unnamed_investors.append(value)
                    continue
                if value not in quote_text:
                    ungrounded_investors.append(value)
                    continue
                named_investors.append(cls._normalize_investor_name(value, quote_text))
            tags = raw.get("industry_tags") or []
            if not isinstance(tags, list) or any(
                not isinstance(tag, str) or not tag.strip() for tag in tags
            ):
                raise SemanticOutputError("industry_tags must be strings")
            confidence = str(raw.get("confidence") or "unknown").strip()
            if confidence not in ALLOWED_CONFIDENCE:
                raise SemanticOutputError("invalid confidence")
            raw_ambiguities = list(ambiguities)
            if used_seed_quote:
                raw_ambiguities.append("minimax_seed_quote_substituted")
            raw_ambiguities.extend(
                f"minimax_ungrounded_field_removed:{field}" for field in removed_fields
            )
            raw_ambiguities.extend(
                f"unnamed_investor_omitted:{value}" for value in unnamed_investors
            )
            raw_ambiguities.extend(
                f"minimax_ungrounded_investor_removed:{value}"
                for value in ungrounded_investors
            )
            seed = cls._matching_seed(
                rule_events,
                company,
                event_type,
                round_name,
                event_status,
            ) or cls._correction_seed(
                rule_events,
                event_type,
                round_name,
                event_status,
                quotes,
            )
            canonical_company = company
            is_company_correction = (
                seed is not None
                and cls._canonical_company(seed.canonical_company)
                != cls._canonical_company(company)
            )
            if seed is None and not cls._company_event_subject_grounded(
                company,
                quotes[0],
                event_type,
            ):
                subject_mismatch_count += 1
                continue
            if is_company_correction and not cls._company_event_subject_grounded(
                company,
                quotes[0],
                event_type,
            ):
                subject_mismatch_count += 1
                continue
            if is_company_correction:
                raw_ambiguities.append(
                    f"minimax_corrected_rule_company:{seed.canonical_company}"
                )
            effective_round = round_name or (seed.funding_round if seed else "")
            effective_quotes = list(quotes)
            if (
                effective_round
                and not any(effective_round in value for value in effective_quotes)
                and seed is not None
            ):
                effective_quotes.extend(
                    value for value in seed.evidence_quotes if effective_round in value
                )
            model_events.append(
                SemanticEvent(
                    source_id=article.index.source_id,
                    source_article_id=article.index.source_article_id,
                    canonical_url=article.index.canonical_url,
                    company_mentions=tuple(dict.fromkeys((canonical_company, company))),
                    canonical_company=canonical_company,
                    event_type=event_type,
                    event_date=article.index.published_at[:10],
                    industry_tags=tuple(dict.fromkeys(map(str, tags))),
                    funding_round=effective_round,
                    funding_amount=amount,
                    cumulative_funding_amount=cumulative_amount,
                    investors=tuple(dict.fromkeys(named_investors)),
                    event_summary=quotes[0].strip()[:500],
                    evidence_quotes=tuple(quote.strip() for quote in effective_quotes),
                    ambiguities=tuple(raw_ambiguities),
                    confidence=confidence,
                    processor="minimax",
                    prompt_version=PROMPT_VERSION,
                    content_hash=article.content_hash,
                    phase=(
                        seed.phase
                        if seed
                        else (
                            "strategy_capital"
                            if event_status == "started"
                            else "build_organize"
                        )
                    ),
                    event_status=event_status,
                )
            )
        if subject_mismatch_count and not model_events:
            raise SemanticOutputError(
                "all semantic events failed primary-subject grounding"
            )
        if rule_events and not model_events and not raw_events:
            raise SemanticOutputError(
                "empty model output did not adjudicate available rule seeds"
            )
        return cls._merge_with_rule_seeds(rule_events, model_events)

    @staticmethod
    def _ground_quote(article_text: str, quote: str) -> str:
        """Return a unique original span for a layout-only quote variant.

        Aggregate pages often insert whitespace around highlighted spans, while
        models reproduce the same characters without those layout gaps or use
        a different straight/curly quote glyph.  Reconciliation removes only
        whitespace and quote glyphs, requires one unique match, and always
        returns the untouched source substring.  Every other character,
        including digits and punctuation, must still match.
        """

        candidate = quote.strip()
        if candidate in article_text:
            return candidate
        ignored_quotes = frozenset(
            {
                '"',
                "'",
                "\u201c",
                "\u201d",
                "\u2018",
                "\u2019",
            }
        )

        def comparable(value: str) -> tuple[str, list[int]]:
            characters: list[str] = []
            positions: list[int] = []
            for position, character in enumerate(value):
                if character.isspace() or character in ignored_quotes:
                    continue
                characters.append(character)
                positions.append(position)
            return "".join(characters), positions

        normalized_quote, _ = comparable(candidate)
        normalized_text, positions = comparable(article_text)
        if not normalized_quote:
            return ""
        start = normalized_text.find(normalized_quote)
        if start < 0 or normalized_text.find(normalized_quote, start + 1) >= 0:
            return ""
        end = start + len(normalized_quote) - 1
        return article_text[positions[start] : positions[end] + 1].strip()

    @staticmethod
    def _funding_use_only_nonfunding(
        event_type: str,
        evidence_text: str,
    ) -> bool:
        if event_type == "funding":
            return False
        if not re.search(
            r"\u672c(?:\u6b21|\u8f6e).{0,20}\u8d44\u91d1"
            r".{0,24}(?:\u7528\u4e8e|\u6295\u5165)",
            evidence_text,
        ):
            return False
        return not re.search(
            r"\u65b0\u5efa|\u6269\u5efa|\u5efa\u8bbe\u4e2d|"
            r"\u5df2.{0,8}(?:\u5f00\u5de5|\u6295\u4ea7|\u5efa\u6210)|"
            r"\u6b63\u5f0f.{0,8}(?:\u542f\u52a8|\u5f00\u5de5|\u6295\u4ea7)|"
            r"\u5df2\u843d\u5730|\u5df2\u7b7e\u7ea6|\u5df2\u83b7\u6279|\u5df2\u4e2d\u6807",
            evidence_text,
        )

    @staticmethod
    def _is_valuation_context(text: str, value: str) -> bool:
        escaped = re.escape(value)
        marker = r"(?:\u6295\u524d|\u6295\u540e|\u76ee\u6807|\u516c\u53f8)?(?:\u4f30\u503c|\u5e02\u503c|\u4f5c\u4ef7|\u80a1\u6743\u4ef7\u503c)"
        valuation_bound = bool(
            re.search(
                rf"(?:{marker}.{{0,16}}{escaped}|"
                rf"{escaped}(?:\u7684)?{marker})",
                text,
            )
        )
        if not valuation_bound:
            return False
        funding_bound = bool(
            re.search(
                rf"(?:\u878d\u8d44(?:\u91d1\u989d|\u989d|\u89c4\u6a21)?|"
                rf"\u52df\u8d44(?:\u91d1\u989d|\u989d|\u89c4\u6a21)?|"
                rf"\u7b79\u96c6(?:\u8d44\u91d1)?)"
                rf"[\uff0c,:\uff1a ]{{0,3}}(?:\u4e3a|\u8fbe)?{escaped}",
                text,
            )
            or re.search(
                rf"(?:\u5b8c\u6210|\u83b7\u5f97|\u83b7|\u52df\u96c6|\u52df\u5f97|\u7b79\u96c6)"
                rf"[^\u3002\uff1b]{{0,12}}{escaped}"
                rf"[^\u3002\uff1b]{{0,8}}\u878d\u8d44",
                text,
            )
        )
        return not funding_bound

    @classmethod
    def _normalize_rule_events(
        cls,
        article: CleanArticle,
        events: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        article_text = (
            f"{article.index.title}\n{article.index.summary}\n"
            f"{clean_semantic_body_scope(article.clean_body)}"
        )
        output: list[SemanticEvent] = []
        for event in cls._normalize_final_events(events):
            quotes = [quote for quote in event.evidence_quotes if quote]
            if not quotes:
                continue
            primary = quotes[0]
            mentions = tuple(
                dict.fromkeys(
                    (
                        event.canonical_company,
                        *event.company_mentions,
                    )
                )
            )
            if not is_company_like(event.canonical_company):
                continue
            if not any(mention and mention in primary for mention in mentions):
                continue
            if cls._summary_names_prior_year(
                event.event_summary,
                article.index.published_at,
            ):
                continue
            if cls._event_evidence_is_historical(
                article_text,
                quotes,
                article.index.published_at,
            ):
                continue
            output.append(event)
        return output

    @classmethod
    def _normalize_final_events(
        cls,
        events: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        normalized: dict[tuple[str, str, str, str], SemanticEvent] = {}
        for event in events:
            if event.event_type == "other":
                continue
            canonical = cls._canonical_company(event.canonical_company)
            if not canonical:
                continue
            updated = replace(
                event,
                canonical_company=canonical,
                company_mentions=tuple(
                    dict.fromkeys(
                        (
                            canonical,
                            event.canonical_company,
                            *event.company_mentions,
                        )
                    )
                ),
            )
            key = cls._event_key(updated)
            previous = normalized.get(key)
            if (
                previous is None
                or previous.processor.startswith("rules")
                and not updated.processor.startswith("rules")
            ):
                normalized[key] = updated
        return list(normalized.values())

    @staticmethod
    def _canonical_company(value: str) -> str:
        return canonical_company_name(value)

    @classmethod
    def _matching_seed(
        cls,
        rule_events: list[SemanticEvent],
        company: str,
        event_type: str,
        round_name: str,
        event_status: str,
    ) -> SemanticEvent | None:
        canonical_company = cls._canonical_company(company)
        exact = [
            event
            for event in rule_events
            if (
                cls._canonical_company(event.canonical_company),
                event.event_type,
                event.funding_round,
                event.event_status,
            )
            == (canonical_company, event_type, round_name, event_status)
        ]
        if exact:
            return exact[0]
        candidates = [
            event
            for event in rule_events
            if event.event_type == event_type
            and event.event_status == event_status
            and (
                cls._canonical_company(event.canonical_company) == canonical_company
                or event.canonical_company in company
                or company in event.canonical_company
                or company in event.company_mentions
            )
            and (
                not event.funding_round
                or not round_name
                or cls._normalize_round(event.funding_round) == round_name
            )
        ]
        return candidates[0] if len(candidates) == 1 else None

    @classmethod
    def _correction_seed(
        cls,
        rule_events: list[SemanticEvent],
        event_type: str,
        round_name: str,
        event_status: str,
        quotes: list[str] | tuple[str, ...],
    ) -> SemanticEvent | None:
        """Match one grounded rule event while allowing subject correction."""

        del event_status
        candidates: list[SemanticEvent] = []
        for event in rule_events:
            if event.event_type != event_type:
                continue
            if (
                event.funding_round
                and round_name
                and cls._normalize_round(event.funding_round) != round_name
            ):
                continue
            if not any(
                model_quote in seed_quote or seed_quote in model_quote
                for model_quote in quotes
                for seed_quote in event.evidence_quotes
                if model_quote and seed_quote
            ):
                continue
            candidates.append(event)
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _normalize_event_status(
        event_type: str,
        event_status: str,
        primary_quote: str,
    ) -> str:
        if event_status != "started":
            return event_status
        completed = re.search(
            r"\u53d1\u5e03|\u63a8\u51fa|\u5b8c\u6210|"
            r"\u83b7\u6279|\u6279\u51c6\u4e0a\u5e02|"
            r"\u7b7e\u7f72|\u7b7e\u8ba2|\u4efb\u547d|"
            r"\u4e2d\u6807|\u4ea4\u4ed8",
            primary_quote,
        )
        future = re.search(
            r"\u5c06\u4e8e|\u5373\u5c06|\u62df|\u8ba1\u5212|"
            r"\u9884\u8ba1|\u542f\u52a8|\u5f00\u542f|\u5f00\u59cb",
            primary_quote,
        )
        if completed and not future:
            return "completed"
        return event_status

    @staticmethod
    def _normalize_round(value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(
            r"Pre[\s-]*IPO(?:轮)?(?:融资)?",
            normalized,
            re.I,
        ):
            return "Pre-IPO"
        if normalized in {
            "\u65b0\u4e00\u8f6e",
            "\u65b0\u4e00\u8f6e\u878d\u8d44",
        }:
            return ""
        if normalized in {
            "\u6218\u7565\u8f6e",
            "\u6218\u7565\u878d\u8d44",
        }:
            return "\u6218\u7565\u878d\u8d44"
        letter = re.match(
            r"^(Pre-)?([A-Z])(\+{0,2})(?:\u8f6e)?"
            r"(?:\uff08[^\uff09]{1,30}\uff09)?$",
            normalized,
        )
        if letter:
            return "".join(
                (
                    letter.group(1) or "",
                    letter.group(2),
                    letter.group(3),
                    "" if letter.group(1) else "\u8f6e",
                )
            )
        return normalized

    @staticmethod
    def _company_subject_grounded(
        company: str,
        primary_quote: str,
    ) -> bool:
        normalized_quote = re.sub(r"\s+", "", primary_quote)
        return any(
            len(candidate) >= 2 and re.sub(r"\s+", "", candidate) in normalized_quote
            for candidate in company_alias_candidates(company)
        )

    @classmethod
    def _company_event_subject_grounded(
        cls,
        company: str,
        primary_quote: str,
        event_type: str,
    ) -> bool:
        """Require the candidate company to precede an event-defining predicate."""

        predicate_patterns = {
            "funding": r"(?:\u5b8c\u6210|\u83b7\u5f97|\u83b7(?!\u6089)|\u65a9\u83b7|\u5ba3\u5e03\u5b8c\u6210|\u5b98\u5ba3\u5b8c\u6210|\u542f\u52a8|\u5f00\u542f|\u5f00\u59cb|\u7b79\u96c6|\u52df\u96c6|\u52df\u8d44|\u6d3d\u8c08|\u8c08\u5224|\u7b79\u5212|\u62df).{0,100}(?:\u878d\u8d44|\u8d44\u91d1|[A-Z](?:\+{1,2})?\u8f6e)",
            "executive_change": r"(?:\u4efb\u547d|\u8058\u4efb|\u51fa\u4efb|\u62c5\u4efb|\u52a0\u5165|\u79bb\u4efb|\u8f9e\u4efb|\u5347\u4efb|\u63a5\u4efb)",
            "partnership": r"(?:\u7b7e\u7f72|\u7b7e\u8ba2|\u8fbe\u6210|\u5efa\u7acb|\u6df1\u5316|\u643a\u624b|\u5408\u4f5c)",
            "major_order": r"(?:\u4e2d\u6807|\u83b7\u5f97|\u65a9\u83b7|\u7b7e\u7f72|\u7b7e\u8ba2|\u62ff\u4e0b).{0,50}(?:\u8ba2\u5355|\u5408\u540c|\u9879\u76ee|\u91c7\u8d2d)",
            "factory_or_capacity": r"(?:\u6269\u4ea7|\u6295\u4ea7|\u5f00\u5de5|\u843d\u5730|\u5efa\u8bbe|\u542f\u7528|\u91cf\u4ea7|\u4e0b\u7ebf)",
            "technical_milestone": r"(?:\u53d1\u5e03|\u63a8\u51fa|\u5b8c\u6210|\u5b9e\u73b0|\u7a81\u7834|\u83b7\u6279|\u4ea4\u4ed8|\u91cf\u4ea7|\u4e0b\u7ebf)",
            "new_site_or_entity": r"(?:\u6210\u7acb|\u7ec4\u5efa|\u8bbe\u7acb|\u6ce8\u518c|\u843d\u5730|\u542f\u7528)",
            "regulatory_or_clinical": r"(?:\u83b7\u6279|\u6279\u51c6|\u901a\u8fc7|\u53d6\u5f97|\u83b7\u5f97|\u53d7\u7406|\u8ba4\u8bc1|\u8bb8\u53ef|\u4e34\u5e8a)",
            "policy_or_standard": r"(?:\u53d1\u5e03|\u5370\u53d1|\u901a\u8fc7|\u51fa\u53f0|\u5b9e\u65bd|\u5f81\u6c42\u610f\u89c1)",
            "procurement_tender": r"(?:\u62db\u6807|\u91c7\u8d2d|\u4e2d\u6807|\u5165\u56f4)",
            "customer_validation": r"(?:\u9a8c\u8bc1|\u91c7\u7528|\u5bfc\u5165|\u5b9a\u70b9|\u8ba4\u8bc1|\u590d\u8d2d)",
            "merger_acquisition": r"(?:\u6536\u8d2d|\u5e76\u8d2d|\u5408\u5e76|\u51fa\u552e|\u53d7\u8ba9)",
            "ipo_or_listing": r"(?:\u4e0a\u5e02|\u6302\u724c|IPO|\u767b\u9646)",
            "enterprise_system": r"(?:\u4e0a\u7ebf|\u90e8\u7f72|\u542f\u7528|\u5efa\u8bbe|\u5347\u7ea7).{0,50}(?:\u7cfb\u7edf|\u5e73\u53f0|ERP|MES|CRM)?",
        }
        predicate = predicate_patterns.get(event_type)
        if not predicate:
            return False
        compact = re.sub(r"\s+", "", primary_quote)
        for alias in company_alias_candidates(company):
            normalized_alias = re.sub(r"\s+", "", alias)
            if len(normalized_alias) < 2:
                continue
            start = 0
            while True:
                position = compact.find(normalized_alias, start)
                if position < 0:
                    break
                left_boundary = max(
                    compact.rfind(mark, 0, position)
                    for mark in "\u3002\uff01\uff1f\uff1b\uff0c,:\uff1a\n"
                )
                clause_start = left_boundary + 1
                alias_end = position + len(normalized_alias)
                tail = compact[alias_end:]
                hard_positions = [
                    tail.find(mark)
                    for mark in "\u3002\uff01\uff1f\uff1b\n"
                ]
                hard_positions = [value for value in hard_positions if value >= 0]
                bounded_tail = tail[: min(hard_positions)] if hard_positions else tail
                soft_positions = [
                    bounded_tail.find(mark)
                    for mark in "\uff0c,:\uff1a"
                ]
                soft_positions = [value for value in soft_positions if value >= 0]
                if soft_positions:
                    first_soft = min(soft_positions)
                    first_segment = bounded_tail[:first_soft]
                    after = first_segment
                    if re.fullmatch(
                        r"(?:\u5ba3\u5e03|\u8868\u793a|\u62ab\u9732|\u5b98\u5ba3|\u79f0|\u6d88\u606f\u79f0)",
                        first_segment,
                    ):
                        remainder = bounded_tail[first_soft + 1 :]
                        next_soft = [
                            remainder.find(mark)
                            for mark in "\uff0c,:\uff1a"
                        ]
                        next_soft = [value for value in next_soft if value >= 0]
                        next_segment = (
                            remainder[: min(next_soft)] if next_soft else remainder
                        )
                        event_match = re.search(predicate, next_segment)
                        if event_match:
                            subject_prefix = next_segment[: event_match.start()]
                            auxiliary = (
                                r"(?:\u5df2|\u516c\u53f8|\u5176|\u8be5\u516c\u53f8|"
                                r"\u672c\u516c\u53f8|\u6b63\u5f0f|\u6210\u529f|"
                                r"\u5c06|\u62df|\u8ba1\u5212|\u6b63|\u6b63\u5728)*"
                            )
                            same_subject = any(
                                subject_prefix.startswith(candidate)
                                for candidate in company_alias_candidates(company)
                                if len(candidate) >= 2
                            )
                            pronominal_subject = subject_prefix.startswith(
                                (
                                    "\u516c\u53f8",
                                    "\u5176",
                                    "\u8be5\u516c\u53f8",
                                    "\u672c\u516c\u53f8",
                                )
                            )
                            if (
                                re.fullmatch(auxiliary, subject_prefix)
                                or same_subject
                                or pronominal_subject
                            ):
                                after += next_segment
                else:
                    after = bounded_tail
                before = compact[max(clause_start, position - 10) : position]
                if not re.search(
                    r"(?:\u6295\u8d44\u65b9|\u6295\u8d44\u4eba|\u9886\u6295\u65b9|\u8ddf\u6295\u65b9|\u5206\u6790\u5e08|\u4e0e|\u548c|\u540c|\u643a\u624b|\u8054\u5408|\u8054\u624b|\u643a|\u534f\u540c|\u4f1a\u540c|\u5055\u540c|\u643a\u540c)$",
                    before,
                ) and re.search(predicate, after[:100]):
                    return True
                start = position + len(normalized_alias)
        return False

    @classmethod
    def _expand_pronominal_subject_context(
        cls,
        article_text: str,
        primary_quote: str,
        company: str,
    ) -> str:
        """Attach a same-sentence antecedent to an otherwise grounded quote.

        Models sometimes quote a clause beginning with a pronoun even though the
        company is named earlier in the same source sentence. Accepting that
        clause by itself would weaken subject grounding. Instead, return the
        untouched source span from the preceding sentence boundary only when
        that bounded span contains an explicit company alias and the quote has
        a unique occurrence.
        """

        if not re.match(
            r"^(?:\u5176|\u8be5\u516c\u53f8|\u516c\u53f8|\u672c\u8f6e|\u8be5\u8f6e|"
            r"\u539f\u5b9a|\u540c\u65f6|\u6b64\u5916|\u53e6\u5916|\u968f\u540e|\u76ee\u524d|"
            r"\u56e0.{0,30}\u672c\u8f6e)",
            primary_quote.lstrip(),
        ):
            return ""
        # Prefer the body copy when listing metadata repeats the same sentence.
        start = article_text.rfind(primary_quote)
        if start < 0:
            return ""
        left = max(0, start - 220)
        prefix = article_text[left:start]
        boundary = max(
            prefix.rfind(marker) for marker in ("\u3002", "\uff01", "\uff1f", "\n")
        )
        context_start = left + boundary + 1 if boundary >= 0 else left
        current_span = article_text[context_start : start + len(primary_quote)]
        if not cls._company_subject_grounded(company, current_span) and boundary >= 0:
            current_prefix = article_text[context_start:start].strip()
            if re.search(
                r"(?:\u516c\u53f8|\u79d1\u6280|\u96c6\u56e2|\u80a1\u4efd|\u7535\u5b50|\u667a\u80fd)"
                r".{0,12}(?:\u8868\u793a|\u5ba3\u5e03|\u79f0)[\uff0c,:\uff1a]?$",
                current_prefix,
            ):
                return ""
            prior_prefix = prefix[:boundary]
            prior_boundary = max(
                prior_prefix.rfind(marker)
                for marker in ("\u3002", "\uff01", "\uff1f", "\n")
            )
            context_start = (
                left + prior_boundary + 1 if prior_boundary >= 0 else left
            )
        expanded = article_text[context_start : start + len(primary_quote)].strip()
        if len(expanded) > 500:
            return ""
        return expanded if cls._company_subject_grounded(company, expanded) else ""

    @classmethod
    def _event_evidence_is_historical(
        cls,
        article_text: str,
        quotes: list[str],
        published_at: str,
    ) -> bool:
        """Use the primary evidence quote to decide event recency.

        The first quote is the event's required primary evidence. Later quotes
        may contain cumulative context without a date and must not make a
        historical event look current.
        """
        if not quotes:
            return False
        quote = quotes[0]
        contexts: list[str] = []
        start = 0
        while True:
            position = article_text.find(quote, start)
            if position < 0:
                break
            left = max(0, position - 80)
            prefix = article_text[left:position]
            boundary = max(
                prefix.rfind(marker) for marker in ("\u3002", "\uff01", "\uff1f", "\n")
            )
            context_start = left + boundary + 1 if boundary >= 0 else left
            current_prefix = prefix[boundary + 1 :] if boundary >= 0 else prefix
            bridges = (
                "\u4e0e\u6b64\u540c\u65f6",
                "\u540c\u65f6",
                "\u6b64\u5916",
                "\u53e6\u5916",
                "\u4f8b\u5982",
                "\u5176\u4e2d",
                "\u5305\u62ec",
            )
            if (
                current_prefix.strip().startswith(bridges)
                or quote.lstrip().startswith(bridges)
                or re.match(
                    r"(?:\u5728|\u4e8e).{0,24}"
                    r"(?:\u878d\u8d44|\u6295\u8d44|\u6536\u8d2d|\u5408\u5e76)"
                    r"(?:\u5b8c\u6210)?\u540e",
                    quote.lstrip(),
                )
            ):
                chain_left = max(0, position - 240)
                paragraph_boundary = article_text.rfind(
                    "\n",
                    chain_left,
                    position,
                )
                context_start = (
                    paragraph_boundary + 1 if paragraph_boundary >= 0 else chain_left
                )
            contexts.append(article_text[context_start : position + len(quote)])
            start = position + max(1, len(quote))
        if not contexts:
            return False
        return all(
            cls._is_historical_event_quote(context, published_at)
            for context in contexts
        )

    @staticmethod
    def _summary_names_prior_year(
        summary: str,
        published_at: str,
    ) -> bool:
        try:
            published_year = datetime.fromisoformat(published_at[:10]).year
        except ValueError:
            return False
        if re.search(
            r"\u8fd1\u65e5|\u8fd1\u671f|\u540c\u65e5|\u73b0\u5df2|\u5df2\u5b8c\u6210|"
            r"\u5df2\u6279\u51c6|\u6b63\u5f0f\u52a8\u5de5|\u83b7\u53d7\u7406|"
            r"\u6536\u5230.{0,80}\u8ba2\u5355|\u7b7e\u7f72|\u7b7e\u8ba2",
            summary,
        ):
            return False
        years = {
            int(match.group(1))
            for match in re.finditer(
                r"(?<!\d)(20\d{2})(?!\d|\s*(?:亿|万|元|美元|日元|人民币|港元))",
                summary,
            )
        }
        return any(year < published_year for year in years)

    @classmethod
    def _is_historical_event_quote(
        cls,
        text: str,
        published_at: str,
    ) -> bool:
        if cls._is_historical_background(text):
            return True
        try:
            published = datetime.fromisoformat(published_at[:10]).date()
        except ValueError:
            published = None
        if re.search(
            r"\u8fd1\u65e5|\u8fd1\u671f|\u540c\u65e5|\u73b0\u5df2|\u5df2\u5b8c\u6210|"
            r"\u5df2\u6279\u51c6|\u6b63\u5f0f\u52a8\u5de5|\u83b7\u53d7\u7406|"
            r"\u6536\u5230.{0,80}\u8ba2\u5355|\u7b7e\u7f72|\u7b7e\u8ba2",
            text,
        ):
            return False
        standalone_years = [
            int(match.group(1))
            for match in re.finditer(r"(20\d{2})\u5e74(?!\d{1,2}\u6708)", text)
        ]
        if (
            published is not None
            and standalone_years
            and max(standalone_years) < published.year
        ):
            return True
        if re.search(
            r"\u53bb\u5e74|\u5f53\u5e74|\u540c\u5e74|\u4e0a\u6708|\u4e0a\u5468|"
            r"\u4eca\u5e74\u5e74\u521d|\u4eca\u5e74\u4e0a\u534a\u5e74|"
            r"\u6b64\u524d|\u65e9\u5728|\u66fe\u4e8e|\u8fc7\u53bb.{0,8}(?:\u5e74|\u6708)",
            text,
        ):
            return True
        try:
            published = datetime.fromisoformat(published_at[:10]).date()
        except ValueError:
            return False
        dated = [
            marker
            for marker in re.finditer(
                r"(?:(?P<year>20\d{2})\u5e74)?"
                r"(?P<month>\d{1,2})\u6708(?!\u4e2a)"
                r"(?:(?P<day>\d{1,2})\u65e5|"
                r"(?P<period>\u521d|\u4e2d\u65ec|\u5e95))?",
                text,
            )
            if marker.group("year") or marker.group("day") or marker.group("period")
        ]
        if not dated:
            return False
        marker = dated[-1]
        year_text = marker.group("year")
        month = int(marker.group("month"))
        day_text = marker.group("day")
        if year_text and int(year_text) != published.year:
            return True
        if month != published.month:
            return True
        if day_text:
            return int(day_text) < published.day - 1
        period = marker.group("period")
        if period == "\u521d":
            return published.day > 7
        if period == "\u4e2d\u65ec":
            return published.day > 20
        return False

    @staticmethod
    def _is_historical_background(text: str) -> bool:
        return bool(
            re.search(
                r"\u8be5\u8f6e\u878d\u8d44\u4e4b\u524d|"
                r"\u6b64\u524d.{0,20}\u5df2\u4e8e|"
                r"\u66fe\u7ecf.{0,20}\u5206\u522b\u4e8e|"
                r"\u5728\u8be5\u8f6e\u878d\u8d44\u4e4b\u524d|"
                r"\u56de\u987e|\u8ffd\u6eaf|\u5f53\u65f6|"
                r"\u5df2(?:\u5438\u5f15|\u6c47\u805a|\u7ec4\u5efa|\u62e5\u6709)"
                r".{0,160}(?:\u52a0\u5165|\u56e2\u961f|\u4eba\u624d|\u987e\u95ee)|"
                r"\u56e2\u961f(?:\u5305\u62ec|\u6c47\u805a|\u62e5\u6709)|"
                r"\u4ec5(?:\u4ec5)?\u95f4\u9694.{0,12}(?:\u4e2a\u6708|\u5929)|"
                r"\u65f6\u9694.{0,12}(?:\u4e2a\u6708|\u5929)",
                text,
            )
        )

    @classmethod
    def _merge_with_rule_seeds(
        cls,
        rule_events: list[SemanticEvent],
        model_events: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        del rule_events
        # A validated MiniMax response is the authoritative semantic projection
        # for this article. Rules are fallible recall seeds, not facts that must
        # survive a grounded correction. They are used only when both model
        # attempts fail in ``process``.
        return cls._normalize_final_events(model_events)

    @staticmethod
    def _event_key(
        event: SemanticEvent,
    ) -> tuple[str, str, str, str]:
        return (
            event.canonical_company,
            event.event_type,
            event.funding_round,
            event.event_status,
        )

    @staticmethod
    def _is_cumulative_context(text: str, value: str) -> bool:
        start = text.find(value)
        if start < 0:
            return False
        local = text[max(0, start - 20) : start + len(value) + 8]
        return bool(
            re.search(
                r"\u7d2f\u8ba1(?:\u878d\u8d44)?"
                r"(?:\u989d|\u91d1\u989d)?|"
                r"\u603b\u878d\u8d44(?:\u89c4\u6a21|\u989d|\u91d1\u989d)?|"
                r"(?:\d+\u8f6e|\u591a\u8f6e).{0,16}",
                local,
            )
        )


__all__ = [
    "MiniMaxSemanticProcessor",
    "PROMPT_VERSION",
    "SemanticOutputError",
]
