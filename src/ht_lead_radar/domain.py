"""Canonical fact-domain objects used by the lead radar.

The original radar used :class:`models.Evidence` as both a crawled document and
an extracted business event.  That is convenient for a prototype, but it makes
it impossible to distinguish ten articles about one financing round from ten
financing rounds.  This module deliberately separates the four layers:

``SourceDocument -> Statement -> BusinessEvent -> CanonicalEntity``.

The dataclasses are persistence-agnostic.  ``FactStore`` owns SQLite concerns,
while the helpers here provide deterministic identifiers and canonical hashes.
Only Python's standard library is used so the model can also run inside the
small OpenClaw deployment.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping

from .sanitization import sanitize_tree, sanitize_url


TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "spm",
        "from",
        "from_source",
        "ref",
        "referrer",
        "source",
    }
)


class EntityJudgement(str, Enum):
    """A reversible entity-resolution decision.

    These values mirror nomenklatura-style judgements.  A judgement is an
    assertion about two records; it never deletes or physically combines them.
    """

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    UNSURE = "UNSURE"
    NO_JUDGEMENT = "NO_JUDGEMENT"


class EventLifecycle(str, Enum):
    EMERGING = "emerging"
    CORROBORATED = "corroborated"
    DEVELOPING = "developing"
    STALE = "stale"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    DISPUTED = "disputed"


class EvidenceStance(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    RETRACTS = "retracts"


class EventLinkType(str, Enum):
    MERGE = "merge"
    SPLIT = "split"
    SUPERSEDES = "supersedes"


def sha256_text(value: str) -> str:
    """Return the exact UTF-8 SHA-256 hash of *value*.

    No whitespace or HTML normalization is applied.  A parser can therefore
    change without altering the historical identity of the source snapshot.
    """

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 32) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{sha256_text(material)[:length]}"


def normalize_url(url: str) -> str:
    """Canonicalize a URL conservatively for page-level deduplication.

    Host/scheme case, default ports, fragments, duplicate slashes and common
    tracking parameters are normalized.  Semantically meaningful query values
    are retained and sorted.  HTTP is not silently rewritten to HTTPS because
    some public registries still serve different resources on the two schemes.
    """

    value = sanitize_url(url)
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    trailing_slash = path.endswith("/")
    path = posixpath.normpath(path)
    if not path.startswith("/"):
        path = "/" + path
    if trailing_slash and path != "/":
        path += "/"
    # Percent-escape normalization without decoding reserved URL characters.
    path = urllib.parse.quote(urllib.parse.unquote(path), safe="/:@!$&'()*+,;=-._~")

    query_items: list[tuple[str, str]] = []
    for key, item_value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, item_value))
    query_items.sort(key=lambda item: (item[0], item[1]))
    query = urllib.parse.urlencode(query_items, doseq=True)
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def normalize_name(value: str) -> str:
    """Normalize an entity name for lookup, not for destructive merging."""

    text = unicodedata.normalize("NFKC", value or "").casefold().strip()
    text = re.sub(r"[\s\u3000·•・._\-—–/\\（）()【】\[\]「」“”\"'，,：:；;]+", "", text)
    return text


def normalize_slots(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Create a deterministic, JSON-safe representation of event slots."""

    def normalize(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key).strip(): normalize(val)
                for key, val in sorted(item.items(), key=lambda pair: str(pair[0]))
                if val is not None and val != ""
            }
        if isinstance(item, (list, tuple, set, frozenset)):
            normalized = [normalize(val) for val in item if val is not None and val != ""]
            return sorted(normalized, key=lambda val: canonical_json(val))
        if isinstance(item, str):
            return unicodedata.normalize("NFKC", re.sub(r"\s+", " ", item).strip())
        if isinstance(item, (int, float, bool)) or item is None:
            return item
        return str(item)

    normalized = normalize(value or {})
    return normalized if isinstance(normalized, dict) else {}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def normalize_timestamp(value: str | date | datetime | None) -> str | None:
    """Normalize a date/datetime to an ISO-8601 UTC string when possible."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                # Keep unusual source timestamps reproducible instead of
                # fabricating a date.  FactStore's date clustering will fall
                # back to observation time for these values.
                return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def grade_rank(grade: str) -> int:
    """Lower is better; unknown grades are deliberately last."""

    return {"A": 0, "B": 1, "C": 2, "D": 3, "X": 4}.get((grade or "").upper(), 9)


@dataclass(frozen=True)
class SourceDocument:
    id: str
    source_name: str
    source_url: str
    normalized_url: str
    url_hash: str
    content_hash: str
    title: str
    content: str
    source_grade: str = "B"
    source_record_id: str = ""
    published_at: str | None = None
    observed_at: str = field(default_factory=utcnow)
    language: str = "zh"
    independent_source_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    exact_duplicate_of_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        source_name: str,
        source_url: str,
        title: str,
        content: str,
        source_grade: str = "B",
        source_record_id: str = "",
        published_at: str | date | datetime | None = None,
        observed_at: str | date | datetime | None = None,
        language: str = "zh",
        independent_source_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> "SourceDocument":
        safe_source_url = sanitize_url(source_url)
        canonical_url = normalize_url(safe_source_url)
        url_hash = sha256_text(canonical_url)
        content_hash = sha256_text(content)
        document_id = stable_id("doc", source_name.casefold(), canonical_url, content_hash)
        if not independent_source_key:
            independent_source_key = (urllib.parse.urlsplit(canonical_url).hostname or source_name).lower()
        return cls(
            id=document_id,
            source_name=source_name,
            source_url=safe_source_url,
            normalized_url=canonical_url,
            url_hash=url_hash,
            content_hash=content_hash,
            title=title,
            content=content,
            source_grade=(source_grade or "B").upper(),
            source_record_id=source_record_id,
            published_at=normalize_timestamp(published_at),
            observed_at=normalize_timestamp(observed_at) or utcnow(),
            language=language,
            independent_source_key=independent_source_key.casefold().strip(),
            metadata=sanitize_tree(dict(metadata or {}), redact_pii=True),
        )


@dataclass(frozen=True)
class CanonicalEntity:
    id: str
    entity_type: str
    canonical_name: str
    normalized_name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def create(
        cls,
        entity_type: str,
        canonical_name: str,
        *,
        entity_key: str = "",
        attributes: Mapping[str, Any] | None = None,
    ) -> "CanonicalEntity":
        normalized = normalize_name(canonical_name)
        identity = entity_key.strip() or normalized
        return cls(
            id=stable_id("ent", entity_type.casefold(), identity),
            entity_type=entity_type.casefold().strip(),
            canonical_name=canonical_name.strip(),
            normalized_name=normalized,
            attributes=dict(attributes or {}),
        )


@dataclass(frozen=True)
class Statement:
    id: str
    document_id: str
    predicate: str
    subject_entity_id: str | None = None
    object_entity_id: str | None = None
    object_value: Any = None
    occurred_at: str | None = None
    confidence: float = 1.0
    quote: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def create(
        cls,
        *,
        document_id: str,
        predicate: str,
        subject_entity_id: str | None = None,
        object_entity_id: str | None = None,
        object_value: Any = None,
        occurred_at: str | date | datetime | None = None,
        confidence: float = 1.0,
        quote: str = "",
        slots: Mapping[str, Any] | None = None,
    ) -> "Statement":
        normalized_slots = normalize_slots(slots)
        normalized_time = normalize_timestamp(occurred_at)
        statement_id = stable_id(
            "stmt",
            document_id,
            predicate,
            subject_entity_id or "",
            object_entity_id or "",
            canonical_json(object_value),
            normalized_time or "",
            canonical_json(normalized_slots),
            quote,
        )
        return cls(
            id=statement_id,
            document_id=document_id,
            predicate=predicate.strip(),
            subject_entity_id=subject_entity_id,
            object_entity_id=object_entity_id,
            object_value=object_value,
            occurred_at=normalized_time,
            confidence=max(0.0, min(1.0, float(confidence))),
            quote=quote,
            slots=normalized_slots,
        )


@dataclass(frozen=True)
class EventEvidence:
    id: str
    event_id: str
    document_id: str
    statement_id: str | None
    stance: str
    independent_source_key: str
    source_grade: str
    linked_at: str


@dataclass(frozen=True)
class BusinessEvent:
    id: str
    company_entity_id: str
    event_type: str
    occurred_at: str
    time_bucket: str
    slots: dict[str, Any]
    slot_fingerprint: str
    lifecycle: str
    lifecycle_mode: str
    lifecycle_reason: str
    canonical_document_id: str | None
    independent_source_count: int
    first_observed_at: str
    last_observed_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class EntityResolutionDecision:
    id: int
    left_entity_id: str
    right_entity_id: str
    judgement: str
    reason: str
    actor: str
    created_at: str
    revoked_at: str | None


@dataclass(frozen=True)
class EventLinkDecision:
    id: int
    left_event_id: str
    right_event_id: str
    link_type: str
    judgement: str
    reason: str
    actor: str
    created_at: str
    revoked_at: str | None


@dataclass(frozen=True)
class IngestResult:
    document: SourceDocument
    entity: CanonicalEntity
    statement: Statement
    event: BusinessEvent
    created_document: bool
    created_entity: bool
    created_statement: bool
    created_event: bool
