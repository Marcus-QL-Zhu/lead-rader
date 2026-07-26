"""Thin, guarded bridge to the existing Liepin Skills.

The real adapter is inert unless ``execution_enabled=True`` is passed by an
explicit operator command.  Tests and local acceptance use ``FakePublisher``.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .talent_pool import validate_liepin_payload
from .talent_pool_store import TalentPoolStore


BLOCKING_ERROR_CODES = frozenset(
    {
        "auth_required",
        "captcha",
        "risk_control",
        "rate_limited",
        "manual_required",
        "ambiguous_result",
    }
)


@dataclass(frozen=True)
class PublishResult:
    success: bool
    job_id: str = ""
    job_url: str = ""
    error_code: str = ""
    error_message: str = ""
    blocking: bool = False


class Publisher(Protocol):
    def publish(
        self, payload: Mapping[str, Any], *, full_criteria: Mapping[str, Any]
    ) -> PublishResult: ...


class FakePublisher:
    """Deterministic publisher for tests and safe local end-to-end runs."""

    def __init__(self, results: Sequence[PublishResult] | None = None):
        self.results = list(results or ())
        self.calls: list[dict[str, Any]] = []

    def publish(
        self, payload: Mapping[str, Any], *, full_criteria: Mapping[str, Any]
    ) -> PublishResult:
        validate_liepin_payload(payload)
        self.calls.append(
            {"payload": dict(payload), "full_criteria": dict(full_criteria)}
        )
        if self.results:
            return self.results.pop(0)
        number = len(self.calls)
        return PublishResult(
            True,
            job_id=f"fake-{number:04d}",
            job_url=f"https://example.invalid/liepin/fake-{number:04d}",
        )


class ExternalLiepinPublisher:
    """Call Liepin's existing publisher, then its existing full pipeline.

    ``publish_job.py`` has no structured result contract and can exit zero on
    failure.  We therefore require a newly appended runtime posting with an
    ``ejob_id``.  Any uncertain outcome is blocking to avoid duplicate ads.
    """

    def __init__(
        self,
        *,
        python_bin: str,
        publish_script: str | Path,
        posting_runtime_file: str | Path,
        orchestrate_script: str | Path,
        execution_enabled: bool = False,
        timeout_seconds: int = 900,
    ):
        self.python_bin = python_bin
        self.publish_script = Path(publish_script)
        self.posting_runtime_file = Path(posting_runtime_file)
        self.orchestrate_script = Path(orchestrate_script)
        self.execution_enabled = execution_enabled
        self.timeout_seconds = timeout_seconds

    def publish(
        self, payload: Mapping[str, Any], *, full_criteria: Mapping[str, Any]
    ) -> PublishResult:
        validate_liepin_payload(payload)
        if not self.execution_enabled:
            return PublishResult(
                False,
                error_code="manual_required",
                error_message="real Liepin execution is disabled",
                blocking=True,
            )
        for path in (self.publish_script, self.orchestrate_script):
            if not path.is_file():
                return PublishResult(
                    False,
                    error_code="manual_required",
                    error_message=f"Liepin entry point not found: {path}",
                    blocking=True,
                )

        prior_ids = self._runtime_job_ids()
        try:
            # load_from_json opens argv[1] as a file path. Never invoke the
            # script without that path: its fallback performs a real publish.
            with tempfile.TemporaryDirectory(
                prefix="lead-rader-liepin-publish-"
            ) as directory:
                payload_path = Path(directory) / "payload.json"
                payload_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    [
                        self.python_bin,
                        str(self.publish_script),
                        str(payload_path),
                        "--no-pipeline",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                )
        except subprocess.TimeoutExpired as error:
            return PublishResult(
                False,
                error_code="ambiguous_result",
                error_message=f"publisher timed out: {error}",
                blocking=True,
            )
        combined = f"{completed.stdout}\n{completed.stderr}"
        classified = classify_liepin_error(combined)
        if classified:
            return classified
        new_records = [
            item for item in self._runtime_records() if _record_job_id(item) not in prior_ids
        ]
        if completed.returncode != 0 and not new_records:
            return PublishResult(
                False,
                error_code="publisher_failed",
                error_message=_tail(combined),
            )
        if len(new_records) != 1 or not _record_job_id(new_records[0]):
            return PublishResult(
                False,
                error_code="ambiguous_result",
                error_message="publisher result could not be tied to exactly one new ejob_id",
                blocking=True,
            )
        record = new_records[0]
        job_id = _record_job_id(record)
        job_url = str(
            record.get("preview_link")
            or record.get("job_url")
            or record.get("url")
            or ""
        )
        criteria = dict(full_criteria)
        criteria["ejob_id"] = job_id
        pipeline_result = self._start_full_pipeline(criteria)
        if pipeline_result is not None:
            # The public job already exists.  Preserve the job identity and
            # report a blocking downstream warning, never make it retryable as
            # a fresh publication.
            return PublishResult(
                True,
                job_id=job_id,
                job_url=job_url,
                error_code=pipeline_result.error_code,
                error_message=pipeline_result.error_message,
                blocking=True,
            )
        return PublishResult(True, job_id=job_id, job_url=job_url)

    def _runtime_records(self) -> list[dict[str, Any]]:
        if not self.posting_runtime_file.exists():
            return []
        try:
            value = json.loads(self.posting_runtime_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
        return []

    def _runtime_job_ids(self) -> set[str]:
        return {
            job_id
            for item in self._runtime_records()
            if (job_id := _record_job_id(item))
        }

    def _start_full_pipeline(
        self, criteria: Mapping[str, Any]
    ) -> PublishResult | None:
        with tempfile.TemporaryDirectory(prefix="lead-rader-liepin-") as directory:
            criteria_path = Path(directory) / "criteria.json"
            criteria_path.write_text(
                json.dumps(criteria, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                completed = subprocess.run(
                    [self.python_bin, str(self.orchestrate_script), str(criteria_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                return PublishResult(
                    False,
                    error_code="manual_required",
                    error_message=f"job published but pipeline start timed out: {error}",
                    blocking=True,
                )
        combined = f"{completed.stdout}\n{completed.stderr}"
        classified = classify_liepin_error(combined)
        if classified:
            return PublishResult(
                False,
                error_code=classified.error_code,
                error_message="job published; full pipeline blocked: "
                + classified.error_message,
                blocking=True,
            )
        if completed.returncode != 0:
            return PublishResult(
                False,
                error_code="manual_required",
                error_message="job published; full pipeline failed: " + _tail(combined),
                blocking=True,
            )
        if "❌" in combined or re.search(
            r"step\s*\d+.*(?:失败|failed)", combined, re.IGNORECASE
        ):
            return PublishResult(
                False,
                error_code="manual_required",
                error_message="job published; full pipeline partially failed: "
                + _tail(combined),
                blocking=True,
            )
        return None


def classify_liepin_error(text: str) -> PublishResult | None:
    lowered = text.casefold()
    patterns = (
        ("captcha", ("captcha", "验证码", "人机验证")),
        ("auth_required", ("登录失效", "重新登录", "unauthorized", "auth required")),
        ("risk_control", ("风控", "risk control", "账号异常")),
        ("rate_limited", ("限流", "rate limit", "too many requests")),
        ("manual_required", ("人工处理", "manual required")),
    )
    for code, markers in patterns:
        if any(marker in lowered for marker in markers):
            return PublishResult(
                False,
                error_code=code,
                error_message=_tail(text),
                blocking=True,
            )
    return None


def publish_approved_serially(
    store: TalentPoolStore,
    *,
    run_date: str,
    direction: str,
    publisher: Publisher,
    draft_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Publish the explicit selection under one account-level serial lease."""

    lease_token = store.acquire_publish_lease(run_date, direction)
    try:
        return _publish_with_lease(
            store,
            run_date=run_date,
            direction=direction,
            publisher=publisher,
            draft_ids=draft_ids,
            lease_token=lease_token,
        )
    finally:
        store.release_publish_lease(run_date, direction, lease_token)


