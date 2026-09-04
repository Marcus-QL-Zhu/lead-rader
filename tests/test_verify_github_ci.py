import json
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest

from deployment.verify_github_ci import (
    CANONICAL_REPOSITORY,
    CANONICAL_WORKFLOW,
    GitHubCIVerificationError,
    MAX_RESPONSE_BYTES,
    _fetch_github_api,
    github_ci_request,
    verify_github_ci,
)


SHA = "a" * 40
NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)


def _run(**overrides):
    payload = {
        "id": 42,
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "event": "push",
        "head_branch": "main",
        "updated_at": "2026-08-31T11:00:00Z",
        "html_url": "https://github.com/Marcus-QL-Zhu/lead-rader/actions/runs/42",
        "repository": {"full_name": CANONICAL_REPOSITORY},
    }
    payload.update(overrides)
    return payload


def _fetcher(payload):
    def fetch(request):
        expected = github_ci_request(SHA)
        assert request.full_url == expected.full_url
        assert request.method == "GET"
        assert request.get_header("User-agent")
        assert request.get_header("Accept") == "application/vnd.github+json"
        return json.dumps({"workflow_runs": payload}).encode()

    return fetch


def test_queries_only_canonical_repo_workflow_and_accepts_exact_recent_push():
    request = github_ci_request(SHA)
    parsed = urlparse(request.full_url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.hostname == "api.github.com"
    assert (
        parsed.path
        == f"/repos/{CANONICAL_REPOSITORY}/actions/workflows/{CANONICAL_WORKFLOW}/runs"
    )
    assert query == {
        "head_sha": [SHA],
        "status": ["completed"],
        "event": ["push"],
        "branch": ["main"],
        "per_page": ["20"],
    }

    result = verify_github_ci(SHA, fetcher=_fetcher([_run()]), now=NOW)

    assert result["id"] == 42


@pytest.mark.parametrize(
    "run",
    [
        _run(head_sha="b" * 40),
        _run(status="in_progress"),
        _run(conclusion="failure"),
        _run(event="pull_request"),
        _run(head_branch="feature"),
        _run(updated_at="2026-07-01T00:00:00Z"),
        _run(repository={"full_name": "attacker/fork"}),
        _run(html_url="https://evil.invalid/run/42"),
        _run(html_url="https://github.com/Marcus-QL-Zhu/lead-rader/actions/runs/99"),
        _run(html_url="https://github.com/Marcus-QL-Zhu/lead-rader/actions/runs/42?x=1"),
    ],
)
def test_rejects_wrong_sha_state_branch_event_age_repo_or_run_url(run):
    with pytest.raises(GitHubCIVerificationError):
        verify_github_ci(SHA, fetcher=_fetcher([run]), now=NOW)


@pytest.mark.parametrize("sha", ["A" * 40, "g" * 40, "a" * 39, "a" * 41])
def test_rejects_noncanonical_sha_without_invoking_fetcher(sha):
    with pytest.raises(GitHubCIVerificationError):
        verify_github_ci(
            sha,
            fetcher=lambda _request: pytest.fail("fetcher must not be called"),
            now=NOW,
        )


def test_fails_closed_on_network_invalid_json_and_oversized_response():
    def network_error(_request):
        raise URLError("not exposed")

    with pytest.raises(GitHubCIVerificationError):
        verify_github_ci(SHA, fetcher=network_error, now=NOW)
    with pytest.raises(GitHubCIVerificationError):
        verify_github_ci(SHA, fetcher=lambda _request: b"not-json", now=NOW)
    with pytest.raises(GitHubCIVerificationError):
        verify_github_ci(
            SHA,
            fetcher=lambda _request: b"x" * (MAX_RESPONSE_BYTES + 1),
            now=NOW,
        )


def test_multiple_successful_reruns_select_the_latest_valid_exact_sha_run():
    older = _run(updated_at="2026-08-31T10:00:00Z")
    newer = _run(
        id=43,
        updated_at="2026-08-31T11:30:00Z",
        html_url="https://github.com/Marcus-QL-Zhu/lead-rader/actions/runs/43",
    )

    result = verify_github_ci(SHA, fetcher=_fetcher([newer, older]), now=NOW)

    assert result["id"] == 43


def test_default_http_fetcher_rejects_a_redirected_final_url(monkeypatch):
    class Response:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self):
            return "https://attacker.invalid/forged.json"

        def read(self, _limit):
            return b"{}"

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout > 0
            return Response()

    monkeypatch.setattr(
        "deployment.verify_github_ci.build_opener",
        lambda *_handlers: Opener(),
    )

    with pytest.raises(GitHubCIVerificationError, match="unsafe response"):
        _fetch_github_api(github_ci_request(SHA))
