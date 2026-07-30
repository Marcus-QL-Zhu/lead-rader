"""Controlled Scrapling-based selector fallback.

Adaptive matching is only a DOM relocation aid.  Callers must validate the
returned values with source-specific business invariants before accepting them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scrapling import Selector


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
        self._selector = Selector(
            html,
            url=url,
            encoding=encoding,
            adaptive=True,
            storage_args={"storage_file": str(storage_file), "url": url},
        )
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
        exact = tuple(
            self._selector.css(
                exact_selector,
                identifier=identifier,
                auto_save=True,
            )
        )
        if self._count_valid(exact, minimum_count, maximum_count):
            return Selection(exact, "exact", None)
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
