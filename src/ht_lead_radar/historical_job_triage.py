"""Triage historical job-search results without promoting snippets to labels."""

from __future__ import annotations

from datetime import date
import re
from urllib.parse import urlparse


SENIORITY_TERMS = ("总监", "副总裁", "director", "head of", "vice president", " vp ")
JOB_TERMS = ("招聘", "职位", "岗位", "job", "career", "join us", "招募")
RECRUITING_HOSTS = (
    "liepin.com", "zhipin.com", "linkedin.com", "jobs.", "career.", "careers.",
)
NON_JOB_PATTERNS = (
    "管理层", "management team", "产品升级", "新版本发布", "app下载",
    "app store", "论文", "算法",
)


def normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def company_aliases(company: str, supplied: list[str] | None = None) -> list[str]:
    values = [company, *(supplied or [])]
    expanded: list[str] = []
    for value in values:
        expanded.extend(re.split(r"[\s/（()）]+", value))
        expanded.append(re.sub(r"[（(].*?[）)]", "", value))
    aliases: set[str] = set()
    for value in expanded:
        candidate = normalise_text(value).strip("-_,，。")
        if len(candidate) >= 2 and candidate not in {"中国", "group", "robotics"}:
            aliases.add(candidate)
    return sorted(aliases, key=lambda item: (-len(item), item))


def parse_publication_date(value: str) -> date | None:
    match = re.search(r"(\d{4})\D{0,2}(\d{1,2})\D{0,2}(\d{1,2})", value)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def query_date_window(query: str) -> tuple[date | None, date | None]:
    after_match = re.search(r"\bafter:(\d{4}-\d{2}-\d{2})", query)
    before_match = re.search(r"\bbefore:(\d{4}-\d{2}-\d{2})", query)
    after = date.fromisoformat(after_match.group(1)) if after_match else None
    before = date.fromisoformat(before_match.group(1)) if before_match else None
    return after, before


def triage_candidate(
    *, company: str, query: str, title: str, snippet: str, url: str,
    published_at: str, aliases: list[str] | None = None,
) -> dict[str, object]:
    """Score one search hit for review without treating it as verified."""
    title_text = normalise_text(title)
    all_text = normalise_text(f"{title} {snippet}")
    candidate_aliases = company_aliases(company, aliases)
    company_match = next((x for x in candidate_aliases if x in all_text), None)
    title_company_match = next((x for x in candidate_aliases if x in title_text), None)
    seniority_match = next(
        (term.strip() for term in SENIORITY_TERMS if term in f" {all_text} "), None
    )
    title_seniority_match = next(
        (term.strip() for term in SENIORITY_TERMS if term in f" {title_text} "), None
    )
    job_match = next((term for term in JOB_TERMS if term in all_text), None)
    host = urlparse(url).netloc.casefold()
    path = urlparse(url).path.casefold()
    recruiting_host = any(value in host for value in RECRUITING_HOSTS)
    direct_job_page = (
        "/job/" in path
        or "/jobs/view/" in path
        or re.search(r"/a/\d+", path) is not None
    )
    seo_landing_page = host.endswith("liepin.com") and path.startswith("/s/")
    non_job_match = next((x for x in NON_JOB_PATTERNS if x in title_text), None)
    public_date = parse_publication_date(published_at)
    after, before = query_date_window(query)
    in_window = public_date is not None
    if public_date is not None and after is not None and public_date <= after:
        in_window = False
    if public_date is not None and before is not None and public_date >= before:
        in_window = False

    score = 0
    reasons: list[str] = []
    for matched, points, reason in (
        (company_match, 3, f"company:{company_match}"),
        (seniority_match, 3, f"seniority:{seniority_match}"),
        (job_match, 2, f"job_context:{job_match}"),
        (recruiting_host, 1, "recruiting_host"),
        (public_date, 1, "parseable_publication_date"),
    ):
        if matched:
            score += points
            reasons.append(reason)
    if not in_window:
        score -= 4
        reasons.append("outside_or_missing_query_window")
    if non_job_match:
        score -= 5
        reasons.append(f"non_job_title:{non_job_match}")

    priority = "low"
    if non_job_match:
        priority = "low"
    elif (
        in_window
        and direct_job_page
        and title_company_match
        and title_seniority_match
        and (job_match or recruiting_host)
    ):
        priority = "high"
    elif in_window and title_company_match and seniority_match and recruiting_host:
        priority = "medium"
    return {
        "review_priority": priority, "triage_score": score, "reasons": reasons,
        "company_match": bool(company_match), "seniority_match": seniority_match,
        "title_company_match": bool(title_company_match),
        "title_seniority_match": title_seniority_match,
        "job_context_match": job_match, "recruiting_host": recruiting_host,
        "direct_job_page": direct_job_page,
        "seo_landing_page": seo_landing_page,
        "parsed_publication_date": public_date.isoformat() if public_date else None,
        "within_query_window": in_window,
        "verification_status": "unverified_search_candidate",
    }


__all__ = ["company_aliases", "parse_publication_date", "query_date_window", "triage_candidate"]
