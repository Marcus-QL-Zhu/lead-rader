from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from html.parser import HTMLParser
import re
import time
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from .taxonomy import classify_seniority


MOBILE_ORIGIN = "https://m.liepin.com"
JOB_PATH_RE = re.compile(r"/job/\d+\.shtml$")
UPDATE_RE = re.compile(r"(?:今天|昨天|\d{1,2}月\d{1,2}日)更新")


class LiepinAccessBlocked(RuntimeError):
    """Raised when Liepin returns its safety-verification page."""


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str
    captured_at: str
    content_sha256: str


class _CompanyPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.company_parts: list[str] = []
        self.jobs: list[dict[str, object]] = []
        self._h1_depth = 0
        self._job_depth = 0
        self._title_depth = 0
        self._current_job: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "h1":
            self._h1_depth = 1
        elif self._h1_depth:
            self._h1_depth += 1

        href = attr.get("href") or ""
        path = urlsplit(urljoin(MOBILE_ORIGIN, href)).path
        if tag == "a" and JOB_PATH_RE.fullmatch(path):
            normalized = urlunsplit(("https", "m.liepin.com", path, "", ""))
            self._current_job = {
                "job_url": normalized,
                "text_parts": [],
                "title_parts": [],
            }
            self._job_depth = 1
        elif self._job_depth:
            self._job_depth += 1

        if self._current_job is not None and tag == "h3":
            self._title_depth = 1
        elif self._title_depth:
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._title_depth:
            self._title_depth -= 1
        if self._h1_depth:
            self._h1_depth -= 1
        if self._job_depth:
            self._job_depth -= 1
            if self._job_depth == 0 and self._current_job is not None:
                self.jobs.append(self._current_job)
                self._current_job = None

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._h1_depth:
            self.company_parts.append(text)
        if self._current_job is not None:
            text_parts = self._current_job["text_parts"]
            assert isinstance(text_parts, list)
            text_parts.append(text)
            if self._title_depth:
                title_parts = self._current_job["title_parts"]
                assert isinstance(title_parts, list)
                title_parts.append(text)


class _JobDetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.update_text = ""
        self.description_parts: list[str] = []
        self._update_depth = 0
        self._description_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        if "update-time" in classes:
            self._update_depth = 1
        elif self._update_depth:
            self._update_depth += 1
        if attr.get("data-selector") == "job-intro-content":
            self._description_depth = 1
        elif self._description_depth:
            self._description_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._update_depth:
            self._update_depth -= 1
        if self._description_depth:
            self._description_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._update_depth and not self.update_text:
            match = UPDATE_RE.search(text)
            if match:
                self.update_text = match.group(0)
        if self._description_depth:
            self.description_parts.append(text)


def fetch_public_page(url: str, *, timeout_seconds: float = 20) -> FetchedPage:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout_seconds) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        final_url = response.geturl()
    if "safe.liepin.com" in final_url:
        raise LiepinAccessBlocked(
            "Liepin redirected to its safety-verification page; manual browser "
            "verification is required"
        )
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return FetchedPage(
        url=url,
        html=raw.decode(charset, errors="replace"),
        captured_at=captured_at,
        content_sha256=sha256(raw).hexdigest(),
    )


def parse_company_page(page: FetchedPage, *, company: str) -> dict[str, object]:
    parser = _CompanyPageParser()
    parser.feed(page.html)
    seen: set[str] = set()
    jobs: list[dict[str, object]] = []
    for raw_job in parser.jobs:
        job_url = str(raw_job["job_url"])
        if job_url in seen:
            continue
        seen.add(job_url)
        title_parts = [str(part) for part in raw_job["title_parts"]]
        title = title_parts[0] if title_parts else ""
        card_text = " | ".join(str(part) for part in raw_job["text_parts"])
        seniority, eligible, scope_terms = classify_seniority(title, card_text)
        jobs.append(
            {
                "company": company,
                "liepin_company_name": " ".join(parser.company_parts),
                "title": title,
                "card_text": card_text,
                "job_url": job_url,
                "source_kind": "liepin_company_guest",
                "company_page_url": page.url,
                "observed_at": page.captured_at,
                "company_page_sha256": page.content_sha256,
                "seniority_classification": seniority,
                "eligible_director_plus": eligible,
                "matched_scope_terms": scope_terms,
            }
        )
    return {
        "company": company,
        "liepin_company_name": " ".join(parser.company_parts),
        "company_page_url": page.url,
        "observed_at": page.captured_at,
        "company_page_sha256": page.content_sha256,
        "jobs": jobs,
    }


def parse_job_detail(page: FetchedPage) -> dict[str, str]:
    parser = _JobDetailParser()
    parser.feed(page.html)
    return {
        "detail_observed_at": page.captured_at,
        "detail_page_sha256": page.content_sha256,
        "displayed_update_text": parser.update_text,
        "description": "\n".join(parser.description_parts),
    }


def collect_company(
    *,
    company: str,
    company_page_url: str,
    fetcher: Callable[[str], FetchedPage] = fetch_public_page,
    fetch_director_details: bool = True,
    delay_seconds: float = 3,
) -> dict[str, object]:
    page = fetcher(company_page_url)
    result = parse_company_page(page, company=company)
    jobs = result["jobs"]
    assert isinstance(jobs, list)
    if fetch_director_details:
        for job in jobs:
            assert isinstance(job, dict)
            if not job["eligible_director_plus"]:
                continue
            if delay_seconds:
                time.sleep(delay_seconds)
            detail_page = fetcher(str(job["job_url"]))
            job.update(parse_job_detail(detail_page))
    return result
