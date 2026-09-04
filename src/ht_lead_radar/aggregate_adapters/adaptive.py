"""Controlled Scrapling-based selector fallback.

Adaptive matching is only a DOM relocation aid.  Callers must validate the
returned values with source-specific business invariants before accepting them.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
from typing import Any

from scrapling import Selector
from scrapling.core.storage import SQLiteStorageSystem


# Keep the opt-in adaptive fallback's SQLite working set bounded.  Production
# daily runs disable this fallback in the launcher, so ordinary collection
# never opens adaptive storage.
_ADAPTIVE_STORAGE_CACHE_SIZE = 8


def _adaptive_enabled() -> bool:
    value = os.environ.get("LEAD_RADAR_ADAPTIVE_SELECTORS", "1")
    return value.strip().lower() not in {"0", "false", "no", "off"}


class _BoundedSQLiteStorageSystemBase(SQLiteStorageSystem.__wrapped__):
    pass


@lru_cache(maxsize=_ADAPTIVE_STORAGE_CACHE_SIZE, typed=True)
class _BoundedSQLiteStorageSystem(_BoundedSQLiteStorageSystemBase):
    pass


@dataclass(frozen=True)
class Selection:
    elements: tuple[Any, ...]
    method: str
    similarity_threshold: int | None


class AdaptiveSelector:
    def __init__(
        self,
        html: bytes | str,
        *,
        url: str,
        storage_path: str | Path,
        encoding: str = "utf-8",
        minimum_similarity: int = 72,
    ) -> None:
        storage_file = Path(storage_path)
        storage_file.parent.mkdir(parents=True, exist_ok=True)
        self._adaptive_enabled = _adaptive_enabled()
        selector_kwargs = {
            "url": url,
            "encoding": encoding,
            "adaptive": self._adaptive_enabled,
        }
        if self._adaptive_enabled:
            selector_kwargs.update(
                storage=_BoundedSQLiteStorageSystem,
                storage_args={
                    "storage_file": str(storage_file),
                    "url": url,
                },
            )
        self._selector = Selector(html, **selector_kwargs)
        self.minimum_similarity = minimum_similarity

    @property
    def selector(self) -> Selector:
        return self._selector

    def css(
        self,
        exact_selector: str,
        *,
        identifier: str,
        minimum_count: int = 1,
        maximum_count: int | None = None,
    ) -> Selection:
        # Scrapling warns for every selector call when ``auto_save`` is sent
        # while adaptive mode is disabled.  Production deliberately disables
        # adaptive storage, so keep the ordinary CSS path completely ordinary
        # instead of paying the warning/logging overhead hundreds of times.
        exact_options: dict[str, Any] = {}
        if self._adaptive_enabled:
            exact_options.update(identifier=identifier, auto_save=True)
        exact = tuple(self._selector.css(exact_selector, **exact_options))
        if self._count_valid(exact, minimum_count, maximum_count):
            return Selection(exact, "exact", None)
        if not self._adaptive_enabled:
            return Selection((), "failed", self.minimum_similarity)
        adaptive = tuple(
            self._selector.css(
                exact_selector,
                identifier=identifier,
                adaptive=True,
                percentage=self.minimum_similarity,
            )
        )
        if not self._count_valid(adaptive, minimum_count, maximum_count):
            return Selection((), "failed", self.minimum_similarity)
        return Selection(adaptive, "adaptive", self.minimum_similarity)

    @staticmethod
    def _count_valid(
        elements: tuple[Any, ...],
        minimum_count: int,
        maximum_count: int | None,
    ) -> bool:
        if len(elements) < minimum_count:
            return False
        return maximum_count is None or len(elements) <= maximum_count
