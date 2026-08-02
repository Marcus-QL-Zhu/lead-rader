"""Base interfaces and source-independent validation for dedicated adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlparse

from .models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


class AggregateAdapterError(RuntimeError):
    """Base failure for an aggregate-source adapter."""


class ListingInvariantError(AggregateAdapterError):
    """Raised when list parsing succeeds syntactically but violates invariants."""


class DetailFetchError(AggregateAdapterError):
    """Raised when an article detail cannot be safely extracted."""


@dataclass(frozen=True)
class AdapterContext:
    state_db: Path
    adaptive_db: Path
    now: datetime
    fetch: Callable[[str], bytes]
    post_json: Callable[[str, dict[str, Any]], bytes] | None = None
    record_decision: Callable[[str, dict[str, Any]], None] | None = None
    decision_state: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        state_db: str | Path,
        fetch: Callable[[str], bytes],
        post_json: Callable[[str, dict[str, Any]], bytes] | None = None,
        record_decision: Callable[[str, dict[str, Any]], None] | None = None,
        now: datetime | None = None,
        decision_state: dict[str, dict[str, Any]] | None = None,
    ) -> "AdapterContext":
        state_path = Path(state_db)
        return cls(
            state_db=state_path,
            adaptive_db=state_path.with_name(
                f"{state_path.stem}-adaptive-selectors.sqlite3"
            ),
            now=now or datetime.now(timezone.utc),
            fetch=fetch,
            post_json=post_json,
            record_decision=record_decision,
            decision_state=dict(decision_state or {}),
        )

    @property
    def capture_full_visible_window(self) -> bool:
        return bool(
            self.decision_state.get("capture_full_visible_window", {}).get(
                "enabled"
            )
        )


class AggregateAdapter(ABC):
    adapter_id = ""
    channels: tuple[SourceChannel, ...] = ()
    minimum_listing_count = 1
    maximum_listing_count = 500

    def channel_for(self, source_id: str) -> SourceChannel:
        for channel in self.channels:
            if channel.source_id == source_id:
                return channel
        raise AggregateAdapterError(
            f"{self.adapter_id} does not own source {source_id}"
        )

    @abstractmethod
    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        """Return every public article displayed in the fetched list window."""

    @abstractmethod
    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        """Extract only article facts, excluding navigation/recommendation noise."""

    @abstractmethod
    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        """Return evidence-bound deterministic event hypotheses."""

    def should_fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
    ) -> bool:
        """Return whether an indexed item needs detail and semantic processing.

        The default is deliberately conservative: ordinary aggregate sources
        process every indexed item. High-frequency streams may override this
        only when the listing payload already contains the complete item text
        and a broad deterministic event router can safely reject noise.
        Rejected indexes are still persisted and audited by the coordinator.
        """

        del channel, index
        return True

    def fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        context: AdapterContext,
    ) -> bytes:
        """Fetch detail through the adapter's preferred auditable path."""

        del channel
        return context.fetch(index.canonical_url)

    def validate_listing(
        self,
        channel: SourceChannel,
        articles: list[SourceArticleIndex],
    ) -> None:
        if (
            not self.minimum_listing_count
            <= len(articles)
            <= self.maximum_listing_count
        ):
            raise ListingInvariantError(
                f"{channel.source_id} listing count {len(articles)} outside "
                f"{self.minimum_listing_count}..{self.maximum_listing_count}"
            )
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        for article in articles:
            if not article.source_article_id or article.source_article_id in seen_ids:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate/empty article id"
                )
            if article.canonical_url in seen_urls:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate canonical URL"
                )
            if not 4 <= len(article.title.strip()) <= 300:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid title: {article.title!r}"
                )
            parsed = urlparse(article.canonical_url)
            if parsed.scheme != "https" or parsed.hostname not in channel.allowed_hosts:
                raise ListingInvariantError(
                    f"{channel.source_id} rejected URL: {article.canonical_url}"
                )
            if channel.allowed_path_patterns and not any(
                re.fullmatch(pattern, parsed.path)
                for pattern in channel.allowed_path_patterns
            ):
                raise ListingInvariantError(
                    f"{channel.source_id} rejected path: {parsed.path}"
                )
            seen_ids.add(article.source_article_id)
            seen_urls.add(article.canonical_url)

    @staticmethod
    def stable_hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()
