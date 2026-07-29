from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

from .collectors import (
    _clean_html,
    _event_date_from_text,
    extract_company_candidates,
    infer_event,
)
from .models import Evidence
from .taxonomy import profile_for


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            text = re.sub(r"\s+", " ", " ".join(self._text)).strip()
            self.anchors.append((self._href, text))
            self._href = ""
            self._text = []


class FixedSourceCollector:
    provider_name = "fixed-source direct crawl"
    supports_search = False

    def __init__(
        self,
        registry_path: str | Path,
        state_db: str | Path,
        timeout: float = 20.0,
        max_bytes: int = 5_000_000,
    ):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.registry_path = Path(registry_path)
        self.state_db = Path(state_db)
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        self.last_run_summary: dict[str, object] = {"sources": {}, "errors": []}
        self._initialize()

    def _initialize(self) -> None:
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.state_db) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS fixed_evidence (
                    source_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    PRIMARY KEY (source_id, source_url)
                );
                CREATE TABLE IF NOT EXISTS source_runs (
                    source_id TEXT PRIMARY KEY,
                    last_run TEXT NOT NULL,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    error TEXT NOT NULL
                );
            """)

    def _fetch(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 HT-Lead-Radar/0.2",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read(self.max_bytes + 1)
        if len(payload) > self.max_bytes:
            raise ValueError(f"response exceeds {self.max_bytes} bytes")
        try:
            return payload.decode(charset, errors="replace")
        except LookupError:
            return payload.decode("utf-8", errors="replace")

    def _candidate_links(self, source: dict, html: str) -> list[tuple[str, str]]:
        parser = AnchorParser()
        parser.feed(html)
        output: list[tuple[str, str]] = []
        seen: set[str] = set()
        pattern = re.compile(source.get("link_pattern") or r".*")
        for href, title in parser.anchors:
            url = urllib.parse.urljoin(source["list_url"], href)
            if url in seen or not pattern.search(url) or len(title) < 6:
                continue
            seen.add(url)
            output.append((url, title))
        return output[: int(source.get("max_items", 30))]

    @staticmethod
    def _profiles(direction: str):
        topics = tuple(
            dict.fromkeys(item.strip() for item in direction.split("|") if item.strip())
        )
        return tuple(profile_for(topic) for topic in topics or (direction,))

    def _relevant(self, source: dict, direction: str, title: str, body: str) -> bool:
        text = f"{title} {body}".lower()
        profiles = self._profiles(direction)
        direction_terms = {
            term
            for profile in profiles
            for term in (*profile.aliases, *profile.seed_companies)
        }
        event_terms = tuple(
            dict.fromkeys(
                term for profile in profiles for term in profile.discovery_terms
            )
        ) + (
            "融资",
            "量产",
            "交付",
            "订单",
            "基地",
            "工厂",
            "产线",
            "产能",
            "投产",
            "签约",
            "战略合作",
            "海外",
            "数据集",
            "大模型",
            "发布",
        )
        if source.get("company"):
            return any(term.lower() in text for term in event_terms)
        return any(term.lower() in text for term in direction_terms) and any(
            term.lower() in text for term in event_terms
        )

    def _evidence(
        self, source: dict, direction: str, url: str, title: str, body: str
    ) -> list[Evidence]:
        text = f"{title} {body}"
        event_type, phase = infer_event(title)
        if event_type == "other":
            event_type, phase = infer_event(text)
        if event_type in {"other", "job_ad"}:
            return []
        if source.get("company"):
            companies = [source["company"]]
        else:
            seeds = tuple(
                dict.fromkeys(
                    seed
                    for profile in self._profiles(direction)
                    for seed in profile.seed_companies
                )
            )
            # Media roundups often mention many companies next to unrelated scale-up
            # signals. Only bind a seed company when it is present in the headline;
            # otherwise require an explicit company-event headline pattern below.
            seeded = [company for company in seeds if company.lower() in title.lower()]
            financing_names: list[str] = []
            for pattern in (
                r'[「“"]([^」”"]{2,18})[」”"].{0,24}(?:融资|获投)',
                r"(?:首发[｜|]\s*)?([\u4e00-\u9fffA-Za-z0-9·]{2,18}?)(?:完成|获得|获).{0,16}(?:融资|投资)",
                r"([\u4e00-\u9fffA-Za-z0-9·]{2,18}?)(?:携手|签约|获批|发布|投产|交付)",
            ):
                financing_names.extend(re.findall(pattern, title))
            noise = {"公司", "企业", "项目", "机器人", "科技", "完成", "获得", "宣布"}
            product_endings = ("版", "系列", "型号", "产品", "系统", "平台", "解决方案")
            inferred = [
                name.strip("，,:：；;（）() ")
                for name in financing_names
                if name.strip("，,:：；;（）() ") not in noise
                and not name.strip("，,:：；;（）() ").endswith(product_endings)
            ]
            suffixes = (
                "公司",
                "集团",
                "科技",
                "智能",
                "机器人",
                "仿生",
                "航天",
                "能源",
            )
            title_candidates = [
                name
                for name in extract_company_candidates(title)
                if len(name) <= 18 and name.endswith(suffixes)
            ]
            companies = list(dict.fromkeys(seeded + inferred + title_candidates))
        return [
            Evidence(
                company=company,
                event_type=event_type,
                phase=phase,
                event_date=_event_date_from_text(text, ""),
                title=title[:240],
                snippet=_clean_html(body)[:800],
                source_url=url,
                source_name=source["name"],
                source_grade=source.get("grade", "B"),
                direction=direction,
            )
            for company in companies[:3]
        ]

    def _store(self, source_id: str, evidence: Evidence) -> None:
        now = date.today().isoformat()
        serialized = json.dumps(asdict(evidence), ensure_ascii=False)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.state_db) as connection:
            connection.execute(
                """
                INSERT INTO fixed_evidence
                    (source_id, source_url, content_hash, first_seen, last_seen, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, source_url) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    last_seen=excluded.last_seen,
                    evidence_json=excluded.evidence_json
            """,
                (source_id, evidence.source_url, digest, now, now, serialized),
            )

    def _record_run(
        self, source_id: str, status: str, count: int, error: str = ""
    ) -> None:
        with sqlite3.connect(self.state_db) as connection:
            connection.execute(
                """
                INSERT INTO source_runs (source_id, last_run, status, item_count, error)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_run=excluded.last_run, status=excluded.status,
                    item_count=excluded.item_count, error=excluded.error
            """,
                (source_id, date.today().isoformat(), status, count, error[:500]),
            )

    def collect(
        self, direction: str, year: int = 0, limit_per_query: int = 10
    ) -> list[Evidence]:
        allowed_source_ids = tuple(
            str(source["id"])
            for source in self.registry.get("sources", [])
            if source.get("enabled", True) and not source.get("company")
        )
        applicable = tuple(
            self.registry.get("policy", {}).get("applicable_directions") or ()
        )
        normalized_direction = direction.strip().casefold()
        if applicable and not any(
            term.casefold() in normalized_direction
            or normalized_direction in term.casefold()
            for term in applicable
        ):
            self.last_run_summary["skipped"] = "direction_outside_legacy_scope"
            return []
        for source in self.registry.get("sources", []):
            # Daily discovery must remain universe-wide. Company-owned pages are
            # intentionally excluded even if an old registry accidentally enables one.
            if not source.get("enabled", True) or source.get("company"):
                continue
            source_id = source["id"]
            count = 0
            try:
                listing = self._fetch(source["list_url"])
                for url, listing_title in self._candidate_links(source, listing):
                    body = listing_title
                    if source.get("fetch_detail", True):
                        try:
                            body = _clean_html(self._fetch(url))
                        except Exception:
                            body = listing_title
                    if not self._relevant(source, direction, listing_title, body):
                        continue
                    for item in self._evidence(
                        source, direction, url, listing_title, body
                    ):
                        self._store(source_id, item)
                        count += 1
                self._record_run(source_id, "ok", count)
                self.last_run_summary["sources"][source_id] = count
            except Exception as exc:
                self._record_run(source_id, "error", 0, str(exc))
                self.last_run_summary["errors"].append(f"{source_id}: {exc}")
        return self.load_recent(direction, source_ids=allowed_source_ids)

    def load_recent(
        self,
        direction: str,
        days: int = 365,
        source_ids: tuple[str, ...] | None = None,
    ) -> list[Evidence]:
        if source_ids is not None and not source_ids:
            return []
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        source_filter = (
            " AND source_id IN ({})".format(",".join("?" for _ in source_ids))
            if source_ids is not None
            else ""
        )
        with sqlite3.connect(self.state_db) as connection:
            rows = connection.execute(
                "SELECT evidence_json, first_seen FROM fixed_evidence WHERE last_seen >= ?"
                + source_filter,
                (cutoff, *(source_ids or ())),
            ).fetchall()
        output: list[Evidence] = []
        for serialized, first_seen in rows:
            item = json.loads(serialized)
            if item.get("direction") != direction:
                continue
            if item.get("event_date") and item["event_date"] < cutoff:
                continue
            if not item.get("event_date"):
                item["event_date"] = first_seen
            item["people"] = tuple(item.get("people") or ())
            item["organizations"] = tuple(item.get("organizations") or ())
            output.append(Evidence(**item))
        return output