def _publish_with_lease(
    store: TalentPoolStore,
    *,
    run_date: str,
    direction: str,
    publisher: Publisher,
    draft_ids: Sequence[str],
    lease_token: str,
) -> list[dict[str, Any]]:
    """Publish approved drafts in ordinal order, stopping on blocking errors."""

    summaries: list[dict[str, Any]] = []
    for draft_id in draft_ids:
        try:
            claimed = store.begin_publish(draft_id, lease_token=lease_token)
        except RuntimeError as error:
            summaries.append(
                {
                    "draft_id": draft_id,
                    "status": "blocked",
                    "error_code": "ambiguous_result",
                    "message": str(error),
                }
            )
            break
        except (ValueError, KeyError) as error:
            summaries.append(
                {
                    "draft_id": draft_id,
                    "status": "failed",
                    "error_code": "preflight_failed",
                    "message": str(error),
                }
            )
            continue
        if claimed is None:
            summaries.append({"draft_id": draft_id, "status": "already_published"})
            continue
        row, attempt_key = claimed
        draft = json.loads(row["draft_json"])
        result = publisher.publish(
            draft["public_payload"],
            full_criteria=draft["public_payload"],
        )
        if result.success:
            store.finish_publish(
                draft_id=draft_id,
                attempt_key=attempt_key,
                outcome="published",
                job_id=result.job_id,
                job_url=result.job_url,
                error_code=result.error_code,
                error_message=result.error_message,
            )
            summaries.append(
                {
                    "draft_id": draft_id,
                    "status": "published",
                    "job_id": result.job_id,
                    "job_url": result.job_url,
                    "warning_code": result.error_code,
                    "message": result.error_message,
                }
            )
            if result.blocking:
                break
            continue
        outcome = (
            "ambiguous"
            if result.error_code == "ambiguous_result"
            else "failed"
        )
        store.finish_publish(
            draft_id=draft_id,
            attempt_key=attempt_key,
            outcome=outcome,
            error_code=result.error_code or "publisher_failed",
            error_message=result.error_message,
        )
        summaries.append(
            {
                "draft_id": draft_id,
                "status": "blocked" if result.blocking else "failed",
                "error_code": result.error_code,
                "message": result.error_message,
            }
        )
        if result.blocking or result.error_code in BLOCKING_ERROR_CODES:
            break
    return summaries


def _record_job_id(record: Mapping[str, Any]) -> str:
    value = record.get("ejob_id") or record.get("job_id") or ""
    match = re.search(r"\d+", str(value))
    return match.group(0) if match else ""


def _tail(text: str, limit: int = 800) -> str:
    compact = " ".join(text.split())
    return compact[-limit:]


__all__ = [
    "BLOCKING_ERROR_CODES",
    "ExternalLiepinPublisher",
    "FakePublisher",
    "PublishResult",
    "classify_liepin_error",
    "publish_approved_serially",
]
