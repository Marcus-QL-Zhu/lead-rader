"""Evidence-bound MiniMax semantic and ambiguity processor."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from json_repair import repair_json
import re
from typing import Any, Protocol

from .entities import canonical_company_name, company_alias_candidates, is_company_like
from .models import CleanArticle, SemanticEvent, SourceChannel


PROMPT_VERSION = "aggregate-semantic-v18"
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
    "company\u3001\u91d1\u989d\u3001\u8f6e\u6b21\u3001"
    "\u6295\u8d44\u4eba\u548cevidence_quotes\u5fc5\u987b"
    "\u9010\u5b57\u51fa\u73b0\u5728\u8f93\u5165\u539f\u6587\u3002"
    "\u65e0\u6cd5\u786e\u5b9a\u65f6\u4e0d\u751f\u6210event\uff0c"
    "\u5728ambiguities\u8bf4\u660e\u3002"
    "ambiguities\u5fc5\u987b\u662f\u5b57\u7b26\u4e32\u6570\u7ec4\uff0c"
    "\u6bcf\u4e2a\u5143\u7d20\u7528\u4e00\u53e5\u8bdd\u8bf4\u660e\u6b67\u4e49\u3002"
    "evidence_quotes\u7684\u7b2c\u4e00\u6761\u5fc5\u987b\u662f\u4e8b\u4ef6\u4e3b\u8bc1\u636e\uff0c"
    "\u5305\u542b\u4e3b\u4f53\u548c\u4e8b\u4ef6\u52a8\u4f5c\uff1b\u5176\u4ed6\u5f15\u6587\u53ea\u80fd\u8865\u5145\u540c\u4e00\u4e8b\u4ef6\u3002"
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
        rule_events = self._normalize_rule_events(article, rule_events)
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
        }
        self.last_audit = audit
        if not rule_events:
            audit["status"] = "no_rule_seed"
            return []
        if self.runner is None:
            return rule_events
        prompt = self._prompt(channel, article, rule_events)
        try:
            response = self.runner.run(
                prompt,
                session_id=(
                    f"aggregate:{article.index.source_id}:"
                    f"{article.index.source_article_id}:"
                    f"{article.content_hash[:12]}"
                ),
                system_prompt=SYSTEM_PROMPT,
            )
            audit["first_response"] = response
            events = self._validate_payload(
                article,
                self._parse_json(response),
                rule_events,
            )
            audit["status"] = "accepted"
            self._complete_audit(audit, events)
            return events
        except Exception as first_error:
            audit["error"] = f"{type(first_error).__name__}: {first_error}"
        try:
            repair = self.runner.run(
                "The prior output failed JSON or evidence grounding validation. "
                "Return only corrected JSON under the original system constraints.\n"
                f"Original input: {prompt}\n"
                f"Prior output: {audit['first_response'][:4000]}\n"
                f"Validation error: {audit['error']}",
                session_id=(
                    f"aggregate-repair:{article.index.source_id}:"
                    f"{article.index.source_article_id}:"
                    f"{article.content_hash[:12]}"
                ),
                system_prompt=SYSTEM_PROMPT,
            )
            audit["repair_response"] = repair
            events = self._validate_payload(
                article,
                self._parse_json(repair),
                rule_events,
            )
            audit["status"] = "repaired"
            audit["error"] = ""
            events = self._salvage_grounded_investors(
                article,
                events,
                (
                    str(audit["first_response"]),
                    str(audit["repair_response"]),
                ),
                "",
            )
            self._complete_audit(audit, events)
            return events
        except Exception as repair_error:
            audit["status"] = "fallback_to_rules"
            audit["error"] = (
                f"{audit['error']}; repair "
                f"{type(repair_error).__name__}: {repair_error}"
            )
            marker = f"minimax_validation_failed:{type(repair_error).__name__}"
            events = self._salvage_grounded_investors(
                article,
                rule_events,
                (
                    str(audit["first_response"]),
                    str(audit["repair_response"]),
                ),
                marker,
            )
            self._complete_audit(audit, events)
            return events

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
        text = f"{article.index.title}\n{article.index.summary}\n{article.clean_body}"
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
        for sentence in re.split(r"(?<=[\u3002\uff01\uff1f\uff1b])", text):
            quote = sentence.strip()
            if investor not in quote or cls._is_historical_background(quote):
                continue
            if not re.search(
                r"\u672c\u8f6e|\u672c\u6b21|\u6295\u8d44\u65b9|"
                r"\u9886\u6295|\u8ddf\u6295|\u53c2\u4e0e.{0,8}"
                r"\u878d\u8d44|\u7531.{0,80}\u6295\u8d44",
                quote,
            ):
                continue
            score = 0
            if any(
                mention and mention in quote
                for mention in (
                    seed.canonical_company,
                    *seed.company_mentions,
                )
            ):
                score += 3
            if seed.funding_round and seed.funding_round in quote:
                score += 2
            if seed.funding_amount and seed.funding_amount in quote:
                score += 2
            if "\u672c\u8f6e" in quote or "\u672c\u6b21" in quote:
                score += 2
            candidates.append((score, -len(quote), quote[:500]))
        if not candidates:
            return ""
        return max(candidates)[2]

    @staticmethod
    def _complete_audit(
        audit: dict[str, Any],
        events: list[SemanticEvent],
    ) -> None:
        audit["final_event_count"] = len(events)
        audit["rules_preserved_count"] = sum(
            event.processor.startswith("rules") for event in events
        )
        audit["omissions_detected"] = max(
            0,
            int(audit["rule_seed_count"]) - len(events),
        )

    @staticmethod
    def _prompt(
        channel: SourceChannel,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
    ) -> str:
        seed = [
            {
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
            "article": article.clean_body[:8000],
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
    def _parse_json(value: str) -> dict[str, Any]:
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
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as error:
            try:
                payload = repair_json(candidate, return_objects=True)
            except Exception as repair_error:
                raise SemanticOutputError(
                    f"invalid JSON: {error}; deterministic repair failed: "
                    f"{type(repair_error).__name__}"
                ) from error
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
        text = f"{article.index.title}\n{article.index.summary}\n{article.clean_body}"

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
            named_investors: list[str] = []
            omitted_investors: list[str] = []
            for investor in map(str, investors):
                value = investor.strip()
                if not value:
                    continue
                if _UNNAMED_INVESTOR.fullmatch(value):
                    omitted_investors.append(value)
                    continue
                if value not in quote_text:
                    raise SemanticOutputError("investor is absent from evidence quote")
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
                f"unnamed_investor_omitted:{value}" for value in omitted_investors
            )
            seed = cls._matching_seed(
                rule_events,
                company,
                event_type,
                round_name,
                event_status,
            )
            canonical_company = seed.canonical_company if seed else company
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
        if subject_mismatch_count and not model_events and not rule_events:
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

    @classmethod
    def _normalize_rule_events(
        cls,
        article: CleanArticle,
        events: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        article_text = (
            f"{article.index.title}\n{article.index.summary}\n{article.clean_body}"
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
            r"^(?:\u5176|\u8be5\u516c\u53f8|\u516c\u53f8)", primary_quote.lstrip()
        ):
            return ""
        start = article_text.find(primary_quote)
        if start < 0 or article_text.find(primary_quote, start + 1) >= 0:
            return ""
        left = max(0, start - 220)
        prefix = article_text[left:start]
        boundary = max(
            prefix.rfind(marker) for marker in ("\u3002", "\uff01", "\uff1f", "\n")
        )
        context_start = left + boundary + 1 if boundary >= 0 else left
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
                r"(?<!\d)(20\d{2})(?=\u5e74|[-/.]\d{1,2})", summary
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
        merged = {cls._event_key(event): event for event in rule_events}
        for event in model_events:
            seed = cls._matching_seed(
                rule_events,
                event.canonical_company,
                event.event_type,
                event.funding_round,
                event.event_status,
            )
            if seed is None:
                continue
            merged.pop(cls._event_key(seed), None)
            merged[cls._event_key(event)] = event
        return cls._normalize_final_events(list(merged.values()))

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
