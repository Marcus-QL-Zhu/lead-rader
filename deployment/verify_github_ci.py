#!/usr/bin/env python3
"""Fail-closed GitHub Actions verification for Lead Radar's exact SHA."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import re
import sys
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)


CANONICAL_REPOSITORY = "Marcus-QL-Zhu/lead-rader"
CANONICAL_WORKFLOW = "ci.yml"
CANONICAL_REPOSITORY_URL = "https://github.com/Marcus-QL-Zhu/lead-rader.git"
GITHUB_API_ORIGIN = "https://api.github.com"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_PATH_RE = re.compile(
    r"^/Marcus-QL-Zhu/lead-rader/actions/runs/(?P<run_id>[1-9][0-9]*)$"
)
MAX_RUN_AGE = timedelta(days=14)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 20.0


class GitHubCIVerificationError(ValueError):
    """The canonical GitHub workflow does not prove this exact SHA is green."""


Fetcher = Callable[[Request], bytes]


class _RejectRedirects(HTTPRedirectHandler):
    """Do not let an API request silently leave the pinned GitHub origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _timestamp(value: object) -> datetime:
    raw = str(value or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise GitHubCIVerificationError("workflow timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise GitHubCIVerificationError("workflow timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def github_ci_url(sha: str) -> str:
    if not SHA_RE.fullmatch(sha):
        raise GitHubCIVerificationError("requested SHA is not canonical lowercase hex")
    query = urlencode(
        {
            "head_sha": sha,
            "status": "completed",
            "event": "push",
            "branch": "main",
            "per_page": "20",
        }
    )
    return (
        f"{GITHUB_API_ORIGIN}/repos/{CANONICAL_REPOSITORY}/actions/"
        f"workflows/{CANONICAL_WORKFLOW}/runs?{query}"
    )


def github_ci_request(sha: str) -> Request:
    return Request(
        github_ci_url(sha),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "lead-rader-exact-sha-deployer/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )


def _fetch_github_api(request: Request) -> bytes:
    """Read a bounded response from the fixed API origin without redirects."""

    parsed = urlparse(request.full_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.github.com"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise GitHubCIVerificationError("GitHub API request origin is not canonical")
    opener = build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status != 200 or response.geturl() != request.full_url:
                raise GitHubCIVerificationError("GitHub API returned an unsafe response")
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "json" not in content_type:
                raise GitHubCIVerificationError("GitHub API response is not JSON")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise GitHubCIVerificationError("trusted GitHub Actions query failed") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise GitHubCIVerificationError("GitHub Actions response exceeded size limit")
    return payload


def _is_canonical_run_url(value: object, run_id: object) -> bool:
    parsed = urlparse(str(value or ""))
    match = RUN_PATH_RE.fullmatch(parsed.path)
    try:
        expected_id = int(run_id)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and match
        and int(match.group("run_id")) == expected_id
    )


def verify_github_ci(
    sha: str,
    *,
    fetcher: Fetcher = _fetch_github_api,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return the exact successful main/push run queried from GitHub itself."""

    request = github_ci_request(sha)
    try:
        response = fetcher(request)
    except GitHubCIVerificationError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise GitHubCIVerificationError("trusted GitHub Actions query failed") from error
    if not isinstance(response, bytes) or len(response) > MAX_RESPONSE_BYTES:
        raise GitHubCIVerificationError("GitHub Actions response is invalid")
    try:
        payload = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GitHubCIVerificationError("GitHub Actions response was not JSON") from error
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise GitHubCIVerificationError("GitHub Actions response omitted workflow runs")
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    accepted: list[tuple[datetime, int, dict[str, object]]] = []
    for raw in runs:
        if not isinstance(raw, dict):
            continue
        repository = raw.get("repository")
        repository_name = (
            repository.get("full_name") if isinstance(repository, dict) else None
        )
        if (
            raw.get("head_sha") != sha
            or raw.get("status") != "completed"
            or raw.get("conclusion") != "success"
            or raw.get("event") != "push"
            or raw.get("head_branch") != "main"
            or repository_name != CANONICAL_REPOSITORY
            or not _is_canonical_run_url(raw.get("html_url"), raw.get("id"))
        ):
            continue
        updated = _timestamp(raw.get("updated_at"))
        if updated > reference + timedelta(minutes=5):
            continue
        if reference - updated > MAX_RUN_AGE:
            continue
        accepted.append((updated, int(raw["id"]), raw))
    if not accepted:
        raise GitHubCIVerificationError(
            "expected a recent successful CI push run for exact SHA"
        )
    # GitHub reruns may legitimately produce more than one successful record
    # for the same exact commit. The newest already-validated run is the proof.
    return max(accepted, key=lambda item: (item[0], item[1]))[2]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sha")
    args = parser.parse_args(argv)
    try:
        run = verify_github_ci(args.sha)
    except GitHubCIVerificationError as error:
        print(f"GitHub CI rejected: {error}", file=sys.stderr)
        return 64
    print(f"GitHub CI verified: run_id={int(run.get('id') or 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
