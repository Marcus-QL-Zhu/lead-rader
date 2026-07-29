"""Validated source-pack registry for generic and sector opportunity scans.

The registry describes public discovery entrances only.  This module performs
no network access and deliberately exposes no undocumented site API.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "source-packs.json"

ALLOWED_ADAPTERS = frozenset({
    "direct_html",
    "rss",
    "json_feed",
    "html_list",
    "html_homepage_list",
    "browser_dynamic_list",
    "browser_search",
    "dedicated_disclosure_adapter",
    "changedetection",
})

ENABLED_STATUSES = frozenset({
    "verified_static_list",
    "verified_public_listing",
})
PROHIBITED_DAILY_SOURCE_TYPES = frozenset({"company_official"})


class SourcePackError(ValueError):
    """Raised when the registry would produce an unsafe or ambiguous plan."""


def _primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_primitive(item) for item in value]
    return value


@dataclass(frozen=True)
class SourceDefinition:
    id: str
    name: str
    owner: str
    source_type: str
    grade: str
    url: str
    adapter: str
    signal_types: tuple[str, ...]
    industry_tags: tuple[str, ...]
    enabled: bool
    verified_on: str
    status: str
    verification_note: str

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class SourcePack:
    id: str
    name: str
    aliases: tuple[str, ...]
    industry_tags: tuple[str, ...]
    source_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


@dataclass(frozen=True)
class SourceSelection:
    topic: str
    pack_ids: tuple[str, ...]
    sources: tuple[SourceDefinition, ...]
    disabled_sources: tuple[SourceDefinition, ...]
    unmatched_topic: bool

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


def _as_nonempty_tuple(value: Any, field_name: str, item_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise SourcePackError(f"{item_id}.{field_name} must be a non-empty list")
    cleaned = tuple(str(item).strip() for item in value if str(item).strip())
    if not cleaned:
        raise SourcePackError(f"{item_id}.{field_name} must contain non-empty strings")
    if len(set(cleaned)) != len(cleaned):
        raise SourcePackError(f"{item_id}.{field_name} contains duplicates")
    return cleaned


def _required_text(raw: Mapping[str, Any], field_name: str, item_id: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise SourcePackError(f"{item_id}.{field_name} must be a non-empty string")
    return value.strip()


def _validate_url(url: str, source_id: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SourcePackError(f"{source_id}.url must be a public http(s) URL")
    if parsed.username or parsed.password:
        raise SourcePackError(f"{source_id}.url must not embed credentials")


def _source_from_raw(raw: Mapping[str, Any]) -> SourceDefinition:
    source_id = _required_text(raw, "id", "<source>")
    adapter = _required_text(raw, "adapter", source_id)
    if adapter not in ALLOWED_ADAPTERS:
        raise SourcePackError(f"{source_id}.adapter is unsupported: {adapter}")
    url = _required_text(raw, "url", source_id)
    _validate_url(url, source_id)
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise SourcePackError(f"{source_id}.enabled must be boolean")
    status = _required_text(raw, "status", source_id)
    if enabled and status not in ENABLED_STATUSES:
        raise SourcePackError(
            f"{source_id} is enabled but status {status!r} is not an enabled verification status"
        )
    grade = _required_text(raw, "grade", source_id)
    if grade not in {"A", "B", "C"}:
        raise SourcePackError(f"{source_id}.grade must be A, B, or C")
    return SourceDefinition(
        id=source_id,
        name=_required_text(raw, "name", source_id),
        owner=_required_text(raw, "owner", source_id),
        source_type=_required_text(raw, "source_type", source_id),
        grade=grade,
        url=url,
        adapter=adapter,
        signal_types=_as_nonempty_tuple(raw.get("signal_types"), "signal_types", source_id),
        industry_tags=_as_nonempty_tuple(raw.get("industry_tags"), "industry_tags", source_id),
        enabled=enabled,
        verified_on=_required_text(raw, "verified_on", source_id),
        status=status,
        verification_note=_required_text(raw, "verification_note", source_id),
    )


def _pack_from_raw(raw: Mapping[str, Any]) -> SourcePack:
    pack_id = _required_text(raw, "id", "<pack>")
    return SourcePack(
        id=pack_id,
        name=_required_text(raw, "name", pack_id),
        aliases=_as_nonempty_tuple(raw.get("aliases"), "aliases", pack_id),
        industry_tags=_as_nonempty_tuple(raw.get("industry_tags"), "industry_tags", pack_id),
        source_ids=_as_nonempty_tuple(raw.get("source_ids"), "source_ids", pack_id),
    )


class SourcePackRegistry:
    """Load, validate, and select reusable fixed-source packages."""

    def __init__(
        self,
        *,
        version: int,
        verified_on: str,
        policy: Mapping[str, Any],
        sources: Iterable[SourceDefinition],
        packs: Iterable[SourcePack],
    ):
        self.version = version
        self.verified_on = verified_on
        self.policy = dict(policy)
        self.sources = tuple(sources)
        self.packs = tuple(packs)
        self._sources_by_id = {source.id: source for source in self.sources}
        self._packs_by_id = {pack.id: pack for pack in self.packs}

    @classmethod
    def load(cls, path: str | Path = DEFAULT_REGISTRY_PATH) -> "SourcePackRegistry":
        registry_path = Path(path)
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SourcePackError(f"source-pack registry not found: {registry_path}") from exc
        except json.JSONDecodeError as exc:
            raise SourcePackError(f"invalid source-pack JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourcePackError("source-pack registry root must be an object")
        version = payload.get("version")
        if not isinstance(version, int) or version < 1:
            raise SourcePackError("registry.version must be a positive integer")
        verified_on = _required_text(payload, "verified_on", "registry")
        policy = payload.get("policy")
        if not isinstance(policy, dict):
            raise SourcePackError("registry.policy must be an object")
        raw_sources = payload.get("sources")
        raw_packs = payload.get("packs")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise SourcePackError("registry.sources must be a non-empty list")
        if not isinstance(raw_packs, list) or not raw_packs:
            raise SourcePackError("registry.packs must be a non-empty list")
        sources = tuple(_source_from_raw(raw) for raw in raw_sources)
        packs = tuple(_pack_from_raw(raw) for raw in raw_packs)
        registry = cls(
            version=version,
            verified_on=verified_on,
            policy=policy,
            sources=sources,
            packs=packs,
        )
        registry._validate_cross_references()
        return registry

    def _validate_cross_references(self) -> None:
        source_ids = [source.id for source in self.sources]
        pack_ids = [pack.id for pack in self.packs]
        if len(source_ids) != len(set(source_ids)):
            raise SourcePackError("duplicate source id")
        if len(pack_ids) != len(set(pack_ids)):
            raise SourcePackError("duplicate pack id")
        if "generic-cn" not in self._packs_by_id:
            raise SourcePackError("required generic-cn pack is missing")
        for pack in self.packs:
            missing = [source_id for source_id in pack.source_ids if source_id not in self._sources_by_id]
            if missing:
                raise SourcePackError(f"{pack.id} references unknown sources: {', '.join(missing)}")
            for source_id in pack.source_ids:
                source = self._sources_by_id[source_id]
                if (
                    pack.id != "generic-cn"
                    and not set(pack.industry_tags).intersection(source.industry_tags)
                ):
                    raise SourcePackError(
                        f"{pack.id} source {source_id} has no matching industry tag"
                    )

    def get_source(self, source_id: str) -> SourceDefinition:
        try:
            return self._sources_by_id[source_id]
        except KeyError as exc:
            raise SourcePackError(f"unknown source: {source_id}") from exc

    def get_pack(self, pack_id: str) -> SourcePack:
        try:
            return self._packs_by_id[pack_id]
        except KeyError as exc:
            raise SourcePackError(f"unknown source pack: {pack_id}") from exc

    def matching_pack_ids(self, topic: str) -> tuple[str, ...]:
        normalized = topic.strip().lower()
        matched = ["generic-cn"]
        scored: list[tuple[int, str]] = []
        for pack in self.packs:
            if pack.id == "generic-cn":
                continue
            alias_hits = [
                alias for alias in pack.aliases
                if alias.lower() in normalized or normalized in alias.lower()
            ]
            if alias_hits:
                scored.append((max(len(alias) for alias in alias_hits), pack.id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        matched.extend(pack_id for _, pack_id in scored)
        return tuple(matched)

    def select(
        self,
        topic: str,
        *,
        include_disabled: bool = False,
        signal_types: Iterable[str] | None = None,
    ) -> SourceSelection:
        if not topic or not topic.strip():
            raise SourcePackError("topic must not be empty")
        pack_ids = self.matching_pack_ids(topic)
        requested_signals = {item.strip() for item in (signal_types or ()) if item.strip()}
        selected: list[SourceDefinition] = []
        disabled: list[SourceDefinition] = []
        seen: set[str] = set()
        for pack_id in pack_ids:
            for source_id in self.get_pack(pack_id).source_ids:
                if source_id in seen:
                    continue
                seen.add(source_id)
                source = self.get_source(source_id)
                if requested_signals and not requested_signals.intersection(source.signal_types):
                    continue
                prohibited = source.source_type in PROHIBITED_DAILY_SOURCE_TYPES
                if prohibited:
                    disabled.append(source)
                elif source.enabled:
                    selected.append(source)
                else:
                    disabled.append(source)
                    if include_disabled:
                        selected.append(source)
        return SourceSelection(
            topic=topic.strip(),
            pack_ids=pack_ids,
            sources=tuple(selected),
            disabled_sources=tuple(disabled),
            unmatched_topic=len(pack_ids) == 1,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "verified_on": self.verified_on,
            "policy": _primitive(self.policy),
            "sources": _primitive(self.sources),
            "packs": _primitive(self.packs),
        }


def load_source_packs(path: str | Path = DEFAULT_REGISTRY_PATH) -> SourcePackRegistry:
    return SourcePackRegistry.load(path)


__all__ = [
    "ALLOWED_ADAPTERS",
    "DEFAULT_REGISTRY_PATH",
    "ENABLED_STATUSES",
    "PROHIBITED_DAILY_SOURCE_TYPES",
    "SourceDefinition",
    "SourcePack",
    "SourcePackError",
    "SourcePackRegistry",
    "SourceSelection",
    "load_source_packs",
]
