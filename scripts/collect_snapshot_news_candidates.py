from __future__ import annotations

import argparse
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from ht_lead_radar.collectors import (
    BingRSSCollector,
    _clean_html,
    _event_date_from_text,
    grade_source,
    infer_event,
)


QUERY_TEMPLATES = (
    '"{company}" 任命 履新 融资 投资 工厂 扩产 投产 订单 合作 2026',
    '"{company}" 产品 发布 交付 客户 研发 总部 海外 2026',
)
BLOCKED_HOST_FRAGMENTS = ("bing.com", "baidu.com/s", "sogou.com")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _company_tokens(name: str) -> tuple[str, ...]:
    values = [name]
    compact = re.sub(r"[（(]中国[）)]", "", name)
    compact = re.sub(
        r"(?:股份有限公司|有限责任公司|有限公司|集团)$",
        "",
        compact,
    ).strip()
    if len(compact) >= 2:
        values.append(compact)
    ascii_tokens = re.findall(r"[A-Za-z][A-Za-z0-9.-]{1,}", name)
    values.extend(token for token in ascii_tokens if len(token) >= 3)
    return tuple(dict.fromkeys(value.casefold() for value in values if value))


def _mentions_company(company: str, title: str, snippet: str) -> bool:
    text = f"{title} {snippet}".casefold()
    return any(token in text for token in _company_tokens(company))


def _rss_date(value: str) -> str:
    if not value.strip():
        return ""
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _in_window(value: str, start: date, end: date) -> bool:
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return False
    return start <= parsed <= end


def _fetch_artifact(
    url: str,
    *,
    artifact_dir: Path,
    timeout_seconds: float,
) -> dict[str, str]:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    )
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout_seconds) as response:
        body = response.read(2_000_000)
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
    digest = sha256(body).hexdigest()
    suffix = ".html" if "html" in content_type.casefold() else ".bin"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_dir / f"{digest}{suffix}"
    if not target.exists():
        target.write_bytes(body)
    return {
        "final_url": final_url,
        "content_sha256": digest,
        "storage_path": target.as_posix(),
        "content_type": content_type,
        "body_text": _clean_html(body.decode("utf-8", errors="replace"))[:20000],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-inclusive", default="2026-06-30")
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--limit-per-query", type=int, default=8)
    parser.add_argument("--max-companies", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=20)
    args = parser.parse_args()

    pool = _read(args.pool)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end_inclusive)
    previous = _read(args.output) if args.output.exists() else {"companies": []}
    by_company = {
        item["company"]: item
        for item in previous.get("companies", [])
    }
    collector = BingRSSCollector(timeout=args.timeout_seconds)
    collector.endpoint = (
        "https://www.bing.com/search?format=rss&mkt=zh-CN"
        "&setlang=zh-Hans&q="
    )
    candidates = pool["companies"]
    if args.max_companies:
        candidates = candidates[: args.max_companies]
    for company_row in candidates:
        company = company_row["company"]
        if by_company.get(company, {}).get("status") == "completed":
            continue
        results: list[dict[str, object]] = []
        errors: list[str] = []
        seen_urls: set[str] = set()
        for template in QUERY_TEMPLATES:
            query = template.format(company=company)
            try:
                search_results = collector.search(
                    query,
                    limit=args.limit_per_query,
                )
            except Exception as exc:  # noqa: BLE001 - retain batch error
                errors.append(f"{type(exc).__name__}: {exc}")
                continue
            for result in search_results:
                if not result.url or result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                if any(fragment in result.url for fragment in BLOCKED_HOST_FRAGMENTS):
                    continue
                if not _mentions_company(company, result.title, result.snippet):
                    continue
                rss_date = _rss_date(result.published_at)
                inferred_date = _event_date_from_text(
                    f"{result.title} {result.snippet}",
                    rss_date,
                )
                event_type, phase = infer_event(
                    f"{result.title} {result.snippet}"
                )
                item: dict[str, object] = {
                    "query": query,
                    "title": result.title,
                    "snippet": result.snippet,
                    "source_url": result.url,
                    "source_host": urlsplit(result.url).netloc.casefold(),
                    "source_grade": grade_source(result.url),
                    "rss_published_at": rss_date,
                    "inferred_event_date": inferred_date,
                    "within_window": _in_window(inferred_date, start, end),
                    "event_type": event_type,
                    "phase": phase,
                    "verification_status": "search_candidate",
                }
                try:
                    artifact = _fetch_artifact(
                        result.url,
                        artifact_dir=args.artifact_dir,
                        timeout_seconds=args.timeout_seconds,
                    )
                    body_text = str(artifact.pop("body_text"))
                    item.update(artifact)
                    item["company_confirmed_in_artifact"] = _mentions_company(
                        company,
                        result.title,
                        body_text,
                    )
                    item["verification_status"] = "artifact_captured"
                except Exception as exc:  # noqa: BLE001 - retain item failure
                    item["artifact_error"] = f"{type(exc).__name__}: {exc}"
                results.append(item)
            if args.delay_seconds:
                time.sleep(args.delay_seconds)
        by_company[company] = {
            **company_row,
            "status": "completed" if not errors else "partial",
            "searched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "errors": errors,
            "results": results,
        }
        payload = {
            "schema_version": 1,
            "window_start": args.start,
            "window_end_inclusive": args.end_inclusive,
            "source": "Bing RSS discovery plus direct artifact fetch",
            "strict_label_policy": (
                "Search dates are candidates only; strict evidence requires a "
                "captured artifact and defensible publication date."
            ),
            "companies": [
                by_company[item["company"]]
                for item in pool["companies"]
                if item["company"] in by_company
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "companies": len(by_company),
                "results": sum(
                    len(item.get("results", []))
                    for item in by_company.values()
                ),
                "artifacts": sum(
                    result.get("verification_status") == "artifact_captured"
                    for item in by_company.values()
                    for result in item.get("results", [])
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
