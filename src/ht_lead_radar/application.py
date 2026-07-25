"""End-to-end application service shared by CLI, OpenClaw and cron.

The service is intentionally built around JSON checkpoints.  A failed run can
resume without re-crawling fixed sources or repeating Metaso calls, and every
published report carries its request interpretation and execution parameters.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .collectors import (
    BingRSSCollector,
    MetasoCollector,
    SearXNGCollector,
    SearchResult,
    collect_josint,
    company_mentioned,
    infer_routes_from_text,
    load_demo_fixture,
    load_env_file,
)
from .costs import (
    METASO_CONSERVATIVE_POINTS_PER_SEARCH,
    SearchBudgetLedger,
)
from .discovery import PlannedSearchCollector
from .fact_store import FactStore
from .feishu import (
    FeishuBitableClient,
    ProjectionState,
    sync_leads,
)
from .fixed_sources import FixedSourceCollector
from .float_matching import rank_candidate_float
from .models import CompanyLead, Evidence
from .ops import AuditLog, OpsMetricsStore, SuppressionRegistry
from .pipeline import build_late_opportunities, build_leads
from .relationships import DeepResearchEngine, RelationshipStore
from .reporting_v2 import render_complete_markdown, write_complete_outputs
from .requests import CandidateProfile
from .role_inference import enrich_industry_roles
from .runtime import (
    RunStore,
    RuntimeResult,
    StageContext,
    StagedRuntime,
    make_run_id,
)
from .serde import evidence_from_dict, lead_from_dict
from .taxonomy import classify_seniority


DEFAULTS = {
    "provider": "auto",
    "fixed_sources": "config/fixed-sources.json",
    "source_packs": "config/source-packs.json",
    "source_state_db": "data/fixed-sources.sqlite",
    "fact_db": "data/facts.sqlite",
    "runtime_db": "data/runtime.sqlite",
    "relationship_db": "data/relationships.sqlite",
    "budget_db": "data/search-budget.sqlite",
    "feishu_state_db": "data/feishu-projection.sqlite",
    "audit_db": "data/audit.sqlite",
    "ops_metrics_db": "data/ops-metrics.sqlite",
    "output_dir": "reports",
    "top": 20,
    "minimum_score": 0.0,
    "limit_per_query": 8,
    "metaso_verify_limit": 3,
    "metaso_daily_point_budget": 30,
    "metaso_provider_daily_limit": 500,
    "metaso_points_per_search": 6,
}


@dataclass(frozen=True)
class ApplicationResult:
    runtime: RuntimeResult

    @property
    def output(self) -> Mapping[str, Any]:
        value = self.runtime.output
        return value if isinstance(value, Mapping) else {}

    @property
    def lead_count(self) -> int:
        return int(self.output.get("lead_count", 0))


class FallbackSearchProvider:
    """Try local/private infrastructure before public Bing RSS."""

    provider_name = "public-search-fallback"
    supports_search = True

    def __init__(self, providers: Iterable[Any]):
        self.providers = tuple(providers)
        if not self.providers:
            raise ValueError("at least one search provider is required")

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        failures: list[str] = []
        for provider in self.providers:
            try:
                return provider.search(query, limit=limit)
            except Exception as error:
                failures.append(f"{provider.provider_name}: {error}")
        raise RuntimeError(" | ".join(failures))

    def collect_routes(
        self,
        company: str,
        direction: str,
        limit_per_query: int = 8,
    ):
        # Use this wrapper's fallback search for every query rather than
        # delegating to one provider that may currently be unavailable.
        routes = []
        seen: set[tuple[str, str]] = set()
        queries = (
            f"{company} {direction} 融资 投资方 领投 投资人",
            f"{company} 创始人 毕业 校友 师从",
            f"{company} 创始人 曾任 前同事 团队来自",
        )
        for query in queries:
            for result in self.search(query, limit=limit_per_query):
                text = f"{result.title} {result.snippet}"
                if not company_mentioned(company, text):
                    continue
                for route in infer_routes_from_text(text, result.url, company=company):
                    key = (route.kind, route.target)
                    if key not in seen:
                        seen.add(key)
                        routes.append(route)
        return routes


class LeadRadarApplication:
    def __init__(self, runtime_database: str | Path):
        self.runtime_store = RunStore(runtime_database)
        self._ephemeral_candidates: dict[str, CandidateProfile] = {}
        self._ephemeral_float_results: dict[str, list[dict[str, Any]]] = {}
        self.runtime = StagedRuntime(
            self.runtime_store,
            {
                "collect": self._collect,
                "normalize": self._normalize,
                "eventize": self._eventize,
                "score": self._score,
                "basic_research": self._basic_research,
                "publish": self._publish,
            },
        )

    def run(self, payload: Mapping[str, Any], idempotency_key: str) -> ApplicationResult:
        normalized = apply_defaults(payload)
        run_id = make_run_id(idempotency_key)
        plan = json.loads(json.dumps(normalized.get("request_plan") or {}))
        request = dict(plan.get("request") or {})
        candidate_data = request.get("candidate_profile")
        if str(request.get("mode", "")).upper() == "CANDIDATE_FLOAT":
            if not candidate_data:
                raise ValueError("Candidate Float requires an ephemeral candidate profile")
            self._ephemeral_candidates[run_id] = _candidate_from_dict(candidate_data)
            request.update({
                "raw_text": "[Candidate Float request redacted: runtime-only]",
                "target_role": None,
                "candidate_profile": None,
            })
            plan["request"] = request
            normalized["request_plan"] = plan
        _drop_candidate_fields(normalized)
        result = self.runtime.run(idempotency_key, normalized)
        if result.status == "completed":
            self._ephemeral_candidates.pop(run_id, None)
            self._ephemeral_float_results.pop(run_id, None)
        return ApplicationResult(result)

    def resume(self, run_id: str) -> ApplicationResult:
        record = self.runtime_store.get_run(run_id)
        plan = record.input.get("request_plan") if isinstance(record.input, dict) else {}
        if _request_mode(plan or {}) == "candidate_float" and run_id not in self._ephemeral_candidates:
            raise RuntimeError(
                "Candidate Float profile was intentionally not persisted; rerun the Float "
                "request with the candidate description to resume."
            )
        return ApplicationResult(self.runtime.resume(run_id))

    def replay(
        self,
        run_id: str,
        *,
        from_stage: str = "normalize",
        reuse_costly: bool = True,
    ) -> ApplicationResult:
        return ApplicationResult(
            self.runtime.replay(
                run_id,
                from_stage=from_stage,
                reuse_costly=reuse_costly,
            )
        )

    # ------------------------------------------------------------------
    # Six checkpointed stages

    def _collect(self, context: StageContext) -> dict[str, Any]:
        payload = dict(context.value)
        direction = str(payload["direction"])
        plan = dict(payload.get("request_plan") or {})
        as_of = date.today()
        metadata: dict[str, Any] = {
            "routes": {},
            "ad_checks": {},
            "request_mode": _request_mode(plan),
            "source_runs": [],
            "source_failures": [],
        }
        evidence: list[Evidence] = []

        if payload.get("demo"):
            evidence, fixture_metadata = load_demo_fixture(direction)
            metadata.update(fixture_metadata)
            as_of = date.fromisoformat(fixture_metadata["as_of"])
            mode = "demo-real-evidence"
        elif payload.get("replay_json"):
            evidence, replay_metadata = _load_replay_any(
                Path(str(payload["replay_json"])),
                direction,
            )
            metadata.update(replay_metadata)
            mode = "replay-audited-evidence"
        else:
            env = load_env_file(payload.get("env_file"))
            provider_mode = str(payload["provider"])
            if provider_mode == "metaso":
                raise ValueError(
                    "Metaso is verification-only; discovery with provider=metaso is disabled "
                    "to protect the 500-point daily quota."
                )

            if provider_mode in {"auto", "fixed"}:
                fixed_items, fixed_summary = self._collect_fixed(
                    context,
                    payload,
                    direction,
                    as_of.year,
                )
                evidence.extend(fixed_items)
                metadata["source_runs"].extend(fixed_summary.get("runs", ()))
                metadata["source_failures"].extend(fixed_summary.get("failures", ()))

            distinct_companies = {item.company for item in evidence}
            target_count = max(int(payload["top"]), 0)
            if (
                provider_mode in {"auto", "searxng", "bing"}
                and len(distinct_companies) < target_count
            ):
                search_provider = _search_provider(
                    payload,
                    env,
                    force=provider_mode if provider_mode != "auto" else "",
                )
                planner_queries = tuple(plan.get("discovery_queries") or ())
                collector = PlannedSearchCollector(search_provider, planner_queries)
                try:
                    searched = context.effect_once(
                        "public-search-discovery",
                        lambda _token: [
                            asdict(item)
                            for item in collector.collect(
                                direction,
                                year=as_of.year,
                                limit_per_query=int(payload["limit_per_query"]),
                            )
                        ],
                    )
                    evidence.extend(evidence_from_dict(item) for item in searched)
                    metadata["source_runs"].append({
                        "provider": search_provider.provider_name,
                        "status": "ok",
                        "evidence_count": len(searched),
                    })
                except Exception as error:
                    metadata["source_failures"].append(
                        f"public-search-discovery: {type(error).__name__}: {error}"
                    )
                    if provider_mode != "auto" and not evidence:
                        raise
            mode = "fixed-sources-primary+bounded-public-fallback"

        josint_db = payload.get("josint_db")
        if josint_db and Path(str(josint_db)).exists():
            evidence.extend(collect_josint(str(josint_db), direction))
            mode += "+josint-late-validation"

        return {
            "payload": payload,
            "direction": direction,
            "as_of": as_of.isoformat(),
            "mode": mode,
            "request_plan": plan,
            "metadata": metadata,
            "evidence": [asdict(item) for item in evidence],
        }

    def _normalize(self, context: StageContext) -> dict[str, Any]:
        value = dict(context.value)
        payload = value["payload"]
        plan = value.get("request_plan") or {}
        evidence = [evidence_from_dict(item) for item in value.get("evidence", ())]
        suppression = _suppression_registry(payload)
        deduplicated: list[Evidence] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        excluded: list[dict[str, str]] = []
        for item in evidence:
            entry_point = (
                "candidate_float"
                if _request_mode(plan) == "candidate_float"
                else "market_scan"
            )
            if suppression and suppression.check_company(entry_point, item.company).suppressed:
                excluded.append({"company": item.company, "reason": "suppression"})
                continue
            key = (
                item.company.casefold(),
                item.event_type,
                item.event_date,
                item.source_url,
                item.title,
            )
            if key not in seen:
                seen.add(key)
                deduplicated.append(item)

        grouped: dict[str, list[Evidence]] = {}
        for item in deduplicated:
            grouped.setdefault(item.company, []).append(item)
        geography = ((plan.get("request") or {}).get("geography") or {})
        geography_code = geography.get("code", "CN_MAINLAND_HIRING_MARKET")
        mainland_relevance: dict[str, str] = {}
        qualified_companies: set[str] = set()
        for company, items in grouped.items():
            if geography_code != "CN_MAINLAND_HIRING_MARKET":
                qualified_companies.add(company)
                mainland_relevance[company] = geography.get("label", "用户指定地域")
            elif _has_mainland_relevance(company, items):
                qualified_companies.add(company)
                mainland_relevance[company] = "中国大陆招聘市场相关"
            else:
                excluded.append({
                    "company": company,
                    "reason": "未找到中国大陆招聘市场相关性",
                })

        industry_layers: dict[str, str] = {}
        adjacent_watchlist: list[str] = []
        for company, items in grouped.items():
            if company not in qualified_companies:
                continue
            layer = _industry_layer(items, plan.get("industry_map") or {})
            industry_layers[company] = layer
            if layer == "adjacent":
                adjacent_watchlist.append(company)

        # Adjacent-only companies remain visible in a watchlist but do not
        # enter the main hard-gated Top 20.
        normalized = [
            item
            for item in deduplicated
            if item.company in qualified_companies
            and industry_layers.get(item.company) != "adjacent"
        ]
        metadata = dict(value.get("metadata") or {})
        metadata["mainland_relevance"] = mainland_relevance
        metadata["industry_layers"] = industry_layers
        metadata["normalization_exclusions"] = excluded
        metadata["adjacent_watchlist"] = adjacent_watchlist
        value["metadata"] = metadata
        value["evidence"] = [asdict(item) for item in normalized]
        return value

    def _eventize(self, context: StageContext) -> dict[str, Any]:
        value = dict(context.value)
        fact_store = FactStore(value["payload"]["fact_db"])
        evidence = [evidence_from_dict(item) for item in value.get("evidence", ())]
        annotated: list[Evidence] = []
        event_ids: set[str] = set()
        document_ids: set[str] = set()
        entity_ids: set[str] = set()
        for item in evidence:
            result = fact_store.ingest_legacy_evidence(item)
            annotated.append(replace(
                item,
                document_id=result.document.id,
                event_id=result.event.id,
                statement_ids=(result.statement.id,),
                independent_source_group=result.document.independent_source_key,
            ))
            document_ids.add(result.document.id)
            event_ids.add(result.event.id)
            entity_ids.add(result.entity.id)
        value["evidence"] = [asdict(item) for item in annotated]
        value["fact_summary"] = {
            "documents_in_run": len(document_ids),
            "events_in_run": len(event_ids),
            "entities_in_run": len(entity_ids),
            "database": str(value["payload"]["fact_db"]),
        }
        return value

    def _score(self, context: StageContext) -> dict[str, Any]:
        value = dict(context.value)
        evidence = [evidence_from_dict(item) for item in value.get("evidence", ())]
        payload = value["payload"]
        as_of = date.fromisoformat(value["as_of"])
        leads = build_leads(
            value["direction"],
            evidence,
            value.get("metadata"),
            as_of=as_of,
            minimum_score=float(payload["minimum_score"]),
            limit=int(payload["top"]),
        )
        enrich_industry_roles(leads, value["direction"])
        value["leads"] = [lead.to_dict() for lead in leads]
        value["late_opportunities"] = build_late_opportunities(
            value["direction"], evidence
        )
        return value

    def _basic_research(self, context: StageContext) -> dict[str, Any]:
        value = dict(context.value)
        payload = value["payload"]
        plan = value.get("request_plan") or {}
        evidence = [evidence_from_dict(item) for item in value.get("evidence", ())]
        leads = [lead_from_dict(item) for item in value.get("leads", ())]
        metadata = dict(value.get("metadata") or {})
        env = load_env_file(payload.get("env_file"))
        mode = _request_mode(plan)
        is_offline = bool(payload.get("demo") or payload.get("replay_json"))
        research_provider = None if is_offline else _search_provider(payload, env)

        # One bounded public job-ad check per company.  Daily basic research
        # does not run investor/HM/HR/founder queries.
        if research_provider:
            for lead in leads:
                query = f"{lead.company} {' '.join(lead.target_roles)} 招聘"
                try:
                    results = context.effect_once(
                        f"job-ad-check:{lead.company}",
                        lambda _token, query=query: [
                            asdict(item)
                            for item in research_provider.search(
                                query,
                                limit=min(int(payload["limit_per_query"]), 8),
                            )
                        ],
                    )
                except Exception as error:
                    metadata["ad_checks"][lead.company] = {
                        "checked_at": value["as_of"],
                        "queries": [query],
                        "matching_results": 0,
                        "status": f"error: {error}",
                    }
                    continue
                matches = []
                for raw in results:
                    result = SearchResult(**raw)
                    text = f"{result.title} {result.snippet}"
                    if not company_mentioned(lead.company, text):
                        continue
                    if not classify_seniority(result.title, result.snippet)[1]:
                        continue
                    matches.append(result)
                    evidence.append(Evidence(
                        company=lead.company,
                        event_type="job_ad",
                        phase="recruit",
                        event_date=result.published_at,
                        title=result.title,
                        snippet=result.snippet[:800],
                        source_url=result.url,
                        source_name=research_provider.provider_name,
                        source_grade="C",
                        direction=value["direction"],
                    ))
                metadata["ad_checks"][lead.company] = {
                    "checked_at": value["as_of"],
                    "queries": [query],
                    "matching_results": len(matches),
                    "status": "ok",
                }

        # New ad evidence goes through the same fact/event store before scores
        # are recomputed.
        fact_store = FactStore(payload["fact_db"])
        annotated: list[Evidence] = []
        for item in evidence:
            if item.document_id and item.event_id:
                annotated.append(item)
                continue
            result = fact_store.ingest_legacy_evidence(item)
            annotated.append(replace(
                item,
                document_id=result.document.id,
                event_id=result.event.id,
                statement_ids=(result.statement.id,),
                independent_source_group=result.document.independent_source_key,
            ))
        evidence = annotated

        leads = build_leads(
            value["direction"],
            evidence,
            metadata,
            as_of=date.fromisoformat(value["as_of"]),
            minimum_score=float(payload["minimum_score"]),
            limit=int(payload["top"]),
        )
        enrich_industry_roles(leads, value["direction"])

        verification = self._metaso_verify(
            context,
            payload,
            env,
            leads,
            value["as_of"],
        )
        if verification:
            metadata["verification"] = verification
            leads = build_leads(
                value["direction"],
                evidence,
                metadata,
                as_of=date.fromisoformat(value["as_of"]),
                minimum_score=float(payload["minimum_score"]),
                limit=int(payload["top"]),
            )
            enrich_industry_roles(leads, value["direction"])

        value["metaso_points_this_run"] = sum(
            max(int(result.get("query_count", 0)), 0)
            * max(
                int(payload["metaso_points_per_search"]),
                METASO_CONSERVATIVE_POINTS_PER_SEARCH,
            )
            for result in verification.values()
            if isinstance(result, Mapping)
        )

        deep_reports: dict[str, dict[str, Any]] = {}
        float_payload: list[dict[str, Any]] = []
        deep_requested = bool(
            ((plan.get("request") or {}).get("deep_research_requested"))
            or payload.get("deep_research")
        )
        if deep_requested and research_provider:
            engine = DeepResearchEngine(
                research_provider,
                RelationshipStore(payload["relationship_db"]),
            )
            for lead in leads:
                try:
                    report = context.effect_once(
                        f"deep-research:{lead.company}",
                        lambda _token, company=lead.company: engine.research(
                            company,
                            value["direction"],
                            refresh=bool(payload.get("refresh_deep_research")),
                        ).to_dict(),
                    )
                    deep_reports[lead.company] = report
                except Exception as error:
                    deep_reports[lead.company] = {
                        "company": lead.company,
                        "direction": value["direction"],
                        "institutions": [],
                        "investors": [],
                        "hiring_managers": [],
                        "hr_people": [],
                        "founders": [],
                        "caveats": [f"深度研究失败：{type(error).__name__}: {error}"],
                    }

        if mode == "candidate_float":
            candidate = self._ephemeral_candidates.get(context.run_id)
            if candidate is None:
                raise RuntimeError(
                    "Candidate Float profile is runtime-only and unavailable; "
                    "rerun with the candidate description."
                )
            if candidate:
                float_payload = [
                    item.to_dict()
                    for item in rank_candidate_float(
                        candidate,
                        leads,
                        as_of=date.fromisoformat(value["as_of"]),
                        limit=int(payload["top"]),
                    )
                ]
                order = {
                    item["company"]: index
                    for index, item in enumerate(float_payload)
                }
                leads.sort(key=lambda lead: order.get(lead.company, 10_000))

        value["metadata"] = metadata
        value["evidence"] = [asdict(item) for item in evidence]
        value["leads"] = [lead.to_dict() for lead in leads]
        value["late_opportunities"] = build_late_opportunities(
            value["direction"], evidence
        )
        self._ephemeral_float_results[context.run_id] = float_payload
        value["float_matches"] = []
        value["deep_research"] = deep_reports
        value["budget_status"] = SearchBudgetLedger(payload["budget_db"]).status(
            configured_limit=int(payload["metaso_daily_point_budget"]),
            provider_limit=int(payload["metaso_provider_daily_limit"]),
        ).to_dict()
        return value

    def _publish(self, context: StageContext) -> dict[str, Any]:
        value = dict(context.value)
        payload = value["payload"]
        leads = [lead_from_dict(item) for item in value.get("leads", ())]
        deep_reports = value.get("deep_research") or {}
        for lead in leads:
            report = deep_reports.get(lead.company)
            if report:
                lead.basic_research["deep_research"] = report
        request_plan = value.get("request_plan") or {}
        output_dir = Path(payload["output_dir"])
        slug = re.sub(
            r"[^0-9A-Za-z\u4e00-\u9fff-]+",
            "-",
            value["direction"],
        ).strip("-") or "direction"
        stem = f"lead-radar-{slug}-{value['as_of']}"
        integration_status: dict[str, Any] = {}

        env = load_env_file(payload.get("env_file"))
        app_id = str(payload.get("feishu_app_id") or env.get("FEISHU_APP_ID") or "")
        app_secret = str(
            payload.get("feishu_app_secret") or env.get("FEISHU_APP_SECRET") or ""
        )
        app_token = str(
            payload.get("feishu_app_token")
            or env.get("FEISHU_BITABLE_APP_TOKEN")
            or ""
        )
        table_id = str(
            payload.get("feishu_table_id")
            or env.get("FEISHU_BITABLE_TABLE_ID")
            or ""
        )
        dry_run_path = str(
            payload.get("feishu_dry_run_path")
            or Path(payload["feishu_state_db"]).with_name("feishu-change-set.json")
        )
        projection_state = ProjectionState(payload["feishu_state_db"])
        client = None
        if all((app_id, app_secret, app_token, table_id)):
            client = FeishuBitableClient(
                app_id,
                app_secret,
                app_token,
                table_id,
            )
        try:
            changes = sync_leads(
                leads,
                projection_state,
                client=client,
                dry_run_path=dry_run_path,
            )
            integration_status["feishu"] = {
                "mode": "live" if client else "dry_run",
                "change_count": len(changes),
                "change_set": str(Path(dry_run_path).resolve()),
                "blocked_reason": (
                    ""
                    if client
                    else "缺少 FEISHU_BITABLE_APP_TOKEN / FEISHU_BITABLE_TABLE_ID；"
                    "已生成幂等增量变更集，未发送。"
                ),
            }
        except Exception as error:
            integration_status["feishu"] = {
                "mode": "error",
                "error": f"{type(error).__name__}: {error}",
                "change_set": str(Path(dry_run_path).resolve()),
            }

        source_summary = {
            "runs": (value.get("metadata") or {}).get("source_runs", []),
            "failures": (value.get("metadata") or {}).get("source_failures", []),
            "normalization_exclusions": (
                value.get("metadata") or {}
            ).get("normalization_exclusions", []),
            "adjacent_watchlist": (
                value.get("metadata") or {}
            ).get("adjacent_watchlist", []),
            "metaso_budget": value.get("budget_status", {}),
        }
        float_matches = self._ephemeral_float_results.get(context.run_id, [])
        markdown = render_complete_markdown(
            value["direction"],
            leads,
            value["as_of"],
            value["mode"],
            late_opportunities=value.get("late_opportunities") or [],
            request_plan=request_plan,
            float_matches=float_matches,
            deep_research=value.get("deep_research") or {},
            source_summary=source_summary,
            integration_status=integration_status,
        )
        manifest = {
            "run_id": context.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "as_of": value["as_of"],
            "mode": value["mode"],
            "direction": value["direction"],
            "request_plan": request_plan,
            "execution_parameters": _safe_parameters(payload),
            "fact_summary": value.get("fact_summary", {}),
            "source_summary": source_summary,
            "integration_status": integration_status,
            "policy": {
                "director_plus_only": True,
                "pre_job_upstream_signal_required": True,
                "target_count": int(payload["top"]),
                "soft_score_threshold": float(payload["minimum_score"]),
                "outreach_generation": False,
                "outreach_sending": False,
                "candidate_profile_persistence": False,
            },
        }
        md_path, json_path = write_complete_outputs(
            output_dir,
            stem,
            markdown,
            leads=leads,
            manifest=manifest,
            late_opportunities=value.get("late_opportunities") or [],
            float_matches=float_matches,
            deep_research=value.get("deep_research") or {},
        )
        try:
            metrics = OpsMetricsStore(payload["ops_metrics_db"])
            metrics.record_run(
                context.run_id,
                recorded_at=datetime.now(timezone.utc),
                status="completed",
                result_count=len(leads),
                metaso_points=float(value.get("metaso_points_this_run", 0)),
            )
            for index, source_run in enumerate(source_summary["runs"]):
                if not isinstance(source_run, Mapping):
                    continue
                source_id = str(
                    source_run.get("source_id")
                    or source_run.get("provider")
                    or f"source-{index}"
                )
                metrics.record_source(
                    context.run_id,
                    source_id,
                    recorded_at=datetime.now(timezone.utc),
                    ok=source_run.get("status") == "ok",
                    yield_count=max(
                        int(source_run.get("evidence_count", 0)), 0
                    ),
                )
        except Exception:
            pass
        try:
            AuditLog(payload["audit_db"]).record(
                actor=str(payload.get("actor") or "openclaw"),
                action="export",
                resource_type="lead_report",
                resource_id=str(json_path.resolve()),
                run_id=context.run_id,
                metadata={
                    "lead_count": len(leads),
                    "mode": _request_mode(request_plan),
                },
            )
        except Exception:
            # Report publication must not be rolled back by an unavailable
            # optional audit sink; monitoring will surface audit DB failures.
            pass
        return {
            "run_id": context.run_id,
            "lead_count": len(leads),
            "markdown_path": str(md_path.resolve()),
            "json_path": str(json_path.resolve()),
            "feishu": integration_status.get("feishu", {}),
            "budget_status": value.get("budget_status", {}),
            "source_failure_count": len(source_summary["failures"]),
        }

    # ------------------------------------------------------------------
    # Collection and metered verification helpers

    def _collect_fixed(
        self,
        context: StageContext,
        payload: Mapping[str, Any],
        direction: str,
        year: int,
    ) -> tuple[list[Evidence], dict[str, list[Any]]]:
        evidence: list[Evidence] = []
        summary: dict[str, list[Any]] = {"runs": [], "failures": []}
        registry_path = Path(str(payload["fixed_sources"]))
        if registry_path.exists():
            collector = FixedSourceCollector(
                registry_path=registry_path,
                state_db=payload["source_state_db"],
            )
            try:
                serialized = context.effect_once(
                    "legacy-fixed-sources",
                    lambda _token: [
                        asdict(item)
                        for item in collector.collect(
                            direction,
                            year=year,
                            limit_per_query=int(payload["limit_per_query"]),
                        )
                    ],
                )
                evidence.extend(evidence_from_dict(item) for item in serialized)
                summary["runs"].append({
                    "provider": collector.provider_name,
                    "status": "ok",
                    "evidence_count": len(serialized),
                })
                summary["failures"].extend(
                    collector.last_run_summary.get("errors", [])
                )
            except Exception as error:
                summary["failures"].append(
                    f"legacy-fixed-sources: {type(error).__name__}: {error}"
                )

        # The reusable source-pack collector is an optional module during
        # rolling upgrades; its absence never disables the proven legacy
        # collector above.
        try:
            from .source_pack_collector import SourcePackCollector

            with SourcePackCollector(
                registry_path=payload["source_packs"],
                state_db=payload["source_state_db"],
            ) as pack_collector:
                serialized = context.effect_once(
                    f"source-pack:{direction}",
                    lambda _token: [
                        asdict(item)
                        for item in pack_collector.collect(
                            direction,
                            year=year,
                            limit_per_query=int(payload["limit_per_query"]),
                        )
                    ],
                )
                pack_health = pack_collector.source_health_summary()
            evidence.extend(evidence_from_dict(item) for item in serialized)
            summary["runs"].append({
                "provider": "reusable-source-packs",
                "status": "ok",
                "evidence_count": len(serialized),
                "health": pack_health,
            })
        except ImportError:
            summary["failures"].append(
                "source-pack collector module unavailable; legacy fixed sources used"
            )
        except Exception as error:
            summary["failures"].append(
                f"source-pack:{type(error).__name__}: {error}"
            )
        return evidence, summary

    def _metaso_verify(
        self,
        context: StageContext,
        payload: Mapping[str, Any],
        env: Mapping[str, str],
        leads: list[CompanyLead],
        as_of: str,
    ) -> dict[str, Any]:
        api_key = env.get("METASO_API_KEY")
        limit = min(
            max(int(payload["metaso_verify_limit"]), 0),
            max(int(payload["metaso_daily_point_budget"]), 0)
            // max(int(payload["metaso_points_per_search"]), 1),
        )
        if not api_key or limit <= 0:
            return {}
        ledger = SearchBudgetLedger(payload["budget_db"])
        verifier = MetasoCollector(
            api_key=api_key,
            base_url=env.get("METASO_BASE_URL", "https://metaso.cn"),
        )
        output: dict[str, Any] = {}
        for lead in leads[:limit]:
            operation_key = f"{as_of}:{lead.company}:verification"

            def verify(_token: str, lead: CompanyLead = lead) -> dict[str, Any]:
                charged = ledger.charge(
                    operation_key,
                    int(payload["metaso_points_per_search"]),
                    configured_limit=int(payload["metaso_daily_point_budget"]),
                    provider_limit=int(payload["metaso_provider_daily_limit"]),
                )
                if not charged:
                    return {
                        "provider": "metaso",
                        "query_count": 0,
                        "matching_results": 0,
                        "status": "budget_exhausted_or_already_charged",
                    }
                query = (
                    f"{lead.company} 融资 扩产 量产 订单 战略合作 "
                    f"{date.fromisoformat(as_of).year}"
                )
                try:
                    results = verifier.search(query, limit=5)
                except Exception as error:
                    return {
                        "provider": "metaso",
                        "query_count": 1,
                        "matching_results": 0,
                        "status": f"error: {type(error).__name__}: {error}",
                    }
                matched = [
                    item for item in results
                    if company_mentioned(
                        lead.company,
                        f"{item.title} {item.snippet}",
                    )
                ]
                return {
                    "provider": "metaso",
                    "query_count": 1,
                    "matching_results": len(matched),
                    "status": "ok",
                }

            output[lead.company] = context.effect_once(
                f"metaso-verify:{lead.company}",
                verify,
            )
        return output


def apply_defaults(payload: Mapping[str, Any]) -> dict[str, Any]:
    output = {**DEFAULTS, **dict(payload)}
    required = ("direction", "request_plan")
    missing = [key for key in required if not output.get(key)]
    if missing:
        raise ValueError(f"missing application fields: {', '.join(missing)}")
    for key in (
        "fixed_sources",
        "source_packs",
        "source_state_db",
        "fact_db",
        "runtime_db",
        "relationship_db",
        "budget_db",
        "feishu_state_db",
        "audit_db",
        "ops_metrics_db",
        "output_dir",
    ):
        output[key] = str(output[key])
    return output


def default_idempotency_key(payload: Mapping[str, Any], *, refresh: bool = False) -> str:
    canonical = json.dumps(
        {
            "date": date.today().isoformat(),
            "command": payload.get("command", "run"),
            "direction": payload.get("direction"),
            "request_plan": payload.get("request_plan"),
            "provider": payload.get("provider", "auto"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    if refresh:
        return f"manual-refresh:{digest}:{datetime.now(timezone.utc).isoformat()}"
    return f"daily:{digest}"


def _request_mode(plan: Mapping[str, Any]) -> str:
    raw = str(((plan.get("request") or {}).get("mode")) or "MARKET_SCAN")
    return "candidate_float" if raw.upper() == "CANDIDATE_FLOAT" else "market_scan"


def _search_provider(
    payload: Mapping[str, Any],
    env: Mapping[str, str],
    *,
    force: str = "",
) -> FallbackSearchProvider:
    providers: list[Any] = []
    requested = force or str(payload.get("provider") or "auto")
    if requested in {"auto", "searxng", "fixed"}:
        providers.append(SearXNGCollector(
            base_url=env.get("SEARXNG_URL", "http://localhost:8080"),
        ))
    if requested in {"auto", "bing", "fixed"} or not providers:
        providers.append(BingRSSCollector())
    return FallbackSearchProvider(providers)


def _load_replay_any(path: Path, direction: str) -> tuple[list[Evidence], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("leads"), list):
        evidence: list[Evidence] = []
        metadata = {"routes": {}, "ad_checks": {}}
        for raw_lead in payload["leads"]:
            lead = lead_from_dict(raw_lead)
            for item in lead.evidence:
                evidence.append(replace(item, direction=direction))
            metadata["routes"][lead.company] = [
                asdict(route) for route in lead.outreach_routes
            ]
        manifest = payload.get("manifest") or {}
        if manifest.get("as_of"):
            metadata["as_of"] = manifest["as_of"]
        return evidence, metadata
    if not isinstance(payload, list):
        raise ValueError("replay JSON must be a legacy lead array or schema v2 envelope")
    evidence = []
    metadata = {"routes": {}, "ad_checks": {}}
    for raw_lead in payload:
        lead = lead_from_dict(raw_lead)
        evidence.extend(replace(item, direction=direction) for item in lead.evidence)
        metadata["routes"][lead.company] = [
            asdict(route) for route in lead.outreach_routes
        ]
    return evidence, metadata


def _has_mainland_relevance(company: str, items: Iterable[Evidence]) -> bool:
    if re.search(r"[\u4e00-\u9fff]", company):
        return True
    text = " ".join(
        f"{item.title} {item.snippet} {item.source_url}" for item in items
    )
    mainland_terms = (
        "中国",
        "大陆",
        "北京",
        "上海",
        "深圳",
        "苏州",
        "无锡",
        "杭州",
        "南京",
        "成都",
        "武汉",
        "合肥",
        "西安",
        "广州",
        "天津",
        "重庆",
    )
    if any(term in text for term in mainland_terms):
        return True
    hosts = {urlparse(item.source_url).hostname or "" for item in items}
    return any(host.endswith(".cn") or host.endswith(".gov.cn") for host in hosts)


def _industry_layer(items: Iterable[Evidence], industry_map: Mapping[str, Any]) -> str:
    text = " ".join(f"{item.title} {item.snippet}" for item in items).casefold()
    for key in ("core", "direct_upstream", "direct_downstream", "adjacent"):
        terms = [
            str(term).casefold()
            for term in industry_map.get(key, ())
            if len(str(term).strip()) >= 2
        ]
        if any(term in text for term in terms):
            return key
    return "core_unverified"


def _candidate_from_dict(value: Mapping[str, Any]) -> CandidateProfile:
    payload = dict(value)
    for key in (
        "core_capabilities",
        "industry_experience",
        "leadership_scope",
        "geography_preferences",
        "desired_directions",
        "exclusions",
        "inferred_fields",
        "missing_critical_fields",
    ):
        payload[key] = tuple(payload.get(key) or ())
    return CandidateProfile(**payload)


def _suppression_registry(payload: Mapping[str, Any]) -> SuppressionRegistry | None:
    path = payload.get("suppressions")
    if not path:
        return None
    target = Path(str(path))
    return SuppressionRegistry.from_json(target) if target.exists() else None


def _safe_parameters(payload: Mapping[str, Any]) -> dict[str, Any]:
    secret_fragments = (
        "secret",
        "token",
        "key",
        "password",
        "resume",
        "candidate",
        "profile",
        "raw_text",
        "question",
        "curriculum",
    )
    output = {}
    for key, value in payload.items():
        if any(fragment in key.casefold() for fragment in secret_fragments):
            continue
        if key == "request_plan":
            continue
        output[key] = value
    return output


def _drop_candidate_fields(payload: dict[str, Any]) -> None:
    """Remove task-local candidate inputs before runtime serialization."""

    sensitive_fragments = (
        "candidate",
        "profile",
        "raw_text",
        "raw_request",
        "question",
        "curriculum",
    )
    for key in tuple(payload):
        normalized = str(key).casefold()
        if any(fragment in normalized for fragment in sensitive_fragments):
            payload.pop(key, None)


__all__ = [
    "ApplicationResult",
    "DEFAULTS",
    "FallbackSearchProvider",
    "LeadRadarApplication",
    "apply_defaults",
    "default_idempotency_key",
]
