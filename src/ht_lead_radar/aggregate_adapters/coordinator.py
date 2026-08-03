"""Incremental orchestration for dedicated aggregate-source adapters."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import ipaddress
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterable
import urllib.parse
import urllib.request

from ..models import Evidence
from ..http_runtime import read_response_body
from .base import AdapterContext
from .models import (
    AdapterRun,
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from .registry import DedicatedAdapterRegistry
from .semantic import MiniMaxSemanticProcessor, PromptRunner
from .storage import AggregateStateStore


@dataclass(frozen=True)
class DedicatedCollectionResult:
    evidence: tuple[Evidence, ...]
    run: AdapterRun


@dataclass(frozen=True)
class _SemanticWork:
    index: SourceArticleIndex
    article: CleanArticle
    future: Future[tuple[list[SemanticEvent], dict[str, Any]]]


def _validate_public_http_url(url: str, *, expected_host: str = "") -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError(f"unsafe aggregate URL: {url}")
    if expected_host and host != expected_host.lower().rstrip("."):
        raise ValueError(f"cross-host redirect rejected: {expected_host} -> {host}")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError(f"local aggregate URL rejected: {url}")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    if not address.is_global:
        raise ValueError(f"non-public aggregate URL rejected: {url}")


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        original_host = urllib.parse.urlparse(req.full_url).hostname or ""
        _validate_public_http_url(newurl, expected_host=original_host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicHttpFetcher:
    def __init__(
        self,
        *,
        timeout: float = 20,
        max_bytes: int = 5_000_000,
        user_agent: str = "HT-Lead-Radar/0.3 (+public aggregate monitoring)",
        urlopen: Callable[..., Any] | None = None,
        minimum_interval_seconds: float = 1.25,
        shared_get_urls: Iterable[str] = (),
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.urlopen = (
            urlopen or urllib.request.build_opener(_SameHostRedirectHandler()).open
        )
        self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
        self._last_fetch_at = 0.0
        self.shared_get_urls = frozenset(shared_get_urls)
        for url in self.shared_get_urls:
            _validate_public_http_url(url)
        # Shared listing URLs are a finite, registry-derived set. Keep their
        # exact response for this fetcher's collection cycle so mutually
        # exclusive source projections always inspect the same snapshot.
        # SourcePackCollector creates a fresh fetcher for every new run.
        self._get_cache: dict[str, bytes] = {}

    def clear_shared_cache(self) -> None:
        """Start a new explicit shared-listing snapshot cycle."""

        self._get_cache.clear()

    def __call__(self, url: str) -> bytes:
        return self._request(url)

    def post_json(self, url: str, payload: dict[str, Any]) -> bytes:
        return self._request(
            url,
            method="POST",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            accept="application/json,text/plain,*/*",
        )

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str = "",
        accept: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    ) -> bytes:
        _validate_public_http_url(url)
        if method == "GET" and body is None and url in self.shared_get_urls:
            cached = self._get_cache.get(url)
            if cached is not None:
                return cached
        now = time.monotonic()
        elapsed = now - self._last_fetch_at
        if self._last_fetch_at and elapsed < self.minimum_interval_seconds:
            time.sleep(self.minimum_interval_seconds - elapsed)
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        with self.urlopen(request, timeout=self.timeout) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else url
            _validate_public_http_url(
                str(final_url),
                expected_host=urllib.parse.urlparse(url).hostname or "",
            )
            body = read_response_body(
                response,
                max_bytes=self.max_bytes,
                timeout=self.timeout,
            )
            self._last_fetch_at = time.monotonic()
            if method == "GET" and url in self.shared_get_urls:
                self._get_cache[url] = body
            return body


class DedicatedAggregateCoordinator:
    def __init__(
        self,
        *,
        state_db: str | Path,
        registry: DedicatedAdapterRegistry | None = None,
        fetch: Callable[[str], bytes] | None = None,
        llm_runner: PromptRunner | None = None,
        now: datetime | None = None,
        acceptance_dir: str | Path | None = None,
        semantic_workers: int | None = None,
        strict_claim_contract: bool | None = None,
        claim_centric_v27: bool | None = None,
        capture_full_visible_window: bool = False,
        source_timeout_seconds: float | None = None,
    ) -> None:
        self.state_db = Path(state_db)
        self.registry = registry or DedicatedAdapterRegistry.defaults()
        self.fetch = fetch or PublicHttpFetcher()
        if strict_claim_contract is None:
            strict_claim_contract = os.environ.get(
                "LEAD_RADAR_AGGREGATE_STRICT_CLAIMS",
                "0",
            ).strip().lower() in {"1", "true", "yes", "on"}
        self.strict_claim_contract = strict_claim_contract
        if claim_centric_v27 is None:
            claim_centric_v27 = os.environ.get(
                "LEAD_RADAR_AGGREGATE_CLAIM_CENTRIC_V27",
                "0",
            ).strip().lower() in {"1", "true", "yes", "on"}
        self.claim_centric_v27 = claim_centric_v27
        self.processor = MiniMaxSemanticProcessor(
            llm_runner,
            strict_claim_contract=strict_claim_contract,
            claim_centric_v27=claim_centric_v27,
        )
        self._llm_runner = llm_runner
        self.semantic_workers = self._worker_count(semantic_workers, llm_runner)
        self.now = now or datetime.now(timezone.utc)
        self.acceptance_dir = Path(acceptance_dir) if acceptance_dir else None
        self.capture_full_visible_window = capture_full_visible_window
        self.reuse_stale_semantics = self._env_bool(
            "LEAD_RADAR_AGGREGATE_REUSE_STALE_SEMANTICS",
            default=True,
        )
        if source_timeout_seconds is None:
            raw_source_timeout = os.environ.get(
                "LEAD_RADAR_AGGREGATE_SOURCE_TIMEOUT_SECONDS",
                "900",
            )
            try:
                source_timeout_seconds = float(raw_source_timeout)
            except (TypeError, ValueError):
                source_timeout_seconds = 900.0
        if not math.isfinite(source_timeout_seconds) or source_timeout_seconds <= 0:
            raise ValueError("source_timeout_seconds must be positive")
        self.source_timeout_seconds = source_timeout_seconds

    def collect_source(
        self,
        source_id: str,
        topic: str,
        *,
        force_reprocess: bool = False,
    ) -> DedicatedCollectionResult:
        adapter = self.registry.for_source(source_id)
        if adapter is None:
            raise KeyError(f"no dedicated adapter for {source_id}")
        channel = adapter.channel_for(source_id)
        started = self.now.replace(microsecond=0).isoformat()
        context = AdapterContext.create(
            state_db=self.state_db,
            fetch=lambda url: self._fetch_adapter_page(source_id, url),
            post_json=self._post_adapter_json(source_id),
            record_decision=self._record_adapter_decision(source_id),
            now=self.now,
            decision_state={
                "capture_full_visible_window": {
                    "enabled": self.capture_full_visible_window,
                }
            },
        )
        listing_count = incremental_count = detail_success_count = 0
        detail_failure_count = rule_event_count = minimax_event_count = 0
        adaptive_used_count = 0
        prefiltered_count = 0
        semantic_failure_count = 0
        omissions_detected = 0
        evidence: list[Evidence] = []
        semantic_work: list[_SemanticWork] = []
        status = "ok"
        error = ""
        source_deadline = time.monotonic() + self.source_timeout_seconds

        current_stage = "listing"

        def check_source_deadline(stage: str) -> None:
            nonlocal current_stage
            current_stage = stage
            if time.monotonic() > source_deadline:
                raise TimeoutError(
                    f"source {source_id} exceeded {self.source_timeout_seconds:g}s "
                    f"watchdog during {stage}"
                )

        with (
            ThreadPoolExecutor(
                max_workers=self.semantic_workers,
                thread_name_prefix="aggregate-minimax",
            ) as executor,
            AggregateStateStore(self.state_db) as store,
        ):
            try:
                check_source_deadline("listing_fetch")
                listing_html = self.fetch(channel.url)
                check_source_deadline("listing_parse")
                self._write_raw(source_id, "listing", listing_html)
                indexes = adapter.parse_listing(channel, listing_html, context)
                for resolved_stage in ("listing", "source_watchdog"):
                    store.resolve_dead_letter(
                        source_id=source_id,
                        source_article_id="",
                        stage=resolved_stage,
                    )
                listing_count = len(indexes)
                listed_ids = {item.source_article_id for item in indexes}
                recovery_indexes = [
                    item
                    for item in store.open_dead_letter_indexes(source_id)
                    if item.source_article_id not in listed_ids
                ]
                processing_indexes = [*indexes, *recovery_indexes]
                for index in processing_indexes:
                    check_source_deadline(
                        f"detail_fetch:{index.source_article_id}"
                    )
                    changed = store.upsert_index(index)
                    if (
                        not changed
                        and store.article_is_current(index, now=self.now)
                        and store.semantic_is_current(
                            index,
                            prompt_version=self.processor.semantic_prompt_version,
                            model_identity=self.processor.model_identity,
                            claim_centric_v27=self.processor.claim_centric_v27
                            and self.processor.runner is not None,
                            strict_claim_contract=self.strict_claim_contract,
                        )
                        and not store.has_open_dead_letter(
                            source_id=source_id,
                            source_article_id=index.source_article_id,
                        )
                        and not force_reprocess
                    ):
                        evidence.extend(
                            self._events_to_evidence(
                                store.events_for_article(
                                    source_id,
                                    index.source_article_id,
                                ),
                                channel.name,
                                channel.source_grade,
                                topic,
                            )
                        )
                        continue
                    # A prompt-version migration must not turn the daily
                    # incremental job into a historical MiniMax backfill. If
                    # the listing/article identity is unchanged and a prior
                    # semantic attempt exists, retain its grounded events.
                    # New or changed articles still use the current prompt;
                    # --refresh remains the explicit escape hatch for a full
                    # reprocessing run.
                    if (
                        self.reuse_stale_semantics
                        and not force_reprocess
                        and not store.has_open_dead_letter(
                            source_id=source_id,
                            source_article_id=index.source_article_id,
                        )
                        # Ignore the short freshness/recheck window here. The
                        # listing content hash is unchanged, and a prior
                        # successful semantic result means this detail page
                        # does not belong in today's incremental work set.
                        and store.article_is_current(
                            index,
                            now=self.now,
                            overlap_hours=0,
                        )
                        and not store.semantic_is_current(
                            index,
                            prompt_version=self.processor.semantic_prompt_version,
                            model_identity=self.processor.model_identity,
                            claim_centric_v27=self.processor.claim_centric_v27
                            and self.processor.runner is not None,
                            strict_claim_contract=self.strict_claim_contract,
                        )
                        and store.has_prior_semantic_attempt(index)
                    ):
                        evidence.extend(
                            self._events_to_evidence(
                                store.events_for_article(
                                    source_id,
                                    index.source_article_id,
                                    content_hash=store.article_content_hash(
                                        source_id,
                                        index.source_article_id,
                                    ),
                                ),
                                channel.name,
                                channel.source_grade,
                                topic,
                            )
                        )
                        continue
                    incremental_count += 1
                    try:
                        if not adapter.should_fetch_detail(channel, index):
                            prefiltered_count += 1
                            article = self._prefiltered_article(index)
                            store.store_article(article)
                            semantic_events: list[SemanticEvent] = []
                            semantic_audit = self.processor.prefiltered_audit(
                                article,
                                reason="adapter_listing_router_rejected",
                            )
                            store.store_semantic_audit(semantic_audit)
                            store.store_events(
                                source_id,
                                index.source_article_id,
                                semantic_events,
                            )
                            for resolved_stage in (
                                "detail_fetch",
                                "semantic_validation",
                                "detail_or_semantic",
                            ):
                                store.resolve_dead_letter(
                                    source_id=source_id,
                                    source_article_id=index.source_article_id,
                                    stage=resolved_stage,
                                )
                            self._write_semantic(
                                source_id,
                                index.source_article_id,
                                article.to_dict(),
                                [],
                                semantic_audit,
                            )
                            continue
                        detail_html = adapter.fetch_detail(channel, index, context)
                        self._write_raw(
                            source_id,
                            f"detail-{index.source_article_id}",
                            detail_html,
                        )
                        article = adapter.parse_detail(
                            channel,
                            index,
                            detail_html,
                            context,
                        )
                        prior_article_hash = store.article_content_hash(
                            source_id,
                            index.source_article_id,
                        )
                        semantic_still_current = store.semantic_is_current(
                            index,
                            prompt_version=self.processor.semantic_prompt_version,
                            model_identity=self.processor.model_identity,
                            claim_centric_v27=self.processor.claim_centric_v27
                            and self.processor.runner is not None,
                            strict_claim_contract=self.strict_claim_contract,
                        )
                        store.store_article(article)
                        if article.fetch_status in {
                            "ok",
                            "structured_complete",
                            "listing_complete",
                        }:
                            detail_success_count += 1
                            store.resolve_dead_letter(
                                source_id=source_id,
                                source_article_id=index.source_article_id,
                                stage="detail_fetch",
                            )
                        else:
                            detail_failure_count += 1
                            store.record_dead_letter(
                                source_id=source_id,
                                source_article_id=index.source_article_id,
                                canonical_url=index.canonical_url,
                                stage="detail_fetch",
                                error=(
                                    "detail unavailable; processed auditable "
                                    "listing title/summary fallback: "
                                    f"{article.failure_reason}"
                                ),
                            )
                        adaptive_used_count += int(
                            article.extraction_method == "adaptive"
                            or index.discovery_method == "adaptive"
                        )
                        if (
                            prior_article_hash == article.content_hash
                            and semantic_still_current
                            and not store.has_open_dead_letter(
                                source_id=source_id,
                                source_article_id=index.source_article_id,
                            )
                            and not force_reprocess
                        ):
                            cached_events = store.events_for_article(
                                source_id,
                                index.source_article_id,
                                content_hash=article.content_hash,
                            )
                            evidence.extend(
                                self._events_to_evidence(
                                    cached_events,
                                    channel.name,
                                    channel.source_grade,
                                    topic,
                                )
                            )
                            continue
                        rule_events = adapter.rule_events(channel, article)
                        rule_event_count += len(rule_events)
                        semantic_work.append(
                            _SemanticWork(
                                index=index,
                                article=article,
                                future=executor.submit(
                                    self._process_semantic,
                                    channel,
                                    article,
                                    rule_events,
                                ),
                            )
                        )
                    except Exception as exc:
                        detail_failure_count += 1
                        store.record_dead_letter(
                            source_id=source_id,
                            source_article_id=index.source_article_id,
                            canonical_url=index.canonical_url,
                            stage="detail_or_semantic",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                # Persist on the main thread in listing order. SQLite never
                # crosses worker boundaries, so the audit trail is deterministic.
                for work in semantic_work:
                    check_source_deadline(
                        f"semantic_result:{work.index.source_article_id}"
                    )
                    index = work.index
                    article = work.article
                    try:
                        semantic_events, semantic_audit = work.future.result()
                        omissions_detected += int(
                            semantic_audit.get("omissions_detected") or 0
                        )
                        store.store_semantic_audit(semantic_audit)
                        store.sync_semantic_claim_dead_letters(
                            source_id=source_id,
                            source_article_id=index.source_article_id,
                            canonical_url=index.canonical_url,
                            failed_claim_ids=list(
                                (
                                    semantic_audit.get("model_unadjudicated_claim_ids")
                                    if self.strict_claim_contract
                                    else semantic_audit.get("unmapped_candidate_ids")
                                )
                                or semantic_audit.get("failed_claim_ids")
                                or []
                            ),
                            error=str(semantic_audit.get("error") or ""),
                        )
                        if semantic_audit.get("status") in {
                            "fallback_to_rules",
                            "partial",
                            "repaired_partial",
                        }:
                            semantic_failure_count += 1
                            store.record_dead_letter(
                                source_id=source_id,
                                source_article_id=index.source_article_id,
                                canonical_url=index.canonical_url,
                                stage="semantic_validation",
                                error=str(semantic_audit.get("error") or "")[:2000],
                            )
                        else:
                            store.resolve_dead_letter(
                                source_id=source_id,
                                source_article_id=index.source_article_id,
                                stage="semantic_validation",
                            )
                        minimax_event_count += sum(
                            event.processor == "minimax" for event in semantic_events
                        )
                        store.store_events(
                            source_id,
                            index.source_article_id,
                            semantic_events,
                        )
                        store.resolve_dead_letter(
                            source_id=source_id,
                            source_article_id=index.source_article_id,
                            stage="detail_or_semantic",
                        )
                        evidence.extend(
                            self._events_to_evidence(
                                semantic_events,
                                channel.name,
                                channel.source_grade,
                                topic,
                            )
                        )
                        self._write_semantic(
                            source_id,
                            index.source_article_id,
                            article.to_dict(),
                            [event.to_dict() for event in semantic_events],
                            semantic_audit,
                        )
                    except Exception as exc:
                        detail_failure_count += 1
                        store.record_dead_letter(
                            source_id=source_id,
                            source_article_id=index.source_article_id,
                            canonical_url=index.canonical_url,
                            stage="detail_or_semantic",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                listing_hash = sha256(listing_html).hexdigest()
                cursor = indexes[0].cursor_value if indexes else ""
                store.update_cursor(
                    source_id=source_id,
                    cursor_value=cursor,
                    listing_hash=listing_hash,
                    listing_count=listing_count,
                )
                if detail_failure_count or semantic_failure_count:
                    status = "partial"
            except Exception as exc:
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
                dead_letter_stage = (
                    "source_watchdog"
                    if isinstance(exc, TimeoutError)
                    and "watchdog" in str(exc).lower()
                    else current_stage
                )
                store.record_dead_letter(
                    source_id=source_id,
                    source_article_id="",
                    canonical_url=channel.url,
                    stage=dead_letter_stage,
                    error=error,
                )
            run = AdapterRun(
                adapter_id=adapter.adapter_id,
                source_id=source_id,
                started_at=started,
                finished_at=max(
                    self.now,
                    datetime.now(timezone.utc),
                )
                .replace(microsecond=0)
                .isoformat(),
                status=status,
                listing_count=listing_count,
                incremental_count=incremental_count,
                detail_success_count=detail_success_count,
                detail_failure_count=detail_failure_count,
                rule_event_count=rule_event_count,
                minimax_event_count=minimax_event_count,
                evidence_count=len(evidence),
                adaptive_used_count=adaptive_used_count,
                semantic_failure_count=semantic_failure_count,
                prefiltered_count=prefiltered_count,
                omissions_detected=omissions_detected,
                error=error,
            )
            store.record_run(run)
        return DedicatedCollectionResult(tuple(evidence), run)

    @staticmethod
    def _worker_count(
        value: int | None,
        runner: PromptRunner | None,
    ) -> int:
        if runner is None:
            return 1
        if value is None:
            raw = os.environ.get("LEAD_RADAR_AGGREGATE_LLM_WORKERS", "4")
            try:
                value = int(raw)
            except ValueError:
                value = 4
        return max(1, min(value, 8))

    @staticmethod
    def _env_bool(name: str, *, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _process_semantic(
        self,
        channel: SourceChannel,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
    ) -> tuple[list[SemanticEvent], dict[str, Any]]:
        processor = MiniMaxSemanticProcessor(
            self._llm_runner,
            strict_claim_contract=self.strict_claim_contract,
            claim_centric_v27=self.claim_centric_v27,
        )
        events = processor.process(channel, article, rule_events)
        return events, dict(processor.last_audit)

    @staticmethod
    def _prefiltered_article(index: SourceArticleIndex) -> CleanArticle:
        body = index.summary or index.title
        return CleanArticle(
            index=index,
            clean_body=body,
            structured_data={
                **index.structured_data,
                "prefilter_reason": "adapter_listing_router_rejected",
            },
            extraction_method="listing-prefilter",
            evidence_locators={
                "body": "listing:complete-text",
                "routing": "adapter.should_fetch_detail",
            },
            fetch_status="prefiltered",
            failure_reason="",
            content_hash=sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest(),
        )

    def health(self) -> dict[str, Any]:
        with AggregateStateStore(self.state_db) as store:
            return store.health()

    @staticmethod
    def _events_to_evidence(
        events: list[SemanticEvent],
        source_name: str,
        source_grade: str,
        topic: str,
    ) -> list[Evidence]:
        accepted = [
            event
            for event in events
            if event.event_type != "other"
            and event.canonical_company
            and event.evidence_quotes
        ]
        output: list[Evidence] = []
        for event in accepted:
            document_id = sha256(
                f"{event.source_id}|{event.canonical_url}".encode("utf-8")
            ).hexdigest()
            event_id = sha256(
                (
                    f"{document_id}|{event.canonical_company}|"
                    f"{event.event_type}|{event.event_date}"
                    f"|{event.funding_round}|{event.event_status}"
                ).encode("utf-8")
            ).hexdigest()
            output.append(
                Evidence(
                    company=event.canonical_company,
                    event_type=event.event_type,
                    phase=event.phase,
                    event_date=event.event_date,
                    title=event.event_summary or event.evidence_quotes[0][:180],
                    snippet=event.evidence_quotes[0][:500],
                    source_url=event.canonical_url,
                    source_name=f"{source_name} [{event.source_id}]",
                    source_grade=source_grade,
                    direction=topic,
                    document_id=document_id,
                    event_id=event_id,
                    independent_source_group=urllib.parse.urlparse(
                        event.canonical_url
                    ).netloc.lower(),
                    content_sha256=event.content_hash,
                    source_excerpt=event.evidence_quotes[0][:500],
                    source_locator="aggregate_semantic_events",
                    analyst_note="; ".join(event.ambiguities),
                    organizations=event.investors,
                    event_slots={
                        "funding_round": event.funding_round,
                        "funding_amount": event.funding_amount,
                        "cumulative_funding_amount": event.cumulative_funding_amount,
                        "event_status": event.event_status,
                    },
                    source_kind="aggregate_media",
                    source_id=event.source_id,
                    industry_tags=event.industry_tags,
                )
            )
        return output

    def _fetch_adapter_page(self, source_id: str, url: str) -> bytes:
        payload = self.fetch(url)
        digest = sha256(url.encode("utf-8")).hexdigest()[:16]
        self._write_raw(source_id, f"adapter-fetch-{digest}", payload)
        return payload

    def _post_adapter_json(
        self,
        source_id: str,
    ) -> Callable[[str, dict[str, Any]], bytes] | None:
        post_json = getattr(self.fetch, "post_json", None)
        if not callable(post_json):
            return None

        def fetch(url: str, payload: dict[str, Any]) -> bytes:
            body = post_json(url, payload)
            request_key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            digest = sha256(f"{url}|{request_key}".encode("utf-8")).hexdigest()[:16]
            self._write_raw(source_id, f"adapter-post-{digest}", body)
            return body

        return fetch

    def _write_raw(self, source_id: str, label: str, payload: bytes) -> None:
        if self.acceptance_dir is None:
            return
        target = self.acceptance_dir / source_id
        target.mkdir(parents=True, exist_ok=True)
        digest = sha256(payload).hexdigest()[:16]
        immutable = target / f"{label}-{digest}.html"
        if not immutable.exists():
            immutable.write_bytes(payload)
        (target / f"{label}.html").write_bytes(payload)

    def _record_adapter_decision(
        self,
        source_id: str,
    ) -> Callable[[str, dict[str, Any]], None] | None:
        if self.acceptance_dir is None:
            return None

        def record(label: str, decision: dict[str, Any]) -> None:
            payload = json.dumps(
                decision,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            self._write_raw(source_id, f"decision-{label}", payload)

        return record

    def _write_semantic(
        self,
        source_id: str,
        article_id: str,
        article: dict[str, Any],
        events: list[dict[str, Any]],
        minimax_audit: dict[str, Any],
    ) -> None:
        if self.acceptance_dir is None:
            return
        target = self.acceptance_dir / source_id
        target.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"article": article, "events": events, "minimax_audit": minimax_audit},
            ensure_ascii=False,
            indent=2,
        )
        digest = sha256(payload.encode("utf-8")).hexdigest()[:16]
        immutable = target / f"semantic-{article_id}-{digest}.json"
        if not immutable.exists():
            immutable.write_text(payload, encoding="utf-8")
        (target / f"semantic-{article_id}.json").write_text(
            payload,
            encoding="utf-8",
        )
