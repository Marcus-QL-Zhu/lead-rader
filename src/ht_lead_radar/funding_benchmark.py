"""Deterministic funding-source coverage benchmark.

The benchmark samples announced funding events from several fixed public
lists, then spends a bounded number of Metaso searches to assemble an
independent investor evidence corpus.  It does not treat Metaso snippets as a
database truth; the generated JSON is designed for a human-reviewed audit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import random
import re
from typing import Iterable

from .collectors import MetasoCollector, SearchResult, load_env_file
from .costs import METASO_CONSERVATIVE_POINTS_PER_SEARCH, SearchBudgetLedger
from .source_pack_collector import DiscoveredDocument, SourcePackCollector
from .source_packs import SourceDefinition, SourcePackRegistry


DEFAULT_SOURCE_IDS = (
    "36kr-financing-flash",
    "pedaily-vcpe-events",
    "cyzone-latest",
    "lieyunpro-archives",
    "vbdata-funding",
)

_POSITIVE = re.compile(
    r"(?:完成|获|斩获|拿下|再获|首发|宣布|raises?|raised).{0,36}"
    r"(?:融资|投资|funding|million|billion|\$\d+(?:\.\d+)?[MBK])|"
    r"(?:融资|funding).{0,36}(?:轮|美元|人民币|亿元|万元)",
    re.I,
)
_NEGATIVE = re.compile(
    r"未融资|融资净买入|融资余额|融资融券|股票|股息|月报|周报|盘点|"
    r"基金募资|基金完成|母基金|LP份额|债券融资|再融资|融资租赁",
    re.I,
)
_NAVIGATION = re.compile(r"^(?:融资|投融资|融资快报|融资事件|首页|更多)$")


@dataclass(frozen=True)
class FundingCandidate:
    title: str
    url: str
    source_id: str
    source_name: str
    published_at: str


def is_announced_funding_title(title: str, url: str = "") -> bool:
    compact = re.sub(r"\s+", " ", title).strip()
    return bool(
        len(compact) >= 10
        and not _NAVIGATION.fullmatch(compact)
        and not _NEGATIVE.search(compact)
        and _POSITIVE.search(compact)
        and "/project/" not in url
    )


def canonical_title(title: str) -> str:
    return re.sub(r"[\W_]+", "", title, flags=re.UNICODE).casefold()


def candidates_from_documents(
    source: SourceDefinition,
    documents: Iterable[DiscoveredDocument],
) -> list[FundingCandidate]:
    output: list[FundingCandidate] = []
    seen: set[str] = set()
    for document in documents:
        if not is_announced_funding_title(document.title, document.url):
            continue
        key = canonical_title(document.title)
        if key in seen:
            continue
        seen.add(key)
        output.append(FundingCandidate(
            title=document.title,
            url=document.url,
            source_id=source.id,
            source_name=source.name,
            published_at=document.published_at,
        ))
    return output


def deterministic_sample(
    candidates: Iterable[FundingCandidate],
    *,
    size: int = 10,
    seed: int = 20260725,
) -> list[FundingCandidate]:
    unique: dict[str, FundingCandidate] = {}
    for candidate in candidates:
        unique.setdefault(canonical_title(candidate.title), candidate)
    ordered = sorted(unique.values(), key=lambda item: (item.source_id, item.title, item.url))
    if len(ordered) < size:
        raise ValueError(f"need at least {size} funding candidates, found {len(ordered)}")
    return random.Random(seed).sample(ordered, size)


def metaso_query(candidate: FundingCandidate) -> str:
    return f"{candidate.title} 投资方 领投 跟投 官方 融资"


def _result_payload(result: SearchResult) -> dict:
    return {
        "title": result.title,
        "url": result.url,
        "snippet": result.snippet,
        "published_at": result.published_at,
    }


def run_benchmark(
    *,
    registry_path: str | Path,
    env_file: str | Path,
    budget_db: str | Path,
    output_path: str | Path,
    seed: int = 20260725,
    sample_size: int = 10,
    configured_limit: int = 90,
    result_limit: int = 10,
    source_ids: tuple[str, ...] = DEFAULT_SOURCE_IDS,
) -> dict:
    registry = SourcePackRegistry.load(registry_path)
    sources = [registry.get_source(source_id) for source_id in source_ids]
    candidate_pool: list[FundingCandidate] = []
    with SourcePackCollector(
        registry=registry,
        state_db=":memory:",
        detail_fetch=False,
        timeout=30,
    ) as collector:
        for source in sources:
            fetched = collector._fetch(source, source.url, "融资")
            documents = collector._discover(source, fetched)
            candidate_pool.extend(candidates_from_documents(source, documents))
    sampled = deterministic_sample(candidate_pool, size=sample_size, seed=seed)

    env = load_env_file(env_file)
    api_key = env.get("METASO_API_KEY", "")
    if not api_key:
        raise RuntimeError("METASO_API_KEY is missing")
    metaso = MetasoCollector(
        api_key=api_key,
        base_url=env.get("METASO_BASE_URL", "https://metaso.cn"),
    )
    ledger = SearchBudgetLedger(budget_db)
    rows = []
    for candidate in sampled:
        query = metaso_query(candidate)
        operation_key = "funding-benchmark-" + sha256(query.encode("utf-8")).hexdigest()[:24]
        charged = ledger.charge(
            operation_key,
            METASO_CONSERVATIVE_POINTS_PER_SEARCH,
            configured_limit=configured_limit,
        )
        if not charged:
            raise RuntimeError(
                f"Metaso budget unavailable or operation already charged: {operation_key}"
            )
        results = metaso.search(query, limit=result_limit)
        rows.append({
            "candidate": asdict(candidate),
            "query": query,
            "metaso_results": [_result_payload(result) for result in results],
        })

    payload = {
        "benchmark": "funding-source-investor-coverage",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "seed": seed,
        "sample_size": sample_size,
        "candidate_pool_size": len(candidate_pool),
        "candidate_source_ids": list(source_ids),
        "method": (
            "从五个固定公开融资列表形成候选池，排除未融资、证券两融、基金募资和汇总稿，"
            "按固定随机种子抽样；每个样本使用一次 Metaso 全网检索投资方证据。"
        ),
        "caveat": (
            "Metaso 搜索结果是独立核验语料，不是完整性真值。投资方清单及固定信源覆盖"
            "必须逐项目人工复核并保留 URL provenance。"
        ),
        "budget_after": ledger.status(configured_limit=configured_limit).to_dict(),
        "samples": rows,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


__all__ = [
    "DEFAULT_SOURCE_IDS",
    "FundingCandidate",
    "candidates_from_documents",
    "canonical_title",
    "deterministic_sample",
    "is_announced_funding_title",
    "metaso_query",
    "run_benchmark",
]
