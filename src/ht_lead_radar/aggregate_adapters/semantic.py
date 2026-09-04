"""Evidence-bound MiniMax semantic and ambiguity processor."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from hashlib import sha1
import json
import re
from typing import Any, Mapping, Protocol

from json_repair import repair_json

from .body_scope import (
    clean_semantic_body_scope,
    mask_semantic_body_scope,
    scope_long_article,
)
from .document_router import route_document
from .claim_adjudication import (
    CONTRACT_VERSION as CLAIM_CENTRIC_CONTRACT_VERSION,
    PROMPT_VERSION as CLAIM_CENTRIC_PROMPT_VERSION,
    ClaimCentricSemanticProcessor,
)
from .entities import canonical_company_name, company_alias_candidates, is_company_like
from .models import CleanArticle, SemanticEvent, SourceChannel


PROMPT_VERSION = "aggregate-semantic-v26-shadow"
CLAIM_CONTRACT_VERSION = "v2-shadow"
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
        "workforce_cluster",
        "research_or_ip",
        "project_buildout",
        "global_expansion",
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


MAX_SEMANTIC_RESPONSE_CHARS = 256_000
MAX_SEMANTIC_REPAIR_CHARS = 16_000

SYSTEM_PROMPT = (
    "\u4f60\u662f\u805a\u5408\u65b0\u95fb\u4e8b\u5b9e\u62bd"
    "\u53d6\u5668\u3002\u53ea\u6d88\u89e3\u8bed\u4e49\u548c"
    "\u4e3b\u4f53\u6b67\u4e49\uff0c\u4e0d\u8865\u5145\u5916"
    "\u90e8\u77e5\u8bc6\u3002"
    "\u4ec5\u8f93\u51fa\u4e00\u4e2aJSON\u5bf9\u8c61\uff1a"
    '{"events":[...],"rejections":[{"id":"c_x",'
    '"reason_code":"funding_use_or_plan"}],"ambiguities":[...]}\u3002'
    "\u6bcf\u4e2aevent\u5b57\u6bb5\uff1acompany,event_type,claim_ids,span_ids,"
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
    "\u6bcf\u4e2a\u771f\u5b9e\u5019\u9009\u5fc5\u987b\u8f93\u51fa\u5bf9\u5e94event\uff1b"
    "\u82e5\u662f\u8d44\u91d1\u7528\u9014\u3001\u4e1a\u52a1\u4ecb\u7ecd\u3001\u5386\u53f2\u56de\u987e\u3001"
    "\u884c\u4e1a\u6cdb\u5316\u63cf\u8ff0\u6216\u5176\u4ed6\u975e\u72ec\u7acb\u4e8b\u4ef6\uff0c"
    "\u5fc5\u987b\u628a\u5b83\u7684id\u548c\u53ef\u9a8c\u8bc1reason_code\u653e\u5165rejections\u3002"
    "rule_seed\u4e2d\u6bcf\u4e2aseed\u5fc5\u987b\u88abevent\u8986\u76d6\u6216\u7ea0\u6b63\uff1b"
    "\u82e5seed\u672c\u8eab\u662f\u8bef\u62a5\uff0c\u4e5f\u628a\u5b83\u7684id\u548creason_code\u653e\u5165rejections\u3002"
    "reason_code\u53ea\u80fd\u662ffunding_use_or_plan,historical_or_reference,"
    "generic_commentary,capability_description,invalid_subject,"
    "duplicate_summary\u4e4b\u4e00\uff1b"
    "\u4e0d\u5f97\u4f7f\u7528\u81ea\u7531\u6587\u672c\u7406\u7531\u5220\u9664\u5019\u9009\u3002"
    "rejections.id\u53ea\u80fd\u4f7f\u7528candidate_ledger\u4e2d\u771f\u5b9e\u5b58\u5728\u7684"
    "id\u3001claim_id\u6216atomic_action_hints.claim_id\uff1b"
    "\u4e0d\u5f97\u6dfb\u52a0_partial\u7b49\u540e\u7f00\u3002"
    "\u4e0d\u5f97\u9759\u9ed8\u5ffd\u7565candidate\u6216seed\u3002"
    "\u6bcf\u4e2aevent\u5fc5\u987b\u5f15\u7528candidate_ledger\u4e2d\u7684claim_id\u548cspan_id\uff1b"
    "\u5bbf\u4e3b\u4f1a\u4ecespan\u6062\u590d\u539f\u6587\uff0c\u4e0d\u8981\u6539\u5199span\u6587\u672c\u3002"
    "\u82e5atomic_action_hints\u5305\u542b\u591a\u4e2a\u4e0d\u540cevent_status\uff0c"
    "\u5fc5\u987b\u6309\u5176claim_id\u62c6\u6210\u591a\u4e2a\u72ec\u7acbevent\uff0c"
    "\u4e0d\u5f97\u628acompleted\u4e0estarted/target\u5408\u5e76\u3002"
    "\u5df2\u88abyevent\u8986\u76d6\u6216\u7ea0\u6b63\u7684candidate\u6216seed\u4e0d\u5f97\u540c\u65f6\u653erejections\u3002"
    "\u516c\u53f8\u5df2\u6709\u80fd\u529b\u3001\u4f18\u52bf\u6216\u4f53\u7cfb\u7684\u9759\u6001\u4ecb\u7ecd\u4e0d\u662f\u65b0\u4e8b\u4ef6\uff0c"
    "\u7528capability_description\u62d2\u7edd\uff1b"
    "\u4f46\u660e\u786e\u7684\u5c06\u5efa\u8bbe\u3001\u5c06\u6269\u4ea7\u3001\u5c06\u4e0a\u7ebf\u7b49\u672a\u6765\u8ba1\u5212\u662f\u6709\u6548event\uff0c"
    "\u5fc5\u987bevent_status=target\uff0c\u4e0d\u5f97\u5f53\u4f5c\u80fd\u529b\u4ecb\u7ecd\u62d2\u7edd\u3002"
    "\u5408\u4f5c\u4e8b\u4ef6\u7684company\u53ea\u586b\u4e00\u4e2a\u53ef\u80fd\u4ea7\u751f\u62db\u8058\u7684\u7ecf\u8425\u4e3b\u4f53\uff1b"
    "\u4e0d\u5f97\u628a\u591a\u4e2a\u5408\u4f5c\u65b9\u7528\u2018\u4e0e\u2019\u6216\u2018\u548c\u2019\u62fc\u6210company\u3002"
    "\u82e5\u5408\u4f5c\u53cc\u65b9\u90fd\u662f\u653f\u5e9c\u3001\u59d4\u5458\u4f1a\u3001\u534f\u4f1a\u6216\u975e\u7ecf\u8425\u6027\u516c\u5171\u673a\u6784\uff0c"
    "\u5219\u4e0d\u751f\u6210event\u3002"
)


class MiniMaxSemanticProcessor:
    def __init__(
        self,
        runner: PromptRunner | None,
        *,
        strict_claim_contract: bool = False,
        claim_centric_v27: bool = False,
        claim_prompt_config: Mapping[str, Any] | None = None,
    ) -> None:
        self.runner = runner
        self.strict_claim_contract = strict_claim_contract
        self.claim_centric_v27 = claim_centric_v27
        self.claim_prompt_config = dict(claim_prompt_config or {})
        unknown_prompt_keys = set(self.claim_prompt_config) - {
            "system_prompt",
            "few_shot",
            "prompt_version",
            "contract_version",
        }
        if unknown_prompt_keys:
            raise ValueError(
                f"unsupported claim prompt config keys:{sorted(unknown_prompt_keys)}"
            )
        self.model_identity = self._runner_identity(runner)
        self.last_audit: dict[str, Any] = {}

    @property
    def semantic_prompt_version(self) -> str:
        """Return the prompt namespace persisted for the active mode."""

        if self.claim_centric_v27 and self.runner is not None:
            return str(
                self.claim_prompt_config.get("prompt_version")
                or CLAIM_CENTRIC_PROMPT_VERSION
            )
        return PROMPT_VERSION

    @property
    def semantic_claim_contract_version(self) -> str:
        """Return the exact semantic contract namespace used by this run."""

        if self.claim_centric_v27 and self.runner is not None:
            return str(
                self.claim_prompt_config.get("contract_version")
                or CLAIM_CENTRIC_CONTRACT_VERSION
            )
        return CLAIM_CONTRACT_VERSION

    @property
    def cache_key(self) -> str:
        configured = self.claim_prompt_config.get("prompt_version")
        prompt_version = (
            str(configured or CLAIM_CENTRIC_PROMPT_VERSION)
            if self.claim_centric_v27
            else PROMPT_VERSION
        )
        return (
            f"{prompt_version}|{self.model_identity}|"
            f"{self.semantic_claim_contract_version}"
        )

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

    def project_payload(
        self,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
        payload: dict[str, Any],
        *,
        raw_response: str = "",
    ) -> list[SemanticEvent]:
        """Project one parsed MiniMax payload through production validation.

        Offline experiments and replay tools must evaluate the same final event
        projection used by the production coordinator.  This entry point does
        no model call and deliberately reuses the production grounding,
        rejection, normalization, and completeness checks.
        """

        cleaned_body = clean_semantic_body_scope(article.clean_body)
        scoped_body, window_decision = scope_long_article(
            cleaned_body,
            title=article.index.title,
            document_type=str(
                (article.structured_data or {}).get("document_type") or ""
            ),
        )
        article = replace(
            article,
            clean_body=scoped_body,
            structured_data={
                **dict(article.structured_data or {}),
                "semantic_window": window_decision.to_dict(),
            },
        )
        route = route_document(article)
        normalized_rules = self._normalize_rule_events(article, rule_events)
        candidates = self._claim_candidates(article, normalized_rules)
        contract_observation = self._claim_contract_observation(payload, candidates)
        events, validation_issues = self._validate_payload_parts(
            article,
            payload,
            normalized_rules,
        )
        events, strict_issues = self._enforce_claim_contract(events, candidates)
        validation_issues.extend(strict_issues)
        ambiguities = [
            value for value in payload.get("ambiguities", []) if isinstance(value, str)
        ]
        (
            rejected_candidates,
            rejected_seeds,
            rejection_issues,
        ) = self._validated_rejections_parts(
            article,
            payload,
            candidates,
            normalized_rules,
            events,
        )
        events = self._preserve_rules_for_failed_items(
            events,
            normalized_rules,
            rejected_seeds,
            (*validation_issues, *rejection_issues),
        )
        events = [
            event
            for event in events
            if not (
                event.processor.startswith("rules")
                and self._rule_seed_id(event) in rejected_seeds
            )
        ]
        response = raw_response or json.dumps(payload, ensure_ascii=False)
        events = self._salvage_grounded_investors(
            article,
            events,
            (response,),
            "",
        )
        events = self._normalize_final_events(events)
        events, conflict_count = self._remove_rejection_conflicts(
            events,
            candidates,
            rejected_candidates,
            rejected_seeds,
        )
        events, final_contract_issues = self._enforce_claim_contract(
            events,
            candidates,
        )
        validation_issues.extend(final_contract_issues)
        audit: dict[str, Any] = {
            "source_id": article.index.source_id,
            "source_article_id": article.index.source_article_id,
            "prompt_version": self.semantic_prompt_version,
            "model_identity": self.model_identity,
            "cache_key": self.cache_key,
            "claim_centric_v27": self.claim_centric_v27 and self.runner is not None,
            "strict_claim_contract": self.strict_claim_contract,
            "index_content_hash": article.index.content_hash,
            "article_content_hash": article.content_hash,
            "status": (
                "projected_partial"
                if validation_issues or rejection_issues
                else "projected"
            ),
            "error": "; ".join((*validation_issues, *rejection_issues)),
            "rule_seed_count": len(normalized_rules),
            "candidate_count": len(candidates),
            "document_type": route.document_type,
            "document_route_reason": route.reason,
            "document_family": route.document_family,
            "processing_mode": route.processing_mode,
            "route_gate_confidence": route.gate_confidence,
            "route_llm_gate_required": route.llm_gate_required,
            "route_gate_signals": list(route.gate_signals),
            "document_unit_ids": [unit.unit_id for unit in route.units],
            "semantic_window": window_decision.to_dict(),
            "rejection_conflict_removed_count": conflict_count,
            "validation_issue_count": len(validation_issues),
            "validation_issues": validation_issues,
            "rejection_issue_count": len(rejection_issues),
            "rejection_issues": rejection_issues,
            **contract_observation,
        }
        self._complete_audit(
            audit,
            events,
            normalized_rules,
            candidates=candidates,
            model_ambiguities=ambiguities,
            rejected_candidate_ids=rejected_candidates,
            rejected_seed_ids=rejected_seeds,
        )
        self._complete_model_claim_audit(
            audit,
            events,
            candidates,
            rejected_candidates,
            response_observed=True,
        )
        if self.strict_claim_contract and not audit["strict_claim_contract_ready"]:
            audit["status"] = "projected_partial"
        incomplete_ids = (
            audit.get("model_unadjudicated_claim_ids", [])
            if self.strict_claim_contract
            else audit.get("unmapped_candidate_ids", [])
        )
        if incomplete_ids:
            audit["status"] = "projected_partial"
            suffix = (
                "claim ledger remained incomplete: "
                f"{incomplete_ids}"
            )
            audit["error"] = "; ".join(
                value for value in (audit["error"], suffix) if value
            )
        self.last_audit = audit
        return events

    def _enforce_claim_contract(
        self,
        events: list[SemanticEvent],
        candidates: list[dict[str, Any]] | None = None,
    ) -> tuple[list[SemanticEvent], list[str]]:
        """Drop uncited model events only after the strict migration switch flips."""

        if not self.strict_claim_contract:
            return events, []
        claim_to_span = self._claim_to_span(candidates or [])
        kept: list[SemanticEvent] = []
        issues: list[str] = []
        for position, event in enumerate(events):
            if event.processor == "minimax":
                if not event.claim_ids or not event.span_ids:
                    issues.append(
                        f"strict claim contract removed uncited event[{position}]"
                    )
                    continue
                if claim_to_span:
                    expected = {
                        claim_to_span.get(claim_id, "")
                        for claim_id in event.claim_ids
                    }
                    if "" in expected or expected != set(event.span_ids):
                        issues.append(
                            "strict claim contract removed invalid claim/span "
                            f"pair event[{position}]"
                        )
                        continue
            kept.append(event)
        return self._normalize_final_events(kept), issues

    @classmethod
    def _validate_payload_parts(
        cls,
        article: CleanArticle,
        payload: dict[str, Any],
        rule_events: list[SemanticEvent],
    ) -> tuple[list[SemanticEvent], list[str]]:
        """Validate events independently so one malformed item cannot erase peers."""

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
        if not raw_events:
            return cls._validate_payload(article, payload, rule_events), []
        output: list[SemanticEvent] = []
        issues: list[str] = []
        for position, raw_event in enumerate(raw_events):
            try:
                output.extend(
                    cls._validate_payload(
                        article,
                        {
                            "events": [raw_event],
                            "ambiguities": ambiguities,
                        },
                        rule_events,
                    )
                )
            except Exception as error:
                issues.append(f"event[{position}]:{type(error).__name__}:{error}")
        if not output:
            output.extend(rule_events)
        return cls._normalize_final_events(output), issues

    @staticmethod
    def _claim_contract_observation(
        payload: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Measure strict citation readiness without changing shadow behavior."""

        claim_to_span: dict[str, str] = {}
        for candidate in candidates:
            claim_to_span[str(candidate["claim_id"])] = str(candidate["span_id"])
            for atomic in candidate.get("atomic_action_hints") or []:
                claim_to_span[str(atomic["claim_id"])] = str(atomic["span_id"])
        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            return {
                "claim_contract_version": CLAIM_CONTRACT_VERSION,
                "raw_model_event_count": 0,
                "cited_model_event_count": 0,
                "uncited_model_event_count": 0,
                "bad_claim_pair_event_count": 0,
                "uncited_model_event_positions": [],
                "bad_claim_pair_event_positions": [],
                "strict_claim_contract_ready": False,
            }
        cited = 0
        uncited: list[int] = []
        bad_pair: list[int] = []
        for position, event in enumerate(raw_events):
            if not isinstance(event, dict):
                bad_pair.append(position)
                continue
            claim_ids = event.get("claim_ids") or []
            span_ids = event.get("span_ids") or []
            if not claim_ids and not span_ids:
                uncited.append(position)
                continue
            if (
                not isinstance(claim_ids, list)
                or not isinstance(span_ids, list)
                or any(not isinstance(value, str) for value in claim_ids)
                or any(not isinstance(value, str) for value in span_ids)
            ):
                bad_pair.append(position)
                continue
            expected = {claim_to_span.get(value, "") for value in claim_ids}
            if "" in expected or expected != set(span_ids):
                bad_pair.append(position)
                continue
            cited += 1
        return {
            "claim_contract_version": CLAIM_CONTRACT_VERSION,
            "raw_model_event_count": len(raw_events),
            "cited_model_event_count": cited,
            "uncited_model_event_count": len(uncited),
            "bad_claim_pair_event_count": len(bad_pair),
            "uncited_model_event_positions": uncited,
            "bad_claim_pair_event_positions": bad_pair,
            "strict_claim_contract_ready": not uncited and not bad_pair,
        }

    @staticmethod
    def _candidate_claim_ids(candidate: dict[str, Any]) -> set[str]:
        return {
            str(value)
            for value in (
                candidate.get("claim_id"),
                *(
                    atomic.get("claim_id")
                    for atomic in candidate.get("atomic_action_hints") or []
                ),
            )
            if value
        }

    @staticmethod
    def _required_claim_ids(candidate: dict[str, Any]) -> set[str]:
        atomic_ids = {
            str(atomic.get("claim_id") or "")
            for atomic in candidate.get("atomic_action_hints") or []
            if str(atomic.get("claim_id") or "")
        }
        if len(atomic_ids) > 1 or candidate.get("event_status_hint") == "mixed":
            return atomic_ids
        claim_id = str(candidate.get("claim_id") or "")
        return {claim_id} if claim_id else set()

    @classmethod
    def _claim_to_span(
        cls,
        candidates: list[dict[str, Any]],
    ) -> dict[str, str]:
        output: dict[str, str] = {}
        for candidate in candidates:
            claim_id = str(candidate.get("claim_id") or "")
            span_id = str(candidate.get("span_id") or "")
            if claim_id and span_id:
                output[claim_id] = span_id
            for atomic in candidate.get("atomic_action_hints") or []:
                atomic_claim = str(atomic.get("claim_id") or "")
                atomic_span = str(atomic.get("span_id") or "")
                if atomic_claim and atomic_span:
                    output[atomic_claim] = atomic_span
        return output

    @classmethod
    def _flat_claim_ledger(
        cls,
        candidates: list[dict[str, Any]],
        *,
        only_claim_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Expose one small, immutable row per claim to the model.

        Nested candidate objects made MiniMax copy parent IDs, atomic IDs and
        partially matching spans into the same event.  The host still keeps the
        richer candidate structure; the model only needs the exact identity,
        source span and deterministic hints for each claim it must adjudicate.
        """

        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            parent_claim_id = str(candidate.get("claim_id") or "")
            parent_span_id = str(candidate.get("span_id") or "")
            atomic = {
                str(item.get("claim_id") or ""): item
                for item in candidate.get("atomic_action_hints") or []
                if str(item.get("claim_id") or "")
            }
            for claim_id in sorted(cls._required_claim_ids(candidate)):
                if only_claim_ids is not None and claim_id not in only_claim_ids:
                    continue
                item = atomic.get(claim_id, {})
                rows.append(
                    {
                        "id": claim_id,
                        "parent_candidate_id": str(candidate.get("id") or ""),
                        "claim_id": claim_id,
                        "span_id": str(item.get("span_id") or parent_span_id),
                        "char_start": item.get(
                            "char_start", candidate.get("char_start")
                        ),
                        "char_end": item.get("char_end", candidate.get("char_end")),
                        "text": str(item.get("text") or candidate.get("quote") or ""),
                        "action_focus": str(item.get("action_text") or ""),
                        "subject_hint": str(candidate.get("subject_hint") or ""),
                        "event_type_hint": str(candidate.get("event_type") or ""),
                        "event_status_hint": str(
                            item.get("event_status")
                            or candidate.get("event_status_hint")
                            or "unknown"
                        ),
                        "funding_round_hint": str(
                            candidate.get("funding_round") or ""
                        ),
                        "time_hints": list(candidate.get("time_hints") or []),
                        "rule_seed_id": str(candidate.get("rule_seed_id") or ""),
                        "parent_claim_id": parent_claim_id,
                    }
                )
        return rows

    @classmethod
    def _model_accepted_claim_ids(
        cls,
        events: list[SemanticEvent],
        candidates: list[dict[str, Any]],
    ) -> list[str]:
        required = {
            claim_id
            for candidate in candidates
            for claim_id in cls._required_claim_ids(candidate)
        }
        cited = {
            claim_id
            for event in events
            if event.processor == "minimax"
            for claim_id in event.claim_ids
        }
        return sorted(required & cited)

    @classmethod
    def _model_accepted_candidate_ids(
        cls,
        events: list[SemanticEvent],
        candidates: list[dict[str, Any]],
    ) -> list[str]:
        accepted_claim_ids = set(cls._model_accepted_claim_ids(events, candidates))
        return [
            str(candidate["id"])
            for candidate in candidates
            if cls._required_claim_ids(candidate)
            and cls._required_claim_ids(candidate).issubset(accepted_claim_ids)
        ]

    @classmethod
    def _model_unadjudicated_claim_ids(
        cls,
        events: list[SemanticEvent],
        candidates: list[dict[str, Any]],
        rejected_candidate_ids: set[str],
    ) -> list[str]:
        accepted = set(cls._model_accepted_claim_ids(events, candidates))
        rejected_claims = cls._expanded_rejected_claim_ids(
            candidates,
            rejected_candidate_ids,
        )
        return sorted(
            claim_id
            for candidate in candidates
            for claim_id in cls._required_claim_ids(candidate)
            if claim_id not in accepted | rejected_claims
        )

    @classmethod
    def _expanded_rejected_claim_ids(
        cls,
        candidates: list[dict[str, Any]],
        rejected_ids: set[str],
    ) -> set[str]:
        output = set(rejected_ids)
        for candidate in candidates:
            if str(candidate.get("id") or "") in rejected_ids:
                output.update(cls._required_claim_ids(candidate))
        return output

    @classmethod
    def _model_unadjudicated_candidate_ids(
        cls,
        events: list[SemanticEvent],
        candidates: list[dict[str, Any]],
        rejected_candidate_ids: set[str],
    ) -> list[str]:
        unresolved_claim_ids = set(
            cls._model_unadjudicated_claim_ids(
                events,
                candidates,
                rejected_candidate_ids,
            )
        )
        return [
            str(candidate["id"])
            for candidate in candidates
            if cls._required_claim_ids(candidate) & unresolved_claim_ids
        ]

    @classmethod
    def _complete_model_claim_audit(
        cls,
        audit: dict[str, Any],
        events: list[SemanticEvent],
        candidates: list[dict[str, Any]],
        rejected_candidate_ids: set[str],
        *,
        response_observed: bool,
    ) -> None:
        """Report final identity-based model adjudication, excluding rule fallback."""

        model_events = [event for event in events if event.processor == "minimax"]
        claim_to_span = cls._claim_to_span(candidates)
        uncited_events = [
            event
            for event in model_events
            if not event.claim_ids or not event.span_ids
        ]
        bad_pair_events = [
            event
            for event in model_events
            if event.claim_ids
            and event.span_ids
            and (
                {
                    claim_to_span.get(claim_id, "")
                    for claim_id in event.claim_ids
                }
                != set(event.span_ids)
                or any(claim_id not in claim_to_span for claim_id in event.claim_ids)
            )
        ]
        accepted_claim_ids = cls._model_accepted_claim_ids(events, candidates)
        unadjudicated_claim_ids = cls._model_unadjudicated_claim_ids(
            events,
            candidates,
            rejected_candidate_ids,
        )
        accepted_ids = cls._model_accepted_candidate_ids(events, candidates)
        unadjudicated_ids = cls._model_unadjudicated_candidate_ids(
            events,
            candidates,
            rejected_candidate_ids,
        )
        audit["model_accepted_claim_ids"] = accepted_claim_ids
        audit["model_unadjudicated_claim_ids"] = unadjudicated_claim_ids
        audit["model_accepted_candidate_ids"] = accepted_ids
        audit["model_unadjudicated_candidate_ids"] = unadjudicated_ids
        audit["final_model_event_count"] = len(model_events)
        audit["final_uncited_model_event_count"] = len(uncited_events)
        audit["final_bad_claim_pair_event_count"] = len(bad_pair_events)
        audit["strict_claim_contract_ready"] = bool(response_observed) and not (
            uncited_events or bad_pair_events or unadjudicated_claim_ids
        )

    @classmethod
    def _preserve_rules_for_failed_items(
        cls,
        events: list[SemanticEvent],
        rule_events: list[SemanticEvent],
        rejected_seed_ids: set[str],
        issues: tuple[str, ...],
    ) -> list[SemanticEvent]:
        if not issues or any(event.processor == "minimax" for event in events):
            return events
        fallback = [
            replace(
                seed,
                ambiguities=tuple(
                    dict.fromkeys(
                        (
                            *seed.ambiguities,
                            "minimax_claim_validation_failed",
                        )
                    )
                ),
            )
            for seed in rule_events
            if cls._rule_seed_id(seed) not in rejected_seed_ids
        ]
        return cls._normalize_final_events(fallback)

    def process(
        self,
        channel: SourceChannel,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        source_body = article.clean_body
        source_index_content_hash = article.index.content_hash
        source_article_content_hash = article.content_hash
        if self.claim_centric_v27 and self.runner is not None:
            scoped_body, window_decision = scope_long_article(
                mask_semantic_body_scope(source_body),
                title=article.index.title,
                document_type=str(
                    (article.structured_data or {}).get("document_type") or ""
                ),
            )
            article = replace(
                article,
                clean_body=scoped_body,
                structured_data={
                    **dict(article.structured_data or {}),
                    "semantic_window": window_decision.to_dict(),
                },
            )
            rule_events = [
                replace(
                    event,
                    prompt_version=self.semantic_prompt_version,
                    content_hash=source_article_content_hash,
                )
                for event in self._normalize_rule_events(article, rule_events)
            ]
            v27 = ClaimCentricSemanticProcessor(
                self.runner,
                model_identity=self.model_identity,
                **self.claim_prompt_config,
            )
            events = v27.process(
                channel,
                article,
                rule_events,
                self._claim_candidates(article, rule_events),
                source_body=source_body,
            )
            events = self._preserve_v27_policy_rule_seeds(events, rule_events)
            self.last_audit = dict(v27.last_audit)
            self.last_audit["claim_centric_v27"] = True
            self.last_audit["strict_claim_contract"] = self.strict_claim_contract
            self.last_audit["claim_contract_version"] = (
                self.semantic_claim_contract_version
            )
            # Claim-centric processing scopes the body for model input, but
            # cache validity is tied to the original persisted index and clean
            # article.  Persist both hashes so a second daily crawl can prove
            # that the materialized events still belong to this exact body.
            self.last_audit["index_content_hash"] = source_index_content_hash
            self.last_audit["article_content_hash"] = source_article_content_hash
            # Policy seeds may be retained after claim-centric projection and
            # the final normalizer may merge duplicate events.  Report the
            # same post-preservation count that callers receive.
            self.last_audit["final_event_count"] = len(events)
            self.last_audit["policy_rule_seed_preserved_count"] = sum(
                1
                for event in events
                if "claim_loop_policy_seed_preserved" in event.ambiguities
            )
            self.last_audit["semantic_window"] = window_decision.to_dict()
            return events
        article = replace(
            article,
            clean_body=clean_semantic_body_scope(source_body),
        )
        rule_events = [
            replace(
                event,
                prompt_version=self.semantic_prompt_version,
                content_hash=source_article_content_hash,
            )
            for event in self._normalize_rule_events(article, rule_events)
        ]
        route = route_document(article)
        units = list(route.units)
        if not units:
            units = [
                type(
                    "_WholeArticleUnit",
                    (),
                    {
                        "unit_id": "u_whole",
                        "char_start": 0,
                        "char_end": len(article.clean_body),
                    },
                )()
            ]
        candidates = self._claim_candidates(article, rule_events)
        audit: dict[str, Any] = {
            "source_id": article.index.source_id,
            "source_article_id": article.index.source_article_id,
            "prompt_version": self.semantic_prompt_version,
            "model_identity": self.model_identity,
            "cache_key": self.cache_key,
            "claim_contract_version": self.semantic_claim_contract_version,
            "claim_centric_v27": self.claim_centric_v27 and self.runner is not None,
            "strict_claim_contract": self.strict_claim_contract,
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
            "chunk_count": len(units),
            "document_type": route.document_type,
            "document_route_reason": route.reason,
            "document_family": route.document_family,
            "processing_mode": route.processing_mode,
            "route_gate_confidence": route.gate_confidence,
            "route_llm_gate_required": route.llm_gate_required,
            "route_gate_signals": list(route.gate_signals),
            "document_unit_ids": [unit.unit_id for unit in route.units],
            "chunk_statuses": [],
            "candidate_count": len(candidates),
            "rejected_candidate_count": 0,
            "rejected_candidate_ids": [],
            "explicitly_rejected_seed_count": 0,
            "explicitly_rejected_seed_ids": [],
            "unmapped_candidate_count": 0,
            "unmapped_candidate_ids": [],
            "validation_issue_count": 0,
            "validation_issues": [],
            "rejection_issue_count": 0,
            "rejection_issues": [],
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
        validation_issues: list[str] = []
        rejection_issues: list[str] = []
        contract_observations: list[dict[str, Any]] = []
        rejected_candidate_ids: set[str] = set()
        rejected_seed_ids: set[str] = set()
        for chunk_index, unit in enumerate(units, start=1):
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
                structured_data={
                    **dict(article.structured_data or {}),
                    "_semantic_unit": {
                        "unit_id": unit.unit_id,
                        "char_start": unit.char_start,
                        "char_end": unit.char_end,
                    },
                },
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
                contract_observations.append(
                    self._claim_contract_observation(
                        payload,
                        self._claim_candidates(chunk_article, chunk_rules),
                    )
                )
                events, chunk_validation_issues = self._validate_payload_parts(
                    chunk_article,
                    payload,
                    chunk_rules,
                )
                events, strict_issues = self._enforce_claim_contract(
                    events,
                    self._claim_candidates(chunk_article, chunk_rules),
                )
                chunk_validation_issues.extend(strict_issues)
                ambiguities = [
                    value
                    for value in payload.get("ambiguities", [])
                    if isinstance(value, str)
                ]
                chunk_candidates = self._claim_candidates(
                    chunk_article,
                    chunk_rules,
                )
                (
                    chunk_rejected_candidates,
                    chunk_rejected_seeds,
                    chunk_rejection_issues,
                ) = self._validated_rejections_parts(
                    chunk_article,
                    payload,
                    chunk_candidates,
                    chunk_rules,
                    events,
                )
                events = self._preserve_rules_for_failed_items(
                    events,
                    chunk_rules,
                    chunk_rejected_seeds,
                    (*chunk_validation_issues, *chunk_rejection_issues),
                )
                events = [
                    event
                    for event in events
                    if not (
                        event.processor.startswith("rules")
                        and self._rule_seed_id(event) in chunk_rejected_seeds
                    )
                ]
                rejected_candidate_ids.update(chunk_rejected_candidates)
                rejected_seed_ids.update(chunk_rejected_seeds)
                validation_issues.extend(
                    f"chunk {chunk_index}:{issue}" for issue in chunk_validation_issues
                )
                rejection_issues.extend(
                    f"chunk {chunk_index}:{issue}" for issue in chunk_rejection_issues
                )
                model_ambiguities.extend(ambiguities)
                events = self._salvage_grounded_investors(
                    chunk_article,
                    events,
                    (response,),
                    "",
                )
                statuses.append(
                    "accepted_partial"
                    if chunk_validation_issues or chunk_rejection_issues
                    else "accepted"
                )
                all_events.extend(events)
                first_responses.append(response)
                repair_responses.append("")
                continue
            except Exception as first_error:
                first_error_text = f"{type(first_error).__name__}: {first_error}"
            try:
                repair_prompt = (
                    "The prior output failed JSON or evidence grounding validation. "
                    "Return one corrected strict JSON object under the original system "
                    "constraints. Do not use Markdown fences. Escape every ASCII double "
                    'quote inside string values as \\"; prefer Chinese corner quotes '
                    "inside summaries. Do not repeat a rejected field. Put only "
                    "deterministically supportable false positives in rejections as "
                    "{id, reason_code}; use only the allowed reason codes.\n"
                    f"Original input: {prompt}\n"
                    f"Prior output: {response[:4000]}\n"
                    f"Validation error: {first_error_text}"
                )
                if "TimeoutError" in first_error_text and not response.strip():
                    # A timeout produced no malformed answer to repair. Retrying
                    # the original, shorter prompt is both faster and less likely
                    # to time out than wrapping the full input in repair prose.
                    repair_prompt = prompt
                repair = self.runner.run(
                    repair_prompt,
                    session_id=(
                        f"aggregate-repair:{article.index.source_id}:"
                        f"{article.index.source_article_id}:"
                        f"{article.content_hash[:12]}:chunk-{chunk_index}"
                    ),
                    system_prompt=SYSTEM_PROMPT,
                )
                payload = self._parse_json(repair, allow_syntax_repair=True)
                contract_observations.append(
                    self._claim_contract_observation(
                        payload,
                        self._claim_candidates(chunk_article, chunk_rules),
                    )
                )
                events, chunk_validation_issues = self._validate_payload_parts(
                    chunk_article,
                    payload,
                    chunk_rules,
                )
                events, strict_issues = self._enforce_claim_contract(
                    events,
                    self._claim_candidates(chunk_article, chunk_rules),
                )
                chunk_validation_issues.extend(strict_issues)
                ambiguities = [
                    value
                    for value in payload.get("ambiguities", [])
                    if isinstance(value, str)
                ]
                chunk_candidates = self._claim_candidates(
                    chunk_article,
                    chunk_rules,
                )
                (
                    chunk_rejected_candidates,
                    chunk_rejected_seeds,
                    chunk_rejection_issues,
                ) = self._validated_rejections_parts(
                    chunk_article,
                    payload,
                    chunk_candidates,
                    chunk_rules,
                    events,
                )
                events = self._preserve_rules_for_failed_items(
                    events,
                    chunk_rules,
                    chunk_rejected_seeds,
                    (*chunk_validation_issues, *chunk_rejection_issues),
                )
                events = [
                    event
                    for event in events
                    if not (
                        event.processor.startswith("rules")
                        and self._rule_seed_id(event) in chunk_rejected_seeds
                    )
                ]
                rejected_candidate_ids.update(chunk_rejected_candidates)
                rejected_seed_ids.update(chunk_rejected_seeds)
                validation_issues.extend(
                    f"chunk {chunk_index}:{issue}" for issue in chunk_validation_issues
                )
                rejection_issues.extend(
                    f"chunk {chunk_index}:{issue}" for issue in chunk_rejection_issues
                )
                model_ambiguities.extend(ambiguities)
                events = self._salvage_grounded_investors(
                    chunk_article,
                    events,
                    (response, repair),
                    "",
                )
                statuses.append(
                    "repaired_partial"
                    if chunk_validation_issues or chunk_rejection_issues
                    else "repaired"
                )
                all_events.extend(events)
            except Exception as repair_error:
                statuses.append("fallback_to_rules")
                errors.append(
                    f"chunk {chunk_index}: {first_error_text}; repair "
                    f"{type(repair_error).__name__}: {repair_error}"
                )
                marker = f"minimax_validation_failed:{type(repair_error).__name__}"
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
        events, rejection_conflict_count = self._remove_rejection_conflicts(
            events,
            candidates,
            rejected_candidate_ids,
            rejected_seed_ids,
        )
        events, final_contract_issues = self._enforce_claim_contract(
            events,
            candidates,
        )
        validation_issues.extend(
            f"fan-in:{issue}" for issue in final_contract_issues
        )
        audit["chunk_statuses"] = statuses
        audit["rejection_conflict_removed_count"] = rejection_conflict_count
        audit["first_response"] = self._audit_responses(first_responses)
        audit["repair_response"] = self._audit_responses(repair_responses)
        audit["validation_issue_count"] = len(validation_issues)
        audit["validation_issues"] = validation_issues
        audit["rejection_issue_count"] = len(rejection_issues)
        audit["rejection_issues"] = rejection_issues
        audit["claim_contract_version"] = CLAIM_CONTRACT_VERSION
        for field in (
            "raw_model_event_count",
            "cited_model_event_count",
            "uncited_model_event_count",
            "bad_claim_pair_event_count",
        ):
            audit[field] = sum(
                int(observation.get(field, 0))
                for observation in contract_observations
            )
        audit["strict_claim_contract_ready"] = bool(contract_observations) and not (
            audit["uncited_model_event_count"]
            or audit["bad_claim_pair_event_count"]
        )
        audit["error"] = "; ".join((*errors, *validation_issues, *rejection_issues))
        if "fallback_to_rules" in statuses:
            audit["status"] = "fallback_to_rules"
        elif any(status.endswith("_partial") for status in statuses):
            audit["status"] = "partial"
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
            rejected_candidate_ids=rejected_candidate_ids,
            rejected_seed_ids=rejected_seed_ids,
        )
        initial_unmapped_ids = (
            self._model_unadjudicated_claim_ids(
                events,
                candidates,
                rejected_candidate_ids,
            )
            if self.strict_claim_contract
            else list(audit["unmapped_candidate_ids"])
        )
        audit["model_unadjudicated_claim_ids_before_retry"] = list(
            initial_unmapped_ids
        )
        audit["model_unadjudicated_candidate_ids_before_retry"] = (
            self._model_unadjudicated_candidate_ids(
                events,
                candidates,
                rejected_candidate_ids,
            )
            if self.strict_claim_contract
            else list(initial_unmapped_ids)
        )
        retry_result = self._retry_unmapped_claims(
            article,
            rule_events,
            candidates,
            events,
            initial_unmapped_ids,
        )
        audit["claim_retry_attempted"] = bool(retry_result["attempted"])
        audit["deterministic_rejected_claim_ids"] = sorted(
            retry_result.get("deterministic_rejected_claim_ids", set())
        )
        audit["claim_retry_response"] = str(retry_result.get("response") or "")
        audit["claim_retry_error"] = str(retry_result.get("error") or "")
        if retry_result["attempted"]:
            events = list(retry_result["events"])
            rejected_candidate_ids.update(
                retry_result.get("rejected_candidate_ids", set())
            )
            rejected_seed_ids.update(retry_result.get("rejected_seed_ids", set()))
            retry_validation_issues = [
                f"claim retry:{issue}"
                for issue in retry_result.get("validation_issues", [])
            ]
            retry_rejection_issues = [
                f"claim retry:{issue}"
                for issue in retry_result.get("rejection_issues", [])
            ]
            validation_issues.extend(retry_validation_issues)
            rejection_issues.extend(retry_rejection_issues)
            model_ambiguities.extend(retry_result.get("ambiguities", []))
            audit["rejection_conflict_removed_count"] += int(
                retry_result.get("rejection_conflict_removed_count", 0)
            )
            retry_observation = retry_result.get("claim_contract_observation")
            if isinstance(retry_observation, dict):
                contract_observations.append(retry_observation)
            self._complete_audit(
                audit,
                events,
                rule_events,
                candidates=candidates,
                model_ambiguities=model_ambiguities,
                rejected_candidate_ids=rejected_candidate_ids,
                rejected_seed_ids=rejected_seed_ids,
            )
        final_unmapped_ids = (
            self._model_unadjudicated_claim_ids(
                events,
                candidates,
                rejected_candidate_ids,
            )
            if self.strict_claim_contract
            else list(audit["unmapped_candidate_ids"])
        )
        audit["claim_retry_resolved_claim_ids"] = sorted(
            set(initial_unmapped_ids) - set(final_unmapped_ids)
        )
        audit["claim_retry_unresolved_claim_ids"] = final_unmapped_ids
        if self.strict_claim_contract:
            final_unresolved_candidates = self._model_unadjudicated_candidate_ids(
                events,
                candidates,
                rejected_candidate_ids,
            )
            initial_unresolved_candidates = set(
                audit["model_unadjudicated_candidate_ids_before_retry"]
            )
            audit["claim_retry_resolved_candidate_ids"] = sorted(
                initial_unresolved_candidates - set(final_unresolved_candidates)
            )
            audit["claim_retry_unresolved_candidate_ids"] = (
                final_unresolved_candidates
            )
        else:
            audit["claim_retry_resolved_candidate_ids"] = list(
                audit["claim_retry_resolved_claim_ids"]
            )
            audit["claim_retry_unresolved_candidate_ids"] = list(
                final_unmapped_ids
            )
        audit["validation_issue_count"] = len(validation_issues)
        audit["validation_issues"] = validation_issues
        audit["rejection_issue_count"] = len(rejection_issues)
        audit["rejection_issues"] = rejection_issues
        retry_error = str(retry_result.get("error") or "")
        audit["error"] = "; ".join(
            value
            for value in (
                *errors,
                *validation_issues,
                *rejection_issues,
                retry_error,
            )
            if value
        )
        if retry_result["attempted"] and not final_unmapped_ids and not retry_error:
            audit["status"] = "repaired"
        incomplete_ids = (
            final_unmapped_ids
            if self.strict_claim_contract
            else list(audit["unmapped_candidate_ids"])
        )
        if incomplete_ids:
            if audit["status"] == "accepted":
                audit["status"] = "partial"
            elif audit["status"] == "repaired":
                audit["status"] = "repaired_partial"
            suffix = (
                "full-article claim ledger remained incomplete: "
                f"{incomplete_ids}"
            )
            audit["error"] = "; ".join(
                value for value in (audit["error"], suffix) if value
            )
        for field in (
            "raw_model_event_count",
            "cited_model_event_count",
            "uncited_model_event_count",
            "bad_claim_pair_event_count",
        ):
            audit[field] = sum(
                int(observation.get(field, 0))
                for observation in contract_observations
            )
        self._complete_model_claim_audit(
            audit,
            events,
            candidates,
            rejected_candidate_ids,
            response_observed=bool(contract_observations),
        )
        if (
            self.strict_claim_contract
            and not audit["strict_claim_contract_ready"]
            and audit["status"] in {"accepted", "repaired"}
        ):
            audit["status"] = "partial"
        return events

    @staticmethod
    def _audit_responses(responses: list[str]) -> str:
        if len(responses) == 1:
            return responses[0]
        return json.dumps(responses, ensure_ascii=False)

    @staticmethod
    def _semantic_chunks(body: str, *, max_chars: int = 5000) -> list[str]:
        """Split only long digests, keeping ordinary articles single-call."""

        if len(body) <= 6500:
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
    def _semantic_unit_bounds(article: CleanArticle) -> tuple[int, int]:
        raw = dict(article.structured_data or {}).get("_semantic_unit")
        if not isinstance(raw, dict):
            return 0, len(article.clean_body)
        try:
            start = int(raw.get("char_start"))
            end = int(raw.get("char_end"))
        except (TypeError, ValueError):
            return 0, len(article.clean_body)
        if start < 0 or end <= start or end > len(article.clean_body):
            return 0, len(article.clean_body)
        return start, end

    @classmethod
    def _semantic_unit_text(cls, article: CleanArticle) -> str:
        start, end = cls._semantic_unit_bounds(article)
        return article.clean_body[start:end]

    @classmethod
    def _rules_for_chunk(
        cls,
        rule_events: list[SemanticEvent],
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        text = (
            f"{article.index.title}\n{article.index.summary}\n"
            f"{cls._semantic_unit_text(article)}"
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
                r"[A-H](?:\+{1,2})?轮|天使轮|种子轮)|"
                r"(?:拟|计划|将)?(?:非公开发行|定向增发|定增|发行)"
                r".{0,100}(?:募资|募集资金|公司债券)"
            ),
            "executive_change": (
                r"(?:任命|聘任|出任|担任|加入|离任|辞任|升任|接任)"
                r".{0,60}(?:董事长|总裁|CEO|首席|总经理|负责人|一号位|"
                r"副总裁|VP|总监)"
            ),
            "ipo_or_listing": (
                r"(?:递表|提交上市申请|启动IPO|港股IPO|完成上市|正式上市|挂牌|"
                r"实施.{0,40}风险警示|撤销.{0,40}风险警示|复牌|停牌)"
            ),
            "major_order": (
                r"(?:中标|签订|签署|获得|斩获|获(?!悉)).{0,80}"
                r"(?:订单|合同|采购项目|项目定点|供应定点)"
            ),
            "factory_or_capacity": (
                r"(?:开工|投产|扩产|扩建|建设|建成|落地|投建).{0,80}"
                r"(?:工厂|产线|基地|产能|制造)|"
                r"(?:工厂|产线|基地|产能|产量).{0,80}"
                r"(?:开工|投产|扩产|扩建|建设|建成|落地|提升|扩大)|"
                r"(?:新增|追加|承诺).{0,60}(?:投资).{0,100}"
                r"(?:制造|产能|工厂|产线)|"
                r"(?:拟|计划).{0,40}投建|规模化量产"
            ),
            "partnership": (
                r"(?:达成|签署|建立|开展|深化).{0,60}"
                r"(?:战略合作|合作协议|合作备忘录|长期合作|合资)|"
                r"与.{2,60}(?:开展|达成|签署|建立|深化).{0,40}合作"
            ),
            "technical_milestone": (
                r"(?:发布|推出|首发|量产|交付|获批|上线公测|正式开源|"
                r"搭建完成|研制成功).{0,100}"
                r"(?:芯片|机器人|模型|产品|平台|设备|系统|药物|卫星|火箭|技术|"
                r"赛道|专项|专题|挑战赛)|"
                r"(?:芯片|机器人|模型|产品|平台|设备|系统|药物|卫星|火箭|技术|"
                r"数据矿|决策大脑)"
                r".{0,100}(?:发布|推出|首发|量产|交付|下线|获批|上线公测|"
                r"正式开源|搭建完成|研制成功)|"
                r"(?:正式版|API|数据矿|模型).{0,80}(?:上线公测|开源|搭建完成)|"
                r"正在开发.{0,100}(?:产品|技术|芯片|模型)|"
                r"(?:模型|产品|API).{0,60}开源"
            ),
            "new_site_or_entity": (
                r"(?:成立|设立|注册|落地|启用).{0,80}"
                r"(?:公司|子公司|中心|基地|实验室|研究院)|"
                r"(?:公司|子公司|中心|基地|实验室|研究院)"
                r".{0,40}(?:成立|设立|注册|落地|启用)"
            ),
            "regulatory_or_clinical": (
                r"(?:获批|批准|受理|取得|获得).{0,80}"
                r"(?:许可|认证|资质|临床|注册证|测试牌照|测试许可)|"
                r"(?:项目|方案|申请).{0,60}获核准|"
                r"(?:被|因.{0,40})(?:证监会)?立案|"
                r"收到.{0,80}(?:行政处罚|处罚决定书|事先告知书)|"
                r"实施.{0,40}风险警示"
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
                r"(?:采用|导入|验证|定点|复购)|"
                r"(?:实现|完成).{0,60}(?:销售发货|客户交付)|"
                r"(?:销售发货|商业化交付)|"
                r"(?:验证|试点).{0,80}(?:落地路径|商业路径)|"
                r"(?:选定|确定).{0,80}(?:技术方|供应商|方案商)|"
                r"(?:连续)?完成.{0,60}\d+轮[^。！？；\n]{0,30}"
                r"(?:实验操作|实验|测试|验证)"
            ),
            "merger_acquisition": (
                r"(?:拟|宣布|完成|同意).{0,80}(?:收购|并购|合并|出售|受让)|"
                r"(?:收购|并购|合并).{0,80}(?:完成|获批|交割)|"
                r"(?:控股股东|实控人|控制权).{0,80}(?:变更|转让)|"
                r"筹划.{0,60}控制权变更|"
                r"拟公开挂牌转让.{0,80}股权"
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
                if (
                    event_type == "funding"
                    and re.search(
                        r"(?:\u8fde\u7eed|\u7d2f\u8ba1|\u5171).{0,20}"
                        r"[\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+\u8f6e\u878d\u8d44",
                        sentence,
                    )
                    and not re.search(
                        r"(?:Pre[ -]?)?[A-H](?:\+{1,2})?\u8f6e|"
                        r"\u5929\u4f7f(?:\+{1,2})?\u8f6e|"
                        r"\u79cd\u5b50(?:\+{1,2})?\u8f6e|Pre[ -]?IPO",
                        sentence,
                        re.I,
                    )
                ):
                    continue
                if event_type == "technical_milestone" and re.search(
                    r"\u4ea7\u54c1(?:\u4ecb\u7ecd|\u5ba3\u4f20|\u56de\u987e)",
                    sentence,
                ):
                    continue
                if (
                    event_type == "technical_milestone"
                    and re.search(
                        r"(?:\u5177\u5907|\u62e5\u6709|\u5f62\u6210|\u662f|\u4f5c\u4e3a)"
                        r".{0,120}(?:\u80fd\u529b|\u4f18\u52bf|\u4f53\u7cfb)|"
                        r"(?:\u5168\u6d41\u7a0b|\u7cfb\u7edf\u6027).{0,20}\u80fd\u529b",
                        sentence,
                    )
                    and not re.search(
                        r"(?:\u8fd1\u65e5|\u8fd1\u671f|\u6b63\u5f0f|\u9996\u6b21|\u6210\u529f|"
                        r"\u5b8c\u6210|\u5b9e\u73b0|\u53d1\u5e03|\u63a8\u51fa|\u5efa\u6210)",
                        sentence,
                    )
                ):
                    continue
                if event_type == "technical_milestone" and re.search(
                    r"(?:\u6bcf\u5f53|\u6bcf\u6b21).{0,40}\u53d1\u5e03|"
                    r"(?:\u5f53\u7136|\u9700\u8981).{0,80}(?:\u5de5\u4f5c\u6d41|\u5e73\u53f0)",
                    sentence,
                ):
                    continue
                if (
                    event_type == "factory_or_capacity"
                    and re.search(
                        r"(?:\u4e1a\u52a1\u8fb9\u754c.{0,80}\u62d3\u5c55|"
                        r"\u62d3\u5c55\u8def\u5f84|\u573a\u666f\u8fb9\u754c)",
                        sentence,
                    )
                    and not re.search(
                        r"(?:\u5f00\u5de5|\u6295\u4ea7|\u6269\u4ea7|\u6269\u5efa|"
                        r"\u5efa\u8bbe|\u5efa\u6210)",
                        sentence,
                    )
                ):
                    continue
                if event_type == "technical_milestone" and re.search(
                    r"(?:\u4ea4\u4ed8(?:\u4f53\u7cfb|\u65b9\u6848|\u80fd\u529b|\u6d41\u7a0b|\u6a21\u5f0f)|"
                    r"\u91cf\u4ea7(?:\u652f\u6491|\u8fed\u4ee3|\u51c6\u5907)|"
                    r"\u53d1\u5e03\u4e0d\u662f(?:\u7ec8\u70b9|\u7ed3\u675f))",
                    sentence,
                ):
                    continue
                if event_type == "customer_validation" and re.search(
                    r"\u5ba2\u6237\u9a8c\u8bc1(?:\u9636\u6bb5|\u671f|\u4e2d)",
                    sentence,
                ):
                    continue
                if event_type == "customer_validation" and re.search(
                    r"(?:\u5f80\u5f80|\u901a\u5e38|\u5b9e\u8df5\u4e2d|\u6d41\u7a0b).{0,100}"
                    r"(?:\u9a8c\u8bc1|\u91cf\u4ea7\u51b3\u7b56)",
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
                    r"\u4efb\u547d|\u8058\u4efb|\u53d1\u5e03|\u63a8\u51fa|\u53d6\u5f97|\u5efa\u8bbe|\u6269\u5efa)",
                    sentence,
                    maxsplit=1,
                )[0][-40:]
                subject_hint = MiniMaxSemanticProcessor._candidate_subject_hint(subject)
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
                    if subject_hint:
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
                            f"{partner_subject_hint}\0{candidate_quote}".encode("utf-8")
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
                            match.group(0).strip() for match in repeated_assertions
                        )
                    clauses = list(dict.fromkeys(clauses))
                    for clause in clauses:
                        if not re.search(pattern, clause, re.I):
                            continue
                        if re.search(
                            r"(?:\u8fde\u7eed|\u7d2f\u8ba1|\u5171).{0,20}"
                            r"[\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+\u8f6e\u878d\u8d44",
                            clause,
                        ):
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
                                MiniMaxSemanticProcessor._normalize_round(clause_round)
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
        return MiniMaxSemanticProcessor._attach_claim_contract(body, candidates)

    @classmethod
    def _claim_candidates(
        cls,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
    ) -> list[dict[str, Any]]:
        """Return the complete host-owned claim ledger for one model call."""

        unit_start, unit_end = cls._semantic_unit_bounds(article)
        candidates = [
            candidate
            for candidate in cls._event_candidates(article.clean_body)
            if int(candidate.get("char_start", -1)) >= unit_start
            and int(candidate.get("char_end", -1)) <= unit_end
        ]
        known_claim_ids = {
            str(candidate["claim_id"])
            for candidate in candidates
        }
        known_claim_ids.update(
            str(atomic["claim_id"])
            for candidate in candidates
            for atomic in candidate.get("atomic_action_hints") or []
        )
        for event in rule_events:
            if event.claim_ids and set(event.claim_ids).issubset(known_claim_ids):
                continue
            seed_candidate = cls._seed_claim_candidate(article, event)
            if seed_candidate is None:
                continue
            if (
                int(seed_candidate["char_start"]) < unit_start
                or int(seed_candidate["char_end"]) > unit_end
            ):
                continue
            claim_id = str(seed_candidate["claim_id"])
            if claim_id in known_claim_ids:
                continue
            candidates.append(seed_candidate)
            known_claim_ids.add(claim_id)
        for candidate in candidates:
            candidate["required_claim_ids"] = sorted(
                cls._required_claim_ids(candidate)
            )
        return candidates

    @classmethod
    def _seed_claim_candidate(
        cls,
        article: CleanArticle,
        event: SemanticEvent,
    ) -> dict[str, Any] | None:
        if not event.evidence_quotes:
            return None
        quote = event.evidence_quotes[0]
        start = article.clean_body.find(quote)
        if start < 0:
            return None
        end = start + len(quote)
        seed_id = cls._rule_seed_id(event)
        claim_material = f"{seed_id}\0{start}\0{end}\0{quote}".encode("utf-8")
        span_material = f"{start}\0{end}\0{quote}".encode("utf-8")
        claim_id = f"c_seed_{sha1(claim_material).hexdigest()[:12]}"
        span_id = f"s_{sha1(span_material).hexdigest()[:12]}"
        return {
            "id": claim_id,
            "claim_id": claim_id,
            "span_id": span_id,
            "char_start": start,
            "char_end": end,
            "event_type": event.event_type,
            "funding_round": event.funding_round,
            "subject_hint": event.canonical_company,
            "action_hint": event.event_type,
            "event_status_hint": event.event_status,
            "time_hints": [],
            "atomic_action_hints": [],
            "quote": quote,
            "rule_seed_id": seed_id,
        }

    @staticmethod
    def _attach_claim_contract(
        body: str,
        candidates: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Attach immutable source spans and deterministic adjudication hints."""

        output: list[dict[str, Any]] = []
        for candidate in candidates:
            quote = str(candidate.get("quote") or "")
            start = body.find(quote)
            end = start + len(quote) if start >= 0 else -1
            span_material = f"{start}\0{end}\0{quote}".encode("utf-8")
            clause_hints: list[dict[str, str]] = []
            for clause in (
                value.strip()
                for value in re.split(r"(?<=[，,；;。！？])", quote)
                if value.strip()
            ):
                status = MiniMaxSemanticProcessor._event_status_hint(clause)
                funding_round = str(candidate.get("funding_round") or "")
                if funding_round and funding_round not in clause:
                    continue
                if status:
                    atomic_material = f"{candidate['id']}\0{status}\0{clause}".encode(
                        "utf-8"
                    )
                    # A claim span must be independently auditable.  A clause
                    # such as "registered capital ..." or "jointly established"
                    # often omits the subject and date carried by the preceding
                    # clause.  Keep the action focus separately, but cite the
                    # complete immutable candidate span for every atomic action.
                    atomic_start = start
                    atomic_end = end
                    atomic_span_material = (
                        f"{atomic_start}\0{atomic_end}\0{quote}".encode("utf-8")
                    )
                    clause_hints.append(
                        {
                            "claim_id": (
                                f"ac_{sha1(atomic_material).hexdigest()[:12]}"
                            ),
                            "span_id": (
                                f"s_{sha1(atomic_span_material).hexdigest()[:12]}"
                            ),
                            "char_start": atomic_start,
                            "char_end": atomic_end,
                            "text": quote,
                            "action_text": clause,
                            "event_status": status,
                        }
                    )
            statuses = tuple(
                dict.fromkeys(
                    item["event_status"]
                    for item in clause_hints
                    if item["event_status"]
                )
            )
            time_hints = tuple(
                dict.fromkeys(
                    re.findall(
                        r"(?:20\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?|"
                        r"\d{1,2}月\d{1,2}日|近日|日前|近期)",
                        quote,
                    )
                )
            )
            output.append(
                {
                    **candidate,
                    "claim_id": candidate["id"],
                    "span_id": f"s_{sha1(span_material).hexdigest()[:12]}",
                    "char_start": start,
                    "char_end": end,
                    "subject_hint": str(candidate.get("subject_hint") or ""),
                    "action_hint": str(candidate.get("event_type") or ""),
                    "event_status_hint": (
                        statuses[0]
                        if len(statuses) == 1
                        else ("mixed" if statuses else "unknown")
                    ),
                    "time_hints": list(time_hints),
                    "atomic_action_hints": clause_hints,
                }
            )
        return output

    @staticmethod
    def _event_status_hint(text: str) -> str:
        if re.search(
            r"(?:已|正式|成功)?(?:完成|获得|斩获|任命|聘任|出任|担任|"
            r"加入|离任|辞任|升任|接任|发布|推出|首发|签订|签署|"
            r"中标|建成|投产|获批|上市|挂牌|成立|设立|注册(?!资本)|启用)",
            text,
        ):
            return "completed"
        if re.search(
            r"(?:启动|开启|开始|筹集|募集|募资|洽谈|谈判|开工|扩建中|建设中)",
            text,
        ):
            return "started"
        if re.search(r"(?:计划|拟|将|预计|目标|力争)", text):
            return "target"
        return ""

    @staticmethod
    def _candidate_subject_hint(value: str) -> str:
        subject = value.strip(
            " \t\n\u4e5f\u53c8\u5e76\u540c\u65f6\u4e14\u5df2\u6b63\u62df\u5c06"
        )
        subject = re.sub(
            r"(?:\u4e8e)?(?:\d{4}\u5e74)?\d{1,2}\u6708\d{1,2}\u65e5$",
            "",
            subject,
        ).strip()
        if not subject or re.match(
            r"(?:\u56e0|\u672c\u8f6e|\u672c\u6b21|\u539f\u5b9a|\u8be5\u8f6e|\u5176|\u6b64\u524d)",
            subject,
        ):
            return ""
        return subject if is_company_like(subject) else ""

    @classmethod
    def _validated_rejections(
        cls,
        article: CleanArticle,
        payload: dict[str, Any],
        candidates: list[dict[str, str]],
        rule_events: list[SemanticEvent],
        events: list[SemanticEvent],
    ) -> tuple[set[str], set[str]]:
        """Accept only reason-coded rejections proven by source text."""

        raw_rejections = payload.get("rejections", [])
        if not isinstance(raw_rejections, list) or any(
            not isinstance(value, dict) for value in raw_rejections
        ):
            raise SemanticOutputError("rejections must be objects")
        candidate_lookup: dict[str, tuple[dict[str, Any], set[str]]] = {}
        for item in candidates:
            candidate_lookup[str(item["id"])] = (
                item,
                cls._required_claim_ids(item),
            )
            for atomic in item.get("atomic_action_hints") or []:
                atomic_id = str(atomic.get("claim_id") or "")
                if not atomic_id:
                    continue
                candidate_lookup[atomic_id] = (
                    {
                        **item,
                        "quote": str(
                            atomic.get("action_text")
                            or atomic.get("text")
                            or item.get("quote")
                            or ""
                        ),
                        "claim_id": atomic_id,
                        "span_id": str(atomic.get("span_id") or ""),
                        "char_start": atomic.get("char_start"),
                        "char_end": atomic.get("char_end"),
                        "event_status_hint": str(
                            atomic.get("event_status") or "unknown"
                        ),
                    },
                    {atomic_id},
                )
        seed_lookup = {cls._rule_seed_id(item): item for item in rule_events}
        rejected_candidates: set[str] = set()
        rejected_seeds: set[str] = set()
        seen: set[str] = set()
        for rejection in raw_rejections:
            rejection_id = str(rejection.get("id") or "").strip()
            reason_code = str(rejection.get("reason_code") or "").strip()
            if not rejection_id or rejection_id in seen:
                raise SemanticOutputError("duplicate or empty rejection ID")
            seen.add(rejection_id)
            candidate_record = candidate_lookup.get(rejection_id)
            candidate = candidate_record[0] if candidate_record else None
            rejected_claims = candidate_record[1] if candidate_record else set()
            seed = seed_lookup.get(rejection_id)
            if candidate is None and seed is None:
                raise SemanticOutputError("unknown rejected ID")
            if candidate is not None:
                if any(
                    rejected_claims & set(event.claim_ids)
                    for event in events
                ) or (
                    rejection_id == str(candidate.get("id") or "")
                    and cls._candidate_is_covered(candidate, events)
                ):
                    continue
            if seed is not None and cls._seed_is_adjudicated(
                seed,
                events,
                rule_events,
            ):
                continue
            if not cls._rejection_reason_grounded(
                article,
                reason_code,
                candidate=candidate,
                seed=seed,
                events=events,
            ):
                raise SemanticOutputError(
                    f"unsupported rejection reason: {rejection_id}:{reason_code}"
                )
            if candidate is not None:
                rejected_candidates.add(rejection_id)
            else:
                rejected_seeds.add(rejection_id)
        return rejected_candidates, rejected_seeds

    @classmethod
    def _validated_rejections_parts(
        cls,
        article: CleanArticle,
        payload: dict[str, Any],
        candidates: list[dict[str, str]],
        rule_events: list[SemanticEvent],
        events: list[SemanticEvent],
    ) -> tuple[set[str], set[str], list[str]]:
        """Validate each rejection independently and keep only grounded ones."""

        raw_rejections = payload.get("rejections", [])
        if not isinstance(raw_rejections, list):
            return set(), set(), ["rejections:SemanticOutputError:not a list"]
        rejected_candidates: set[str] = set()
        rejected_seeds: set[str] = set()
        issues: list[str] = []
        seen: set[str] = set()
        for position, rejection in enumerate(raw_rejections):
            rejection_id = (
                str(rejection.get("id") or "").strip()
                if isinstance(rejection, dict)
                else ""
            )
            if not rejection_id or rejection_id in seen:
                issues.append(
                    f"rejection[{position}]:SemanticOutputError:"
                    "duplicate or empty rejection ID"
                )
                continue
            seen.add(rejection_id)
            try:
                chunk_candidates, chunk_seeds = cls._validated_rejections(
                    article,
                    {"rejections": [rejection]},
                    candidates,
                    rule_events,
                    events,
                )
            except Exception as error:
                issues.append(f"rejection[{position}]:{type(error).__name__}:{error}")
                continue
            rejected_candidates.update(chunk_candidates)
            rejected_seeds.update(chunk_seeds)
        return rejected_candidates, rejected_seeds, issues

    @classmethod
    def _rejection_reason_grounded(
        cls,
        article: CleanArticle,
        reason_code: str,
        *,
        candidate: dict[str, str] | None,
        seed: SemanticEvent | None,
        events: list[SemanticEvent],
    ) -> bool:
        quote = (
            str(candidate.get("quote") or "")
            if candidate is not None
            else (seed.evidence_quotes[0] if seed and seed.evidence_quotes else "")
        )
        event_type = (
            str(candidate.get("event_type") or "")
            if candidate is not None
            else (seed.event_type if seed else "")
        )
        if reason_code == "funding_use_or_plan":
            return cls._funding_use_only_nonfunding(event_type, quote)
        if reason_code == "historical_or_reference":
            return cls._is_historical_event_quote(
                quote,
                article.index.published_at,
            )
        if reason_code == "generic_commentary":
            generic = bool(
                re.search(
                    r"(?:\u884c\u4e1a|\u8f66\u4f01|\u4f01\u4e1a|\u5382\u5546|"
                    r"\u521b\u4e1a\u516c\u53f8|\u5e02\u573a|\u4e1a\u754c|\u901a\u5e38|\u5f80\u5f80)",
                    quote,
                )
            )
            subject_hint = str((candidate or {}).get("subject_hint") or "")
            generic_seed = bool(
                seed
                and re.fullmatch(
                    r".*(?:\u884c\u4e1a|\u8f66\u4f01|\u4f01\u4e1a|\u5382\u5546|\u5e02\u573a).*",
                    seed.canonical_company,
                )
            )
            return generic and (not subject_hint or generic_seed)
        if reason_code == "capability_description":
            capability = bool(
                re.search(
                    r"(?:\u5177\u5907|\u62e5\u6709|\u5f62\u6210|\u662f|\u4f5c\u4e3a)"
                    r".{0,120}(?:\u80fd\u529b|\u4f18\u52bf|\u4f53\u7cfb)|"
                    r"(?:\u5168\u6d41\u7a0b|\u7cfb\u7edf\u6027).{0,20}\u80fd\u529b",
                    quote,
                )
            )
            concrete_change = bool(
                re.search(
                    r"(?:\u8fd1\u65e5|\u8fd1\u671f|\u6b63\u5f0f|\u9996\u6b21|\u6210\u529f|"
                    r"\u5b8c\u6210|\u5b9e\u73b0|\u53d1\u5e03|\u63a8\u51fa|\u5efa\u6210)",
                    quote,
                )
            )
            return event_type != "funding" and capability and not concrete_change
        if reason_code == "invalid_subject":
            if seed is not None:
                company = seed.canonical_company
                return (
                    not is_company_like(company)
                    or bool(
                        re.search(r"(?:\u4e0e|\u548c).{2,80}(?:\u4e0e|\u548c)", company)
                    )
                    or bool(
                        re.search(
                            r"(?:\u59d4\u5458\u4f1a|\u7814\u7a76\u9662|\u653f\u5e9c|"
                            r"\u534f\u4f1a|\u79c1\u52df\u57fa\u91d1|\u6295\u8d44\u57fa\u91d1)",
                            company,
                        )
                    )
                )
            candidate_subject = str((candidate or {}).get("subject_hint") or "")
            public_body = bool(
                re.search(
                    r"(?:\u59d4\u5458\u4f1a|\u7814\u7a76\u9662|\u653f\u5e9c|\u534f\u4f1a)",
                    quote,
                )
            )
            return public_body and (
                not candidate_subject
                or bool(re.search(r"(?:\u4e0e|\u548c)", candidate_subject))
            )
        if reason_code == "duplicate_summary":
            if event_type != "funding" or not re.search(
                r"(?:\u8fde\u7eed|\u7d2f\u8ba1|\u5171).{0,20}[\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+\u8f6e\u878d\u8d44",
                quote,
            ):
                return False
            return (
                len([event for event in events if event.event_type == "funding"]) >= 2
            )
        return False

    @classmethod
    def _assert_chunk_adjudicated(
        cls,
        events: list[SemanticEvent],
        rule_events: list[SemanticEvent],
        candidates: list[dict[str, str]],
        *,
        rejected_candidate_ids: set[str] | None = None,
        rejected_seed_ids: set[str] | None = None,
    ) -> None:
        rejected_candidate_ids = rejected_candidate_ids or set()
        rejected_seed_ids = rejected_seed_ids or set()
        missing_candidates = [
            candidate["id"]
            for candidate in candidates
            if candidate["id"] not in rejected_candidate_ids
            and not cls._candidate_is_covered(candidate, events)
        ]
        missing_seeds = [
            cls._rule_seed_id(seed)
            for seed in rule_events
            if cls._rule_seed_id(seed) not in rejected_seed_ids
            and not cls._seed_is_adjudicated(seed, events, rule_events)
        ]
        if missing_candidates or missing_seeds:
            raise SemanticOutputError(
                "unadjudicated semantic candidates: "
                f"candidates={missing_candidates}, seeds={missing_seeds}"
            )

    @classmethod
    def _remove_rejection_conflicts(
        cls,
        events: list[SemanticEvent],
        candidates: list[dict[str, str]],
        rejected_candidate_ids: set[str],
        rejected_seed_ids: set[str],
    ) -> tuple[list[SemanticEvent], int]:
        """Apply deterministic rejections globally after chunk fan-in."""

        rejected_candidates = [
            item
            for item in candidates
            if item["id"] in rejected_candidate_ids
        ]
        rejected_claim_ids = cls._expanded_rejected_claim_ids(
            candidates,
            rejected_candidate_ids,
        )
        conflicts = [
            event
            for event in events
            if bool(set(event.claim_ids) & rejected_claim_ids)
            or any(
                cls._candidate_is_covered(candidate, [event])
                for candidate in rejected_candidates
            )
            or (
                event.processor.startswith("rules")
                and cls._rule_seed_id(event) in rejected_seed_ids
            )
        ]
        conflict_object_ids = {id(event) for event in conflicts}
        return (
            [event for event in events if id(event) not in conflict_object_ids],
            len(conflicts),
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
                or cls._funding_round_is_covered(
                    str(candidate["funding_round"]),
                    event.funding_round,
                )
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
    def _funding_round_is_covered(
        cls,
        candidate_round: str,
        event_round: str,
    ) -> bool:
        """Match one ledger round against an exact or composite model round."""

        candidate = cls._normalize_round(candidate_round)
        event = cls._normalize_round(event_round)
        if not candidate or candidate == event:
            return True
        if not event:
            return False
        compact = re.sub(r"\s+", "", event)
        # Chinese financing reports sometimes collapse adjacent rounds into
        # forms such as "天使+/++轮". Expand that notation before tokenizing.
        compact = re.sub(
            r"(天使|种子)(\+{1,2})/(\+{1,2})轮",
            lambda match: (
                f"{match.group(1)}{match.group(2)}轮、"
                f"{match.group(1)}{match.group(3)}轮"
            ),
            compact,
        )
        tokens = re.findall(
            r"Pre-IPO(?:轮)?|"
            r"Pre-?[A-H](?:\+{1,2})?(?:轮)?|"
            r"[A-H](?:\+{1,2})?轮|"
            r"天使(?:\+{1,2})?轮|种子(?:\+{1,2})?轮|"
            r"战略(?:轮|融资)",
            compact,
            re.I,
        )
        return candidate in {cls._normalize_round(token) for token in tokens}

    @classmethod
    def _seed_is_adjudicated(
        cls,
        seed: SemanticEvent,
        events: list[SemanticEvent],
        rule_events: list[SemanticEvent],
    ) -> bool:
        for event in events:
            if (
                cls._matching_seed(
                    [seed],
                    event.canonical_company,
                    event.event_type,
                    event.funding_round,
                    event.event_status,
                )
                is not None
            ):
                return True
            if event.event_type != seed.event_type:
                continue
            same_company = cls._canonical_company(
                seed.canonical_company
            ) == cls._canonical_company(event.canonical_company)
            same_round = cls._normalize_round(
                seed.funding_round
            ) == cls._normalize_round(event.funding_round)
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
        return False

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
            "prompt_version": self.semantic_prompt_version,
            "model_identity": self.model_identity,
            "cache_key": self.cache_key,
            "claim_contract_version": self.semantic_claim_contract_version,
            "claim_centric_v27": self.claim_centric_v27 and self.runner is not None,
            "strict_claim_contract": self.strict_claim_contract,
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
            candidates.append((score, -len(identity_quote), identity_quote[:500]))
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
        rejected_candidate_ids: set[str] | None = None,
        rejected_seed_ids: set[str] | None = None,
    ) -> None:
        audit["final_event_count"] = len(events)
        audit["rules_preserved_count"] = sum(
            event.processor.startswith("rules") for event in events
        )
        candidates = candidates or []
        model_ambiguities = model_ambiguities or []
        rejected_candidate_ids = rejected_candidate_ids or set()
        rejected_seed_ids = rejected_seed_ids or set()
        def candidate_is_semantically_covered(candidate: dict[str, Any]) -> bool:
            if MiniMaxSemanticProcessor._candidate_is_covered(candidate, events):
                return True
            rule_seed_id = str(candidate.get("rule_seed_id") or "")
            seed = next(
                (
                    item
                    for item in rule_events
                    if MiniMaxSemanticProcessor._rule_seed_id(item) == rule_seed_id
                ),
                None,
            )
            return seed is not None and MiniMaxSemanticProcessor._seed_is_adjudicated(
                seed,
                events,
                rule_events,
            )

        unmapped: list[str] = []
        for candidate in candidates:
            candidate_id = candidate["id"]
            covered = candidate_is_semantically_covered(candidate)
            rejected_claim_ids = MiniMaxSemanticProcessor._expanded_rejected_claim_ids(
                candidates,
                rejected_candidate_ids,
            )
            if (
                not covered
                and candidate_id not in rejected_candidate_ids
                and not MiniMaxSemanticProcessor._required_claim_ids(
                    candidate
                ).issubset(rejected_claim_ids)
            ):
                unmapped.append(candidate_id)
        audit["rejected_candidate_count"] = len(rejected_candidate_ids)
        audit["rejected_candidate_ids"] = sorted(rejected_candidate_ids)
        audit["explicitly_rejected_seed_count"] = len(rejected_seed_ids)
        audit["explicitly_rejected_seed_ids"] = sorted(rejected_seed_ids)
        audit["unmapped_candidate_count"] = len(unmapped)
        audit["unmapped_candidate_ids"] = unmapped
        audit["omissions_detected"] = len(unmapped)
        accepted_candidate_ids = [
            candidate["id"]
            for candidate in candidates
            if candidate_is_semantically_covered(candidate)
        ]
        rejected_only_ids = sorted(
            rejected_candidate_ids - set(accepted_candidate_ids)
        )
        disposition_ids = (
            set(accepted_candidate_ids) | set(rejected_only_ids) | set(unmapped)
        )
        audit["accepted_candidate_ids"] = accepted_candidate_ids
        audit["ambiguous_candidate_ids"] = []
        audit["failed_candidate_ids"] = unmapped
        audit["candidate_disposition_complete"] = (
            len(disposition_ids) == len(candidates)
            and not (set(accepted_candidate_ids) & set(rejected_only_ids))
            and not (set(accepted_candidate_ids) & set(unmapped))
            and not (set(rejected_only_ids) & set(unmapped))
        )
        exact_seed_ids = {
            MiniMaxSemanticProcessor._rule_seed_id(seed)
            for seed in rule_events
            if MiniMaxSemanticProcessor._rule_seed_id(seed) not in rejected_seed_ids
            and any(
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
        }
        corrected_seed_ids = {
            MiniMaxSemanticProcessor._rule_seed_id(seed)
            for seed in rule_events
            if MiniMaxSemanticProcessor._rule_seed_id(seed)
            not in rejected_seed_ids | exact_seed_ids
            and MiniMaxSemanticProcessor._seed_is_adjudicated(
                seed,
                events,
                rule_events,
            )
        }
        seed_bound_event_count = sum(
            any(
                seed.event_type == event.event_type
                and MiniMaxSemanticProcessor._quotes_overlap(
                    seed.evidence_quotes,
                    event.evidence_quotes,
                )
                for seed in rule_events
                if MiniMaxSemanticProcessor._rule_seed_id(seed) not in rejected_seed_ids
            )
            for event in events
        )
        audit["model_only_count"] = max(0, len(events) - seed_bound_event_count)
        audit["rejected_seed_count"] = len(rejected_seed_ids)
        audit["corrected_seed_count"] = len(corrected_seed_ids)

    @staticmethod
    def _prompt(
        channel: SourceChannel,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
    ) -> str:
        route = route_document(article)
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
                    "claim_ids": ["c_example"],
                    "span_ids": ["s_example"],
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
            "rejections": [],
            "ambiguities": [],
        }
        payload = {
            "source_prior": list(channel.event_prior),
            "document_type": route.document_type,
            "document_family": route.document_family,
            "processing_mode": route.processing_mode,
            "route_gate_confidence": route.gate_confidence,
            "route_llm_gate_required": route.llm_gate_required,
            "route_gate_signals": list(route.gate_signals),
            "title": article.index.title,
            "headline_is_clue_only": True,
            "published_at": article.index.published_at,
            "structured": {
                key: value
                for key, value in article.structured_data.items()
                if key != "_semantic_unit"
            },
            "rule_seed": seed,
            "claim_contract_version": CLAIM_CONTRACT_VERSION,
            "candidate_ledger": MiniMaxSemanticProcessor._flat_claim_ledger(
                MiniMaxSemanticProcessor._claim_candidates(article, rule_events)
            ),
            "article": clean_semantic_body_scope(
                MiniMaxSemanticProcessor._semantic_unit_text(article)
            )[:24000],
            "historical_counterexample": {
                "input": (
                    "\u672c\u65e5\u8bbf\u8c08\u56de\u987e\u4e0a\u6708"
                    "\u5df2\u5b8c\u6210\u7684\u878d\u8d44"
                ),
                "expected_output": {
                    "events": [],
                    "rejections": [],
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
    def _claim_retry_prompt(
        article: CleanArticle,
        candidates: list[dict[str, Any]],
        accepted_events: list[SemanticEvent],
    ) -> str:
        failed_claim_ids = {
            str(claim_id)
            for candidate in candidates
            for claim_id in candidate.get("required_claim_ids") or []
            if str(claim_id)
        }
        claims = MiniMaxSemanticProcessor._flat_claim_ledger(
            candidates,
            only_claim_ids=failed_claim_ids,
        )
        spans = {
            str(claim["span_id"]): {
                "span_id": str(claim["span_id"]),
                "char_start": claim["char_start"],
                "char_end": claim["char_end"],
                "text": claim["text"],
            }
            for claim in claims
        }
        payload = {
            "task": "adjudicate_failed_claims_only",
            "published_at": article.index.published_at,
            "failed_claims": claims,
            "immutable_source_spans": list(spans.values()),
            "already_accepted_event_keys": [
                {
                    "company": event.canonical_company,
                    "event_type": event.event_type,
                    "funding_round": event.funding_round,
                    "event_status": event.event_status,
                }
                for event in accepted_events
            ],
            "constraints": [
                "Only adjudicate failed_claims; do not repeat accepted events.",
                "Adjudicate every failed claim ID exactly once as an event or rejection.",
                "Every event must cite claim_ids and matching span_ids.",
                "Use confidence only as high, medium, low, or unknown; never a number.",
                "A rejection ID must be the exact failed claim ID, without suffixes.",
                "The host restores evidence from immutable_source_spans.",
                "Return one JSON object with events, rejections, ambiguities.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    def _retry_unmapped_claims(
        self,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
        candidates: list[dict[str, Any]],
        events: list[SemanticEvent],
        failed_claim_ids: list[str],
    ) -> dict[str, Any]:
        failed_claim_set = set(failed_claim_ids)
        failed: list[dict[str, Any]] = []
        for candidate in candidates:
            required = self._required_claim_ids(candidate)
            unresolved = required & failed_claim_set
            if not unresolved:
                continue
            retry_candidate = dict(candidate)
            retry_candidate["required_claim_ids"] = sorted(unresolved)
            if candidate.get("atomic_action_hints"):
                retry_candidate["atomic_action_hints"] = [
                    atomic
                    for atomic in candidate["atomic_action_hints"]
                    if atomic.get("claim_id") in unresolved
                ]
            failed.append(retry_candidate)
        deterministic_rejected = self._deterministic_historical_rejections(
            article,
            failed,
            failed_claim_set,
        )
        failed_claim_set -= deterministic_rejected
        if deterministic_rejected:
            remaining: list[dict[str, Any]] = []
            for candidate in failed:
                unresolved = self._required_claim_ids(candidate) & failed_claim_set
                if not unresolved:
                    continue
                retained = dict(candidate)
                retained["required_claim_ids"] = sorted(unresolved)
                if candidate.get("atomic_action_hints"):
                    retained["atomic_action_hints"] = [
                        atomic
                        for atomic in candidate["atomic_action_hints"]
                        if atomic.get("claim_id") in unresolved
                    ]
                remaining.append(retained)
            failed = remaining
        if self.runner is None or not failed:
            return {
                "attempted": bool(deterministic_rejected),
                "events": events,
                "response": "",
                "error": "",
                "validation_issues": [],
                "rejection_issues": [],
                "rejected_candidate_ids": deterministic_rejected,
                "rejected_seed_ids": set(),
                "deterministic_rejected_claim_ids": deterministic_rejected,
                "rejection_conflict_removed_count": 0,
                "ambiguities": [
                    f"host_historical_context_rejected:{claim_id}"
                    for claim_id in sorted(deterministic_rejected)
                ],
                "claim_contract_observation": {},
            }
        failed_rules = [
            seed
            for seed in rule_events
            if any(
                self._quotes_overlap(seed.evidence_quotes, (candidate["quote"],))
                for candidate in failed
            )
        ]
        prompt = self._claim_retry_prompt(article, failed, events)
        response = ""
        try:
            response = self.runner.run(
                prompt,
                session_id=(
                    f"aggregate-claim-retry:{article.index.source_id}:"
                    f"{article.index.source_article_id}:"
                    f"{article.content_hash[:12]}"
                ),
                system_prompt=SYSTEM_PROMPT,
            )
            payload = self._parse_json(response, allow_syntax_repair=True)
            retry_events, validation_issues = self._validate_payload_parts(
                article,
                payload,
                failed_rules,
            )
            retry_events, strict_issues = self._enforce_claim_contract(
                retry_events,
                failed,
            )
            validation_issues.extend(strict_issues)
            retry_events = [
                event
                for event in retry_events
                if (
                    bool(set(event.claim_ids) & failed_claim_set)
                    if self.strict_claim_contract
                    else any(
                        self._candidate_is_covered(candidate, [event])
                        for candidate in failed
                    )
                )
            ]
            combined = self._normalize_final_events([*events, *retry_events])
            (
                rejected_candidates,
                rejected_seeds,
                rejection_issues,
            ) = self._validated_rejections_parts(
                article,
                payload,
                failed,
                failed_rules,
                combined,
            )
            rejected_candidates.update(deterministic_rejected)
            combined = [
                event
                for event in combined
                if not (
                    event.processor.startswith("rules")
                    and self._rule_seed_id(event) in rejected_seeds
                )
            ]
            combined = self._salvage_grounded_investors(
                article,
                combined,
                (response,),
                "",
            )
            combined = self._normalize_final_events(combined)
            combined, conflict_count = self._remove_rejection_conflicts(
                combined,
                candidates,
                rejected_candidates,
                rejected_seeds,
            )
            combined, final_contract_issues = self._enforce_claim_contract(
                combined,
                candidates,
            )
            validation_issues.extend(final_contract_issues)
            return {
                "attempted": True,
                "events": combined,
                "response": response,
                "error": "",
                "validation_issues": validation_issues,
                "rejection_issues": rejection_issues,
                "rejected_candidate_ids": (
                    rejected_candidates | deterministic_rejected
                ),
                "rejected_seed_ids": rejected_seeds,
                "rejection_conflict_removed_count": conflict_count,
                "deterministic_rejected_claim_ids": deterministic_rejected,
                "ambiguities": [
                    value
                    for value in payload.get("ambiguities", [])
                    if isinstance(value, str)
                ],
                "claim_contract_observation": self._claim_contract_observation(
                    payload,
                    failed,
                ),
            }
        except Exception as error:
            return {
                "attempted": True,
                "events": events,
                "response": response,
                "error": f"{type(error).__name__}: {error}",
                "validation_issues": [],
                "rejection_issues": [],
                "rejected_candidate_ids": deterministic_rejected,
                "rejected_seed_ids": set(),
                "rejection_conflict_removed_count": 0,
                "ambiguities": [],
                "claim_contract_observation": {},
                "deterministic_rejected_claim_ids": deterministic_rejected,
            }

    @classmethod
    def _deterministic_historical_rejections(
        cls,
        article: CleanArticle,
        candidates: list[dict[str, Any]],
        failed_claim_ids: set[str],
    ) -> set[str]:
        """Reject only claims whose own or bounded section date is historical.

        Multi-company monthly roundups often introduce an entity with an old
        establishment date and then split ownership/registration attributes
        into later clauses.  Those later clauses are not new events merely
        because they omit the repeated date.  The bounded document unit is used
        only when the action has no explicit current marker.
        """

        units = route_document(article).units
        output: set[str] = set()
        current_marker = re.compile(
            r"(?:\u8fd1\u65e5|\u8fd1\u671f|\u540c\u65e5|\u73b0\u5df2|\u6b63\u5f0f|"
            r"\u4eca\u65e5|\u6628\u65e5|\u672c\u6708|\u5f53\u6708)"
        )
        for candidate in candidates:
            atomic_by_id = {
                str(item.get("claim_id") or ""): item
                for item in candidate.get("atomic_action_hints") or []
            }
            start = int(candidate.get("char_start", -1))
            end = int(candidate.get("char_end", -1))
            unit = next(
                (
                    item
                    for item in units
                    if item.char_start <= start and end <= item.char_end
                ),
                None,
            )
            context = ""
            if unit is not None:
                context = article.clean_body[unit.char_start:end]
            for claim_id in cls._required_claim_ids(candidate) & failed_claim_ids:
                atomic = atomic_by_id.get(claim_id, {})
                full_text = str(
                    atomic.get("text") or candidate.get("quote") or ""
                )
                action_text = str(atomic.get("action_text") or full_text)
                own_history = cls._is_historical_event_quote(
                    full_text,
                    article.index.published_at,
                )
                inherited_history = bool(
                    context
                    and not current_marker.search(action_text)
                    and cls._is_historical_event_quote(
                        context,
                        article.index.published_at,
                    )
                )
                if own_history or inherited_history:
                    output.add(claim_id)
        return output

    @staticmethod
    def _parse_json(
        value: str,
        *,
        allow_syntax_repair: bool = False,
    ) -> dict[str, Any]:
        # Provider output should be a small JSON object. Bound the input before
        # parsing so malformed/verbose replies cannot tie up a worker in regex
        # backtracking or consume unbounded memory.
        if len(value) > MAX_SEMANTIC_RESPONSE_CHARS:
            raise SemanticOutputError(
                "semantic output exceeds the maximum response size"
            )

        candidate = value.strip()
        fence_start = candidate.find("```")
        if fence_start >= 0:
            content_start = fence_start + 3
            if candidate[content_start : content_start + 4].lower() == "json":
                content_start += 4
            while content_start < len(candidate) and candidate[content_start] in " \t\r\n":
                content_start += 1
            fence_end = candidate.find("```", content_start)
            if fence_end >= 0:
                fenced = candidate[content_start:fence_end].strip()
                if fenced.startswith("{") and fenced.endswith("}"):
                    candidate = fenced
        if not candidate.startswith("{") or not candidate.endswith("}"):
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
            if len(candidate) > MAX_SEMANTIC_REPAIR_CHARS:
                raise SemanticOutputError(
                    "semantic output exceeds the syntax repair size limit"
                ) from error
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
        identity_text = (
            f"{article.index.title}\n{article.index.summary}\n"
            f"{clean_semantic_body_scope(article.clean_body)}"
        )
        evidence_text = (
            f"{article.index.summary}\n{clean_semantic_body_scope(article.clean_body)}"
        )
        candidates = cls._claim_candidates(article, rule_events)
        claim_lookup: dict[str, dict[str, Any]] = {}
        valid_span_ids: set[str] = set()
        for candidate in candidates:
            claim_lookup[str(candidate["claim_id"])] = candidate
            valid_span_ids.add(str(candidate["span_id"]))
            for atomic in candidate.get("atomic_action_hints") or []:
                valid_span_ids.add(str(atomic["span_id"]))
                claim_lookup[str(atomic["claim_id"])] = {
                    **candidate,
                    "claim_id": atomic["claim_id"],
                    "span_id": atomic["span_id"],
                    "char_start": atomic["char_start"],
                    "char_end": atomic["char_end"],
                    "event_status_hint": atomic["event_status"],
                }

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
            if (
                not company
                or company not in identity_text
                or not is_company_like(company)
            ):
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
            raw_claim_ids = raw.get("claim_ids") or []
            raw_span_ids = raw.get("span_ids") or []
            citation_pair_normalized = False
            if not isinstance(raw_claim_ids, list) or not isinstance(
                raw_span_ids,
                list,
            ):
                raise SemanticOutputError("claim_ids/span_ids must be lists")
            if any(not isinstance(value, str) for value in raw_claim_ids) or any(
                not isinstance(value, str) for value in raw_span_ids
            ):
                raise SemanticOutputError("claim_ids/span_ids must be strings")
            if bool(raw_claim_ids) != bool(raw_span_ids):
                raise SemanticOutputError("claim_ids/span_ids must be cited together")
            cited_candidates: list[dict[str, Any]] = []
            if raw_claim_ids:
                try:
                    cited_candidates = [claim_lookup[value] for value in raw_claim_ids]
                except KeyError as error:
                    raise SemanticOutputError("unknown claim_id/span_id") from error
                expected_span_ids = {
                    str(candidate["span_id"]) for candidate in cited_candidates
                }
                if (
                    expected_span_ids != set(raw_span_ids)
                    or not set(raw_span_ids).issubset(valid_span_ids)
                    or len(raw_span_ids) != len(set(raw_span_ids))
                ):
                    provided_span_ids = set(raw_span_ids)
                    if (
                        expected_span_ids
                        and provided_span_ids
                        and provided_span_ids.issubset(valid_span_ids)
                    ):
                        raw_span_ids = list(
                            dict.fromkeys(
                                str(candidate["span_id"])
                                for candidate in cited_candidates
                            )
                        )
                        citation_pair_normalized = True
                    else:
                        raise SemanticOutputError("claim/span citation mismatch")
            raw_quotes = raw.get("evidence_quotes")
            if not cited_candidates and (
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
            if cited_candidates:
                for candidate in cited_candidates:
                    start = int(candidate["char_start"])
                    end = int(candidate["char_end"])
                    quote = article.clean_body[start:end] if start >= 0 else ""
                    if quote:
                        quotes.append(quote)
            else:
                for position, raw_quote in enumerate(raw_quotes):
                    quote = cls._ground_quote(
                        evidence_text,
                        str(raw_quote).strip(),
                    )
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
            historical_evidence_quotes = list(quotes)
            if preliminary_seed is None:
                preliminary_seed = cls._correction_seed(
                    rule_events,
                    event_type,
                    round_name,
                    event_status,
                    quotes,
                )
            defined_alias = cls._defined_alias_for_company(identity_text, company)
            grounding_company = company
            if (
                defined_alias
                and not cls._company_subject_grounded(company, quotes[0])
                and cls._company_subject_grounded(defined_alias, quotes[0])
            ):
                grounding_company = defined_alias
            if not cls._company_subject_grounded(grounding_company, quotes[0]):
                expanded_primary = cls._expand_pronominal_subject_context(
                    evidence_text,
                    quotes[0],
                    grounding_company,
                )
                if expanded_primary:
                    quotes[0] = expanded_primary
            quote_text = "\n".join(quotes)
            if not cls._company_subject_grounded(grounding_company, quotes[0]):
                subject_mismatch_count += 1
                continue
            if cls._funding_use_only_nonfunding(event_type, quote_text):
                continue
            if cls._event_evidence_is_historical(
                evidence_text,
                [str(quote).strip() for quote in historical_evidence_quotes],
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
                    funding_fields[field] = ""
                else:
                    seed_value = str(getattr(preliminary_seed, field))
                    funding_fields[field] = (
                        seed_value if seed_value in quote_text else ""
                    )
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
            raw_confidence = raw.get("confidence")
            confidence_normalized = False
            if isinstance(raw_confidence, (int, float)) and not isinstance(
                raw_confidence, bool
            ):
                score = float(raw_confidence)
                if 1 < score <= 100:
                    score /= 100
                if not 0 <= score <= 1:
                    raise SemanticOutputError("invalid confidence")
                confidence = "high" if score >= 0.8 else (
                    "medium" if score >= 0.5 else "low"
                )
                confidence_normalized = True
            else:
                confidence = str(raw_confidence or "unknown").strip()
            if confidence not in ALLOWED_CONFIDENCE:
                raise SemanticOutputError("invalid confidence")
            raw_ambiguities = list(ambiguities)
            if citation_pair_normalized:
                raw_ambiguities.append("minimax_redundant_span_ids_removed")
            if confidence_normalized:
                raw_ambiguities.append("minimax_numeric_confidence_normalized")
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
            is_company_correction = seed is not None and cls._canonical_company(
                seed.canonical_company
            ) != cls._canonical_company(company)
            if seed is None and not cls._company_event_subject_grounded(
                grounding_company,
                quotes[0],
                event_type,
            ):
                subject_mismatch_count += 1
                continue
            if is_company_correction and not cls._company_event_subject_grounded(
                grounding_company,
                quotes[0],
                event_type,
            ):
                subject_mismatch_count += 1
                continue
            if cited_candidates and not cls._company_event_subject_grounded(
                grounding_company,
                quotes[0],
                event_type,
            ):
                subject_mismatch_count += 1
                continue
            if is_company_correction:
                raw_ambiguities.append(
                    f"minimax_corrected_rule_company:{seed.canonical_company}"
                )
            hinted_types = {
                str(candidate.get("action_hint") or "")
                for candidate in cited_candidates
                if str(candidate.get("action_hint") or "")
            }
            if hinted_types and event_type not in hinted_types:
                raw_ambiguities.append(
                    "minimax_corrected_claim_type:"
                    f"{','.join(sorted(hinted_types))}->{event_type}"
                )
            hinted_statuses = {
                str(candidate.get("event_status_hint") or "")
                for candidate in cited_candidates
                if str(candidate.get("event_status_hint") or "")
                not in {"", "unknown", "mixed"}
            }
            if hinted_statuses and event_status not in hinted_statuses:
                raw_ambiguities.append(
                    "minimax_corrected_claim_status:"
                    f"{','.join(sorted(hinted_statuses))}->{event_status}"
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
                    claim_ids=tuple(dict.fromkeys(raw_claim_ids)),
                    span_ids=tuple(dict.fromkeys(raw_span_ids)),
                )
            )
        if subject_mismatch_count and not model_events:
            raise SemanticOutputError(
                "all semantic events failed primary-subject grounding"
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
            r"(?:\u672c(?:\u6b21|\u8f6e).{0,20}\u8d44\u91d1|"
            r"\u672c\u6b21\u52df\u8d44|\u52df\u96c6\u8d44\u91d1|\u878d\u8d44\u6240\u5f97|"
            r"\u8d44\u91d1).{0,30}(?:\u5c06|\u62df|\u8ba1\u5212)?"
            r"(?:\u7528\u4e8e|\u6295\u5165|\u6295\u5411)",
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
            # Official policy adapters may supply a body sub-clause as their
            # first rule seed (for example the future list-publication step in
            # an MIIT notice). The notice title is the authoritative current
            # event and remains stable across body wording changes, so promote
            # it before grounding and claim lookup. This also prevents a
            # future sub-step from replacing the main notice in the persisted
            # event.
            if (
                event.event_type == "policy_or_standard"
                and article.index.title
                and re.search(r"(?:通知|公告)$", article.index.title)
            ):
                event = replace(
                    event,
                    event_summary=article.index.title[:500],
                    evidence_quotes=(article.index.title,),
                )
            quotes = [quote for quote in event.evidence_quotes if quote]
            if not quotes:
                continue
            primary = cls._ground_rule_primary(article, event, quotes[0])
            if not primary:
                continue
            mentions = tuple(
                dict.fromkeys(
                    (
                        event.canonical_company,
                        *event.company_mentions,
                    )
                )
            )
            if (
                event.event_type != "policy_or_standard"
                and not is_company_like(event.canonical_company)
            ):
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
                [primary],
                article.index.published_at,
            ):
                continue
            grounded_event = replace(
                event,
                event_summary=primary[:500],
                evidence_quotes=(primary,),
            )
            candidate = next(
                (
                    item
                    for item in cls._event_candidates(article.clean_body)
                    if cls._candidate_is_covered(item, [grounded_event])
                ),
                None,
            )
            if candidate is None:
                if event.event_type == "policy_or_standard":
                    # Official notices commonly carry the decisive action in
                    # the title while the body starts with a recipient list.
                    # Keep the title-grounded rule seed even when no company
                    # action span can be built for the non-operating issuer.
                    output.append(
                        replace(
                            grounded_event,
                            claim_ids=(),
                            span_ids=(),
                        )
                    )
                    continue
                candidate = cls._seed_claim_candidate(article, grounded_event)
            if candidate is None:
                continue
            output.append(
                replace(
                    grounded_event,
                    claim_ids=(str(candidate["claim_id"]),),
                    span_ids=(str(candidate["span_id"]),),
                )
            )
        return output

    @classmethod
    def _ground_rule_primary(
        cls,
        article: CleanArticle,
        event: SemanticEvent,
        primary: str,
    ) -> str:
        if (
            event.event_type == "policy_or_standard"
            and primary == article.index.title
            and primary
        ):
            # A policy notice's title is the stable, source-owned event label;
            # do not replace it with a later implementation sub-clause.
            return primary
        # Adapter evidence often concatenates the page title with the first
        # body paragraph.  Prefer a span that actually occurs in the body so
        # the restored rule seed can receive a valid claim/span pair.  The
        # previous title-first shortcut caused otherwise valid CLS seeds to be
        # discarded when the title was not reproduced verbatim in the body.
        body = clean_semantic_body_scope(article.clean_body)
        if primary in body:
            return primary
        body_grounded = cls._ground_quote(body, primary)
        if body_grounded:
            return body_grounded
        evidence_text = (
            f"{article.index.title}\n{article.index.summary}\n"
            f"{body}"
        )
        grounded = cls._ground_quote(evidence_text, primary)
        if grounded:
            # A title/summary-only match is not sufficient for a claim span;
            # continue looking for the corresponding body sentence below.
            if grounded in body:
                return grounded
        if body and primary.endswith(body):
            return body
        if body:
            for prefix_length in range(min(80, len(body)), 19, -1):
                prefix = body[:prefix_length]
                prefix_position = primary.find(prefix)
                if prefix_position < 0:
                    continue
                body_fragment = primary[prefix_position:]
                if body_fragment in body:
                    return body_fragment
        mentions = tuple(
            value
            for value in (
                event.canonical_company,
                *event.company_mentions,
            )
            if value
        )
        for candidate in cls._event_candidates(body):
            if candidate["event_type"] != event.event_type:
                continue
            quote = str(candidate["quote"])
            if event.funding_round and candidate.get("funding_round") not in {
                "",
                event.funding_round,
            }:
                continue
            if any(mention in quote for mention in mentions):
                return quote
            position = body.find(quote)
            if position >= 0:
                prefix = body[max(0, position - 240) : position]
                boundary = max(
                    prefix.rfind(marker) for marker in ("。", "！", "？", "\n")
                )
                prior_boundary = max(
                    (
                        prefix.rfind(marker, 0, boundary)
                        for marker in ("。", "！", "？", "\n")
                    ),
                    default=-1,
                )
                start = (
                    max(0, position - 240) + prior_boundary + 1
                    if boundary >= 0
                    else max(0, position - 240)
                )
                expanded = body[start : position + len(quote)].strip()
                if len(expanded) <= 500 and any(
                    mention in expanded for mention in mentions
                ):
                    return expanded
        for sentence in (
            value.strip()
            for value in re.split(r"(?<=[。！？；])", body)
            if value.strip()
        ):
            if any(mention in sentence for mention in mentions) and (
                cls._company_event_subject_grounded(
                    event.canonical_company,
                    sentence,
                    event.event_type,
                )
            ):
                return sentence
        # Keep a rule seed when the adapter has already identified the event
        # but the generic semantic ledger does not yet know that source's
        # wording (for example ``收到…采购订单`` or ``正式动工``).  This is
        # still bounded by the adapter's company mention and an event-specific
        # action cue; it does not turn arbitrary company prose into an event.
        action_cues = {
            "funding": r"融资|入股|增资|股东|投资基金|募资",
            "major_order": r"订单|合同|中标|签订|签署|采购|项目|供货协议|委托.{0,20}建造",
            "factory_or_capacity": r"投资|建设|扩产|扩充|产能|产线|生产线|动工|投产|量产|制造",
            "technical_milestone": r"发布|推出|首发|量产|交付|出货|样品|上线|开源",
            "partnership": r"合作|协议|合资|签署|签订",
            "new_site_or_entity": r"成立|设立|注册|落地|子公司|研究院|基地",
            "merger_acquisition": r"收购|并购|合并|股权|控制权",
            "ipo_or_listing": r"上市|挂牌|IPO|递表|上市申请",
            "regulatory_or_clinical": r"获批|批准|认证|许可|临床|注册证",
            "policy_or_standard": r"政策|标准|办法|条例|规范|通知|印发|发布",
            "procurement_tender": r"招标|采购|中标|入围",
            "customer_validation": r"客户|验证|导入|定点|复购|交付",
            "enterprise_system": r"系统|平台|ERP|MES|CRM|上线|部署",
        }
        cue = action_cues.get(event.event_type)
        if cue:
            for sentence in (
                value.strip()
                for value in re.split(r"(?<=[。！？；])", body)
                if value.strip()
            ):
                if any(mention in sentence for mention in mentions) and re.search(
                    cue, sentence, re.I
                ):
                    return sentence
        return ""

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
            if previous is None:
                normalized[key] = updated
                continue
            preferred = previous
            if previous.processor.startswith("rules") and not updated.processor.startswith(
                "rules"
            ):
                preferred = updated
            normalized[key] = replace(
                preferred,
                claim_ids=tuple(
                    dict.fromkeys((*previous.claim_ids, *updated.claim_ids))
                ),
                span_ids=tuple(
                    dict.fromkeys((*previous.span_ids, *updated.span_ids))
                ),
                evidence_quotes=tuple(
                    dict.fromkeys(
                        (*previous.evidence_quotes, *updated.evidence_quotes)
                    )
                ),
                ambiguities=tuple(
                    dict.fromkeys((*previous.ambiguities, *updated.ambiguities))
                ),
            )
        return list(normalized.values())

    @classmethod
    def _preserve_v27_policy_rule_seeds(
        cls,
        events: list[SemanticEvent],
        rule_events: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        """Keep high-confidence policy notices when claim routing has no issuer.

        Government notices are intentionally not eligible operating-company
        subjects in the entity ledger.  A strict claim batch can therefore
        reject every noisy action span even though the dedicated adapter has a
        precise, title-grounded policy seed.  Preserve only that narrow rule
        family; ordinary company claims still require claim-level adjudication.
        """

        output = list(events)
        for seed in rule_events:
            if seed.event_type != "policy_or_standard":
                continue
            canonical = cls._canonical_company(seed.canonical_company)
            if any(
                event.event_type == seed.event_type
                and cls._canonical_company(event.canonical_company) == canonical
                for event in output
            ):
                continue
            output.append(
                replace(
                    seed,
                    ambiguities=tuple(
                        dict.fromkeys(
                            (*seed.ambiguities, "claim_loop_policy_seed_preserved")
                        )
                    ),
                )
            )
        return cls._normalize_final_events(output)

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
        if normalized in {
            "\u672a\u62ab\u9732",
            "\u672a\u660e\u786e",
            "\u672a\u77e5",
            "\u4e0d\u8be6",
        }:
            return ""
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
    def _defined_alias_for_company(article_text: str, company: str) -> str:
        """Return an alias explicitly bound to a legal company name in text."""

        pattern = re.compile(
            rf"{re.escape(company)}\s*[（(]"
            r"(?:\u4ee5\u4e0b\u7b80\u79f0|\u7b80\u79f0)\s*[‘’“\"']?"
            r"(?P<alias>[A-Za-z0-9\u4e00-\u9fff·. -]{2,40}?)"
            r"[’‘”\"']?[）)]"
        )
        match = pattern.search(article_text)
        if not match:
            return ""
        alias = match.group("alias").strip()
        return alias if is_company_like(alias) else ""

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

    @staticmethod
    def _is_auxiliary_subject_prefix(value: str) -> bool:
        """Match neutral subject prefixes without a backtracking regex."""
        tokens = (
            "\u8be5\u516c\u53f8",
            "\u672c\u516c\u53f8",
            "\u6b63\u5728",
            "\u516c\u53f8",
            "\u6b63\u5f0f",
            "\u6210\u529f",
            "\u8ba1\u5212",
            "\u5df2",
            "\u5176",
            "\u5c06",
            "\u62df",
            "\u6b63",
        )
        position = 0
        while position < len(value):
            for token in tokens:
                if value.startswith(token, position):
                    position += len(token)
                    break
            else:
                return False
        return True

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
            "major_order": r"(?:\u4e2d\u6807|\u83b7\u5f97|\u65a9\u83b7|\u83b7(?!\u6089)|\u7b7e\u7f72|\u7b7e\u8ba2|\u62ff\u4e0b).{0,50}(?:\u8ba2\u5355|\u5408\u540c|\u9879\u76ee|\u91c7\u8d2d|\u5b9a\u70b9)",
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
                # Stock-code and legal-form metadata immediately after the
                # subject is part of the noun phrase. Punctuation inside that
                # parenthetical must not split the company from its predicate.
                tail = re.sub(r"^[（(][^）)]{1,80}[）)]", "", tail)
                # Accept both ASCII and full-width parenthetical stock-code
                # metadata using explicit Unicode escapes.
                tail = re.sub(
                    r"^(?:\([^)]{1,80}\)|\uff08[^\uff09]{1,80}\uff09)",
                    "",
                    tail,
                )
                hard_positions = [
                    tail.find(mark) for mark in "\u3002\uff01\uff1f\uff1b\n"
                ]
                hard_positions = [value for value in hard_positions if value >= 0]
                bounded_tail = tail[: min(hard_positions)] if hard_positions else tail
                soft_positions = [bounded_tail.find(mark) for mark in "\uff0c,:\uff1a"]
                soft_positions = [value for value in soft_positions if value >= 0]
                if soft_positions:
                    first_soft = min(soft_positions)
                    first_segment = bounded_tail[:first_soft]
                    # Digest and bulletin items commonly use ``Company: action``.
                    # A leading colon is a subject/predicate delimiter, not a
                    # clause boundary.  Treating the empty prefix as the whole
                    # searchable segment caused valid orders, acquisitions and
                    # partnerships to fail subject grounding after the model had
                    # cited the exact immutable span.
                    after = (
                        bounded_tail[first_soft + 1 :]
                        if first_soft == 0
                        else first_segment
                    )
                    if re.fullmatch(
                        r"(?:\u5ba3\u5e03|\u8868\u793a|\u62ab\u9732|\u5b98\u5ba3|\u79f0|\u6d88\u606f\u79f0|"
                        r"(?:\d{1,2}\u6708\d{1,2}\u65e5)?\u516c\u544a)",
                        first_segment,
                    ):
                        remainder = bounded_tail[first_soft + 1 :]
                        next_soft = [remainder.find(mark) for mark in "\uff0c,:\uff1a"]
                        next_soft = [value for value in next_soft if value >= 0]
                        next_segment = (
                            remainder[: min(next_soft)] if next_soft else remainder
                        )
                        event_match = re.search(predicate, next_segment)
                        if event_match:
                            subject_prefix = next_segment[: event_match.start()]
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
                                cls._is_auxiliary_subject_prefix(subject_prefix)
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
            r"\u6b64\u6b21|\u672c\u6b21|"
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
        if cls._company_subject_grounded(company, current_span):
            return current_span.strip()
        if boundary >= 0:
            current_prefix = article_text[context_start:start].strip()
            if re.search(
                r"(?:\u516c\u53f8|\u79d1\u6280|\u96c6\u56e2|\u80a1\u4efd|\u7535\u5b50|\u667a\u80fd)"
                r".{0,12}(?:\u8868\u793a|\u5ba3\u5e03|\u79f0)[\uff0c,:\uff1a]?$",
                current_prefix,
            ):
                return ""
        # A result clause may use “公司” while the target is named two
        # sentences earlier. Walk back at most three bounded sentences and
        # accept only an untouched span that explicitly contains the company.
        for _ in range(3):
            prior = article_text[left : max(left, context_start - 1)]
            prior_boundary = max(
                prior.rfind(marker)
                for marker in ("\u3002", "\uff01", "\uff1f", "\n")
            )
            next_start = left + prior_boundary + 1 if prior_boundary >= 0 else left
            if next_start >= context_start:
                break
            context_start = next_start
            expanded = article_text[
                context_start : start + len(primary_quote)
            ].strip()
            if len(expanded) > 500:
                return ""
            if cls._company_subject_grounded(company, expanded):
                return expanded
            if prior_boundary < 0:
                break
        return ""

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
            return int(year_text) < published.year
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
