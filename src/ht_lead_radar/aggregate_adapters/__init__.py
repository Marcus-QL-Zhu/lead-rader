"""Dedicated adapters for high-value aggregate news sources."""

from .base import (
    AdapterContext,
    AggregateAdapter,
    AggregateAdapterError,
    DetailFetchError,
    ListingInvariantError,
)
from .models import (
    AdapterRun,
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from .registry import DedicatedAdapterRegistry

__all__ = [
    "AdapterContext",
    "AdapterRun",
    "AggregateAdapter",
    "AggregateAdapterError",
    "CleanArticle",
    "DedicatedAdapterRegistry",
    "DetailFetchError",
    "ListingInvariantError",
    "SemanticEvent",
    "SourceArticleIndex",
    "SourceChannel",
]
