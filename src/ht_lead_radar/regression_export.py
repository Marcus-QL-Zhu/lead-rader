"""Strict, offline export of the frozen production regression set."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
from typing import Any
from urllib.parse import parse_qsl, urlparse


EXPORT_SCHEMA_VERSION = 2
CAPTURE_VERSION = "pre-hotfix"
SANITIZER_POLICY = "allowlist-v2-url-hash-strict-json"
DEFAULT_START = date(2026, 8, 18)
DEFAULT_END = date(2026, 8, 31)
DEFAULT_FIXTURE_OUTPUT = Path("evaluation/production-regression-20260818-31")

DAILY_KEYS = frozenset(
    {
        "schema_version",
        "capture_date",
        "capture_version",
        "legacy",
        "safe_report_manifest",
        "source_report",
        "operational_status",
        "candidate_gates",
        "selected_leads",
        "cooldown",
        "source_health",
    }
)
MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "capture_version",
        "date_range",
        "generator_git_sha",
        "sanitizer_policy",
        "operational_database",
        "days",
        "overall_sha256",
    }
)

_ANALYSIS_STATUSES = {"completed", "partial", "failed", "not_run"}
_DRAFT_STATUSES = {"complete", "partial", "failed", "not_run"}
_NOTIFICATION_STATUSES = {
    "pending",
    "hook_reported",
    "hook_failed",
    "hook_failed_fallback_sent",
    "fallback_sent",
    "fallback_failed",
    "not_attempted",
}
_HEALTH_STATUSES = {"healthy", "warning", "critical", "unavailable"}
_ADAPTER_STATUSES = {
    "ok",
    "not_modified",
    "partial",
    "error",
    "disabled",
    "unsupported_adapter",
}
_NOTIFICATION_CHANNELS = {"openclaw", "direct_feishu", "feishu", "hook"}
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[^\s/@?#:]{1,160}$")
_FORBIDDEN_KEY_TERMS = frozenset(
    {
        "authorization",
        "contact",
        "contacts",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "cv",
        "email",
        "header",
        "headers",
        "html",
        "password",
        "passwd",
        "people",
        "person",
        "phone",
        "prompt",
        "raw",
        "response",
        "resume",
        "secret",
        "sessionid",
        "token",
    }
)
_FORBIDDEN_KEY_SEQUENCES = (
    ("access", "key"),
    ("access", "token"),
    ("api", "key"),
    ("api", "secret"),
    ("app", "secret"),
    ("client", "id"),
    ("client", "secret"),
    ("id", "token"),
    ("private", "key"),
    ("refresh", "token"),
    ("secret", "key"),
    ("session", "id"),
)
_FORBIDDEN_COMPACT_KEY_SUFFIXES = (
    "accesskey",
    "accesstoken",
    "apikey",
    "apisecret",
    "appsecret",
    "clientid",
    "clientsecret",
    "idtoken",
    "privatekey",
    "refreshtoken",
    "secretkey",
    "sessionid",
)
_SAFE_SENSITIVE_KEY_SUFFIXES = frozenset(
    {"class", "count", "date", "status", "version"}
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_NUMBERISH_PII = re.compile(
    r"(?<![0-9A-Za-z])(?:[（(]\s*)?(?:\+\s*)?[0-9Xx]"
    r"[0-9Xx()（）\s./-]{6,62}[0-9Xx](?![0-9A-Za-z])"
)
_STANDALONE_CALENDAR_DATE = re.compile(
    r"(?<![0-9Xx./-])(?:"
    r"(?:18|19|20)\d{2}[-./\s](?:0?[1-9]|1[0-2])[-./\s]"
    r"(?:0?[1-9]|[12]\d|3[01])|"
    r"(?:0?[1-9]|[12]\d|3[01])[-./\s](?:0?[1-9]|1[0-2])[-./\s]"
    r"(?:18|19|20)\d{2}"
    r")(?![0-9Xx./-])"
)
_DATE_VERSION_CANDIDATE = re.compile(
    r"(?:"
    r"(?:18|19|20)\d{2}[-./](?:0?[1-9]|1[0-2])[-./]"
    r"(?:0?[1-9]|[12]\d|3[01])|"
    r"(?:0?[1-9]|[12]\d|3[01])[-./](?:0?[1-9]|1[0-2])[-./]"
    r"(?:18|19|20)\d{2}"
    r")(?:-(?:r)?\d+){0,3}",
    re.IGNORECASE,
)
_FORMATTED_THOUSANDS = re.compile(
    r"\d{1,3}(?P<thousands_separator>[ .])\d{3}"
    r"(?:(?P=thousands_separator)\d{3})+"
)
_NON_CONTACT_NUMBER_CONTEXT = re.compile(
    r"(?:version|build|release|revision|rev|count|total|"
    r"版本|构建|计数|数量|总计)\s*[:#=-]?\s*$",
    re.IGNORECASE,
)
_ABSOLUTE_UNIX_PATH = re.compile(
    r"/(?:bin|boot|data|dev|etc|home|lib|lib64|media|mnt|opt|proc|root|run|"
    r"sbin|srv|sys|tmp|usr|var|workspace)(?:/|$)",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"[A-Z]:(?:\\\\|/(?!/))", re.IGNORECASE
)
_UNC_PATH = re.compile(r"(?:\\\\|(?<!:)//)[^/\\\s]+[/\\]")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<name>[A-Za-z][A-Za-z0-9_.\-/]{0,95})"
    r"\s*(?:=|:)\s*"
    r"(?P<value>[^\s,;]{1,512})"
)
_SPACED_CREDENTIAL_ASSIGNMENT = re.compile(
    r"\b(?:"
    r"(?:api|app|client)\s+(?:id|key|secret|token)|"
    r"(?:access|id|refresh|secret)\s+(?:key|token)|"
    r"session\s+id|set\s+cookie"
    r")\s*(?:=|:)\s*[^\s,;]+",
    re.IGNORECASE,
)
_AUTHORIZATION_HEADER = re.compile(
    r"\bauthorization\s*[:=]?\s*(?:basic|bearer)\s+[^\s,;]+",
    re.IGNORECASE,
)
_AUTH_SCHEME_VALUE = re.compile(
    r"^\s*(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]{8,}\s*$",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?i:sk-(?:proj-|svcacct-|ant-)[a-z0-9_-]{20,})|"
    r"(?i:sk-[a-z0-9]{20,})|"
    r"(?i:github_pat_[a-z0-9_]{20,})|"
    r"(?i:gh[pousr]_[a-z0-9]{20,})|"
    r"(?i:gl(?:pat|dt|rt|cbt|soat|ptt)-[a-z0-9_-]{20,})|"
    r"(?i:AIza[a-z0-9_-]{20,})|"
    r"(?i:GOCSPX-[a-z0-9_-]{20,})|"
    r"(?i:ya29\.[a-z0-9_-]{20,})|"
    r"(?i:1//[a-z0-9_-]{20,})|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"(?i:xox[baprs]-[a-z0-9-]{10,})"
    r")(?![A-Za-z0-9])"
)
_PRIVATE_KEY_MATERIAL = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", re.IGNORECASE
)
_URL_WITH_QUERY = re.compile(r"(?:https?:)?//[^\s<>\"']+", re.IGNORECASE)
_SIGNED_URL_QUERY_KEYS = frozenset(
    {
        "access-token",
        "awsaccesskeyid",
        "googleaccessid",
        "key-pair-id",
        "ossaccesskeyid",
        "q-signature",
        "security-token",
        "sig",
        "signature",
        "token",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-signature",
        "x-goog-credential",
        "x-goog-security-token",
        "x-goog-signature",
        "x-oss-security-token",
    }
)
_ADAPTER_COUNT_KEYS = (
    "listing_count",
    "discovered_count",
    "incremental_count",
    "detail_success_count",
    "detail_failure_count",
    "semantic_attempt_count",
    "semantic_accepted_count",
    "semantic_prefiltered_count",
    "semantic_failure_count",
    "rule_event_count",
    "minimax_event_count",
    "evidence_count",
    "open_dead_letter_count",
)


class RegressionExportError(ValueError):
    """An input or export violates the frozen-set contract."""


def stable_json_bytes(value: Any) -> bytes:
    """Return deterministic finite JSON bytes."""

    _assert_finite(value)
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return sha256(stable_json_bytes(value)).hexdigest()


def strict_json_loads(value: str | bytes) -> Any:
    """Reject duplicate object keys and JSON's non-standard numeric constants."""

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise RegressionExportError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    def reject_constant(constant: str) -> None:
        raise RegressionExportError(f"non-finite JSON constant: {constant}")

    try:
        return json.loads(
            value,
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegressionExportError(f"invalid JSON: {error}") from error


def expected_dates() -> list[str]:
    return [
        date.fromordinal(value).isoformat()
        for value in range(DEFAULT_START.toordinal(), DEFAULT_END.toordinal() + 1)
    ]


def inspect_sqlite_metadata(sqlite_path: str | Path | None) -> dict[str, Any] | None:
    """Read only schema names through an immutable read-only SQLite connection."""

    if sqlite_path is None:
        return None
    path = Path(sqlite_path)
    _require_regular_no_reparse(path, "SQLite input")
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True
    )
    try:
        table_names = [
            str(item[0])
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
    finally:
        connection.close()
    result = {
        "provided": True,
        "schema_sha256": sha256_json(table_names),
        "table_count": len(table_names),
    }
    _validate_database_metadata(result)
    return result


def build_regression_set(
    reports_dir: str | Path,
    output_dir: str | Path,
    *,
    generator_git_sha: str,
    start: date = DEFAULT_START,
    end: date = DEFAULT_END,
    sqlite_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a brand-new directory and atomically publish it after validation."""

    if start != DEFAULT_START or end != DEFAULT_END:
        raise RegressionExportError("the frozen window must be 2026-08-18..2026-08-31")
    if not _GIT_SHA.fullmatch(generator_git_sha):
        raise RegressionExportError("generator_git_sha must be a 40-hex commit")
    report_root = Path(reports_dir)
    target = Path(output_dir)
    _require_directory_no_reparse(report_root, "reports directory")
    _reject_overlapping_paths(report_root, target)
    _require_safe_new_target(target)

    inputs = _find_reports(report_root, expected_dates())
    missing = sorted(set(expected_dates()) - set(inputs))
    if missing:
        raise RegressionExportError(f"missing dates: {', '.join(missing)}")
    database_metadata = inspect_sqlite_metadata(sqlite_path)

    target.parent.mkdir(parents=True, exist_ok=True)
    _require_directory_no_reparse(target.parent, "output parent")
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
    )
    try:
        entries: list[dict[str, Any]] = []
        for capture_date in expected_dates():
            source_bytes = inputs[capture_date].read_bytes()
            raw = strict_json_loads(source_bytes)
            daily = canonicalize_daily_report(
                raw,
                capture_date,
                source_report_sha256=sha256(source_bytes).hexdigest(),
                source_report_bytes=len(source_bytes),
            )
            validate_daily_export(daily, expected_date=capture_date)
            filename = f"{capture_date}.json"
            content = stable_json_bytes(daily) + b"\n"
            _write_new_file(staging / filename, content)
            entries.append(
                {
                    "capture_date": capture_date,
                    "file": filename,
                    "byte_count": len(content),
                    "sha256": sha256(content).hexdigest(),
                }
            )

        manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "capture_version": CAPTURE_VERSION,
            "date_range": {
                "start": DEFAULT_START.isoformat(),
                "end": DEFAULT_END.isoformat(),
            },
            "generator_git_sha": generator_git_sha,
            "sanitizer_policy": SANITIZER_POLICY,
            "operational_database": database_metadata,
            "days": entries,
        }
        manifest["overall_sha256"] = sha256_json(manifest)
        _validate_manifest(manifest)
        _write_new_file(
            staging / "manifest.json", stable_json_bytes(manifest) + b"\n"
        )
        validate_regression_set(staging)
        if os.path.lexists(target):
            raise RegressionExportError("output target appeared during export")
        os.replace(staging, target)
        return validate_regression_set(target)
    except Exception:
        if staging.exists() and not _is_reparse(staging):
            shutil.rmtree(staging)
        raise


def canonicalize_daily_report(
    raw: Any,
    capture_date: str,
    *,
    source_report_sha256: str,
    source_report_bytes: int,
) -> dict[str, Any]:
    """Project operational data into a bounded, privacy-safe representation."""

    if not isinstance(raw, Mapping):
        raise RegressionExportError("report must be a JSON object")
    if capture_date not in expected_dates():
        raise RegressionExportError("capture date is outside the frozen window")
    leads = raw.get("leads", raw.get("companies", []))
    if not isinstance(leads, list):
        raise RegressionExportError("report leads must be a list")
    selected = [_project_lead(item, index + 1) for index, item in enumerate(leads)]
    manifest = raw.get("manifest")
    direction = _safe_string(raw.get("direction"))
    if not direction and isinstance(manifest, Mapping):
        direction = _safe_string(manifest.get("direction"))
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "capture_date": capture_date,
        "capture_version": CAPTURE_VERSION,
        "legacy": None,
        "safe_report_manifest": _safe_manifest(manifest, direction),
        "source_report": {
            "byte_count": source_report_bytes,
            "sha256": source_report_sha256,
        },
        "operational_status": _project_status(raw),
        "candidate_gates": _gate_counts(selected),
        "selected_leads": selected,
        "cooldown": _project_cooldown(raw),
        "source_health": _project_source_health(raw),
    }


def validate_daily_export(
    value: Any, *, expected_date: str | None = None
) -> None:
    if not isinstance(value, Mapping) or set(value) != DAILY_KEYS:
        raise RegressionExportError("daily export has unexpected or missing keys")
    if value["schema_version"] != EXPORT_SCHEMA_VERSION:
        raise RegressionExportError("unsupported daily schema version")
    if value["capture_version"] != CAPTURE_VERSION or value["legacy"] is not None:
        raise RegressionExportError("historical exports require pre-hotfix legacy null")
    if value["capture_date"] not in expected_dates():
        raise RegressionExportError("daily capture date is outside frozen window")
    if expected_date is not None and value["capture_date"] != expected_date:
        raise RegressionExportError("daily capture date does not match filename")
    _validate_safe_manifest(value["safe_report_manifest"])
    if value["safe_report_manifest"]["as_of"] != value["capture_date"]:
        raise RegressionExportError("report as_of must equal capture_date")
    _validate_source_report(value["source_report"])
    _validate_operational_status(value["operational_status"])
    _validate_candidate_gates(value["candidate_gates"], value["selected_leads"])
    if not isinstance(value["selected_leads"], list):
        raise RegressionExportError("selected_leads must be a list")
    for index, item in enumerate(value["selected_leads"], 1):
        _validate_lead(item, expected_rank=index)
    _validate_lead_collection(value["selected_leads"])
    _validate_cooldown(value["cooldown"], value["selected_leads"])
    _validate_source_health(value["source_health"])
    _scan_forbidden(value)
    _assert_finite(value)


def validate_regression_set(output_dir: str | Path) -> dict[str, Any]:
    """Validate exact contents, strict JSON, all schemas, and all digests."""

    target = Path(output_dir)
    _require_directory_no_reparse(target, "regression-set directory")
    expected_names = {"manifest.json", *(f"{day}.json" for day in expected_dates())}
    children = list(target.iterdir())
    if {item.name for item in children} != expected_names:
        raise RegressionExportError("regression-set directory has missing or extra entries")
    for child in children:
        _require_regular_no_reparse(child, "regression-set file")

    manifest_content = (target / "manifest.json").read_bytes()
    manifest = strict_json_loads(manifest_content)
    if manifest_content != stable_json_bytes(manifest) + b"\n":
        raise RegressionExportError("manifest is not canonical JSON")
    _validate_manifest(manifest)
    entries = manifest["days"]
    for entry, capture_date in zip(entries, expected_dates(), strict=True):
        filename = entry["file"]
        content = (target / filename).read_bytes()
        if len(content) != entry["byte_count"]:
            raise RegressionExportError(f"byte count mismatch for {filename}")
        if sha256(content).hexdigest() != entry["sha256"]:
            raise RegressionExportError(f"hash mismatch for {filename}")
        daily = strict_json_loads(content)
        if content != stable_json_bytes(daily) + b"\n":
            raise RegressionExportError(f"daily file is not canonical JSON: {filename}")
        validate_daily_export(daily, expected_date=capture_date)
    digest_input = {
        key: value for key, value in manifest.items() if key != "overall_sha256"
    }
    if manifest["overall_sha256"] != sha256_json(digest_input):
        raise RegressionExportError("overall manifest hash mismatch")
    _scan_forbidden(manifest)
    return dict(manifest)


def _find_reports(root: Path, dates: Sequence[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(root.iterdir()):
        if path.suffix.lower() != ".json":
            continue
        match = re.search(r"(20\d\d-\d\d-\d\d)", path.name)
        if not match or match.group(1) not in dates:
            continue
        _require_regular_no_reparse(path, "source report")
        capture_date = match.group(1)
        if capture_date in found:
            raise RegressionExportError(f"multiple report files for {capture_date}")
        found[capture_date] = path
    return found


def _project_lead(item: Any, rank: int) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise RegressionExportError("lead must be an object")
    score = item.get("score", 0)
    if not _is_finite_number(score):
        raise RegressionExportError("lead score must be finite")
    evidence = item.get("evidence", [])
    roles = item.get("target_roles", item.get("roles", []))
    gates = item.get("gates", {})
    if not isinstance(evidence, list):
        raise RegressionExportError("lead evidence must be a list")
    if not isinstance(roles, list) or not all(isinstance(value, str) for value in roles):
        raise RegressionExportError("target roles must be a string list")
    if not isinstance(gates, Mapping) or not all(
        isinstance(key, str) and isinstance(value, bool)
        for key, value in gates.items()
    ):
        raise RegressionExportError("lead gates must be a boolean mapping")
    result = {
        "rank": rank,
        "company": _required_safe_string(item.get("company"), "company"),
        "score": round(float(score), 4),
        "confidence_grade": _nullable_safe_string(item.get("confidence_grade")),
        "target_roles": [
            _required_safe_string(value, "target role") for value in roles
        ],
        "gates": {key: value for key, value in sorted(gates.items())},
        "evidence": _project_unique_evidence(evidence),
    }
    _scan_forbidden(result)
    return result


def _project_unique_evidence(evidence: Sequence[Any]) -> list[dict[str, Any]]:
    """Keep the first occurrence of each complete projected evidence tuple."""

    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for event in evidence:
        if not isinstance(event, Mapping):
            continue
        projected = _project_evidence(event)
        identity = (
            projected["event_type"],
            projected["event_date"],
            projected["source"],
            projected["evidence_url_sha256"],
        )
        if identity in seen:
            continue
        seen.add(identity)
        output.append(projected)
    return output


def _project_evidence(event: Mapping[str, Any]) -> dict[str, str | None]:
    raw_source_id = event.get("source_id")
    if raw_source_id is not None and (
        not isinstance(raw_source_id, str) or "://" in raw_source_id
    ):
        raise RegressionExportError(
            "evidence source_id must be an opaque identifier"
        )
    source_id = _nullable_safe_string(raw_source_id)
    if source_id is not None and (
        "://" in source_id or not _SAFE_ID.fullmatch(source_id)
    ):
        raise RegressionExportError("evidence source_id must be an opaque identifier")
    source = source_id or _source_domain(event.get("source_url"))
    source_url = event.get("source_url")
    return {
        "event_type": _nullable_safe_string(event.get("event_type")),
        "event_date": _safe_date(
            event.get("event_date") or event.get("published_at")
        ),
        "source": source,
        "evidence_url_sha256": (
            sha256(str(source_url).encode("utf-8")).hexdigest()
            if source_url
            else None
        ),
    }


def _safe_manifest(value: Any, direction: str) -> dict[str, str | None]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "direction": _nullable_safe_string(direction or raw.get("direction")),
        "run_id": _nullable_safe_string(raw.get("run_id")),
        "as_of": _safe_date(raw.get("as_of")),
    }


def _project_status(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = raw.get(
        "operational_status", raw.get("run_status", raw.get("status", {}))
    )
    status = value if isinstance(value, Mapping) else {}
    draft_count = status.get("draft_count", raw.get("draft_count"))
    return {
        "analysis_status": _enum_or_none(
            status.get("analysis_status"), _ANALYSIS_STATUSES
        ),
        "draft_generation_status": _enum_or_none(
            status.get("draft_generation_status"), _DRAFT_STATUSES
        ),
        "notification_status": _enum_or_none(
            status.get("notification_status"), _NOTIFICATION_STATUSES
        ),
        "source_health_status": _enum_or_none(
            status.get("source_health_status"), _HEALTH_STATUSES
        ),
        "draft_count": (
            draft_count
            if isinstance(draft_count, int)
            and not isinstance(draft_count, bool)
            and draft_count >= 0
            else None
        ),
        "draft_error_class": _nullable_safe_string(
            status.get("draft_error_class")
        ),
        "notification_channel": _enum_or_none(
            status.get("notification_channel"), _NOTIFICATION_CHANNELS
        ),
    }


def _project_cooldown(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = raw.get("daily_opportunity_segments", raw.get("cooldown"))
    count_keys = (
        "input_count",
        "eligible_count",
        "selected_count",
        "suppressed_count",
        "new_evidence_count",
        "returning_count",
    )
    company_keys = (
        "input",
        "eligible",
        "selected",
        "suppressed",
        "new_evidence",
        "returning",
    )
    if not isinstance(value, Mapping):
        return {
            "observed": None,
            **{key: None for key in count_keys},
            "companies": None,
        }

    new_items = value.get("new_opportunities")
    ongoing = value.get("ongoing_watchlist")
    suppressed = value.get("cooldown")
    material_new_items = [
        item
        for item in new_items or []
        if isinstance(item, Mapping)
        and item.get("reason") == "material_new_evidence"
    ] if isinstance(new_items, list) else []
    companies = {
        "input": _company_names(
            value.get("input_companies", value.get("input"))
        ),
        "eligible": _company_names(
            value.get("eligible_companies", value.get("eligible"))
        ),
        "selected": _company_names(
            value.get("selected_companies", value.get("selected"))
        ),
        "suppressed": _company_names(
            value.get("suppressed_companies", suppressed)
        ),
        "new_evidence": _company_names(material_new_items),
        "returning": _company_names(ongoing),
    }
    return {
        "observed": True,
        "input_count": _first_count(
            value.get("input_company_count"), value.get("input_count")
        ),
        "eligible_count": _first_count(
            value.get("eligible_company_count"), value.get("eligible_count")
        ),
        "selected_count": _first_count(
            value.get("selected_company_count"), value.get("selected_count")
        ),
        "suppressed_count": _first_count(
            value.get("suppressed_company_count"), value.get("suppressed_count")
        ),
        "new_evidence_count": _first_count(
            value.get("new_evidence_company_count"),
            value.get("new_evidence_count"),
        ),
        "returning_count": _first_count(
            value.get("returning_company_count"), value.get("returning_count")
        ),
        "companies": {key: companies[key] for key in company_keys},
    }


def _project_source_health(raw: Mapping[str, Any]) -> dict[str, Any]:
    adapters: dict[str, dict[str, Any]] = {}
    dedicated_summary: dict[str, int | None] | None = None
    direct = raw.get("source_health", raw.get("health"))
    if isinstance(direct, Mapping):
        _collect_adapter_map(
            direct.get("sources", direct.get("adapters")), adapters
        )

    manifest = raw.get("manifest")
    source_summary = (
        manifest.get("source_summary")
        if isinstance(manifest, Mapping)
        else None
    )
    if isinstance(source_summary, Mapping):
        runs = source_summary.get("runs")
        if isinstance(runs, list):
            for run in runs:
                if not isinstance(run, Mapping):
                    continue
                run_summary = run.get("run_summary")
                if isinstance(run_summary, Mapping):
                    _collect_adapter_map(run_summary.get("sources"), adapters)
                    _collect_dedicated(run_summary.get("dedicated_aggregate"), adapters)
                    candidate = _project_dedicated_summary(
                        run_summary.get("dedicated_aggregate")
                    )
                    if candidate is not None:
                        dedicated_summary = candidate
                health = run.get("health")
                if isinstance(health, Mapping):
                    _collect_adapter_map(health.get("sources"), adapters)
                    _collect_dedicated(health.get("dedicated_aggregate"), adapters)
                    candidate = _project_dedicated_summary(
                        health.get("dedicated_aggregate")
                    )
                    if candidate is not None:
                        dedicated_summary = candidate
    observed = bool(adapters) or isinstance(direct, Mapping) or isinstance(
        source_summary, Mapping
    )
    return {
        "observed": True if observed else None,
        "adapters": [adapters[key] for key in sorted(adapters)],
        "dedicated_aggregate": dedicated_summary,
    }


def _collect_dedicated(value: Any, adapters: dict[str, dict[str, Any]]) -> None:
    if not isinstance(value, Mapping):
        return
    _collect_adapter_map(value.get("sources"), adapters)


def _project_dedicated_summary(value: Any) -> dict[str, int | None] | None:
    if not isinstance(value, Mapping):
        return None
    names = (
        "source_count",
        "healthy_count",
        "failed_count",
        "open_dead_letter_count",
    )
    if not any(name in value for name in names):
        return None
    return {name: _first_count(value.get(name)) for name in names}


def _collect_adapter_map(
    value: Any, adapters: dict[str, dict[str, Any]]
) -> None:
    if not isinstance(value, Mapping):
        return
    for source_id, item in value.items():
        if isinstance(item, Mapping):
            projected = _project_adapter(str(source_id), item)
            clean_id = projected["source_id"]
            previous = adapters.get(clean_id)
            if previous is None:
                adapters[clean_id] = projected
                continue
            # A production report can describe one adapter in several nested
            # summaries.  Later summaries are more specific, but frequently
            # omit counters that were observed earlier.  Preserve every
            # observed value while still allowing an explicit later value to
            # take precedence.
            adapters[clean_id] = {
                key: (
                    projected[key]
                    if projected.get(key) is not None
                    else previous.get(key)
                )
                for key in previous.keys() | projected.keys()
            }


def _project_adapter(source_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
    clean_id = _required_safe_string(source_id, "source_id")
    if "://" in clean_id or not _SAFE_ID.fullmatch(clean_id):
        raise RegressionExportError("adapter source_id must be an opaque identifier")
    accepted = _first_count(item.get("semantic_accepted_count"))
    prefiltered = _first_count(
        item.get("semantic_prefiltered_count"), item.get("prefiltered_count")
    )
    failures = _first_count(
        item.get("semantic_failure_count"), item.get("semantic_failures")
    )
    attempts = _first_count(item.get("semantic_attempt_count"))
    raw_error = item.get("error_class")
    if raw_error is None and item.get("error"):
        raw_error = str(item["error"]).split(":", 1)[0]
    result: dict[str, Any] = {
        "source_id": clean_id,
        "status": _enum_or_none(item.get("status"), _ADAPTER_STATUSES),
        "error_class": _nullable_safe_string(raw_error),
        "listing_count": _first_count(item.get("listing_count")),
        "discovered_count": _first_count(item.get("discovered_count")),
        "incremental_count": _first_count(item.get("incremental_count")),
        "detail_success_count": _first_count(item.get("detail_success_count")),
        "detail_failure_count": _first_count(
            item.get("detail_failure_count"), item.get("detail_error_count")
        ),
        "semantic_attempt_count": attempts,
        "semantic_accepted_count": accepted,
        "semantic_prefiltered_count": prefiltered,
        "semantic_failure_count": failures,
        "rule_event_count": _first_count(item.get("rule_event_count")),
        "minimax_event_count": _first_count(item.get("minimax_event_count")),
        "evidence_count": _first_count(item.get("evidence_count")),
        "open_dead_letter_count": _first_count(
            item.get("open_dead_letter_count")
        ),
    }
    return result


def _gate_counts(leads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    names = sorted(
        {
            key
            for lead in leads
            for key in lead["gates"]
        }
    )
    return {
        "total_candidates": len(leads),
        "by_gate": [
            {
                "gate": name,
                "pass_count": sum(lead["gates"].get(name) is True for lead in leads),
                "fail_count": sum(lead["gates"].get(name) is False for lead in leads),
            }
            for name in names
        ],
    }


def _validate_manifest(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != MANIFEST_KEYS:
        raise RegressionExportError("invalid manifest schema")
    if value["schema_version"] != EXPORT_SCHEMA_VERSION:
        raise RegressionExportError("unsupported manifest schema version")
    if value["capture_version"] != CAPTURE_VERSION:
        raise RegressionExportError("invalid capture version")
    if value["sanitizer_policy"] != SANITIZER_POLICY:
        raise RegressionExportError("invalid sanitizer policy")
    if not isinstance(value["generator_git_sha"], str) or not _GIT_SHA.fullmatch(
        value["generator_git_sha"]
    ):
        raise RegressionExportError("invalid generator git SHA")
    if value["date_range"] != {
        "start": DEFAULT_START.isoformat(),
        "end": DEFAULT_END.isoformat(),
    }:
        raise RegressionExportError("invalid frozen date range")
    _validate_database_metadata(value["operational_database"])
    entries = value["days"]
    if not isinstance(entries, list) or len(entries) != 14:
        raise RegressionExportError("manifest must contain exactly fourteen days")
    if [entry.get("capture_date") for entry in entries] != expected_dates():
        raise RegressionExportError("manifest has incomplete or unordered coverage")
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "capture_date",
            "file",
            "byte_count",
            "sha256",
        }:
            raise RegressionExportError("invalid manifest day entry")
        if entry["file"] != f"{entry['capture_date']}.json":
            raise RegressionExportError("invalid manifest filename")
        _require_nonnegative_int(entry["byte_count"], "manifest byte_count")
        _require_digest(entry["sha256"], "manifest day digest")
    _require_digest(value["overall_sha256"], "overall digest")


def _validate_database_metadata(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping) or set(value) != {
        "provided",
        "schema_sha256",
        "table_count",
    }:
        raise RegressionExportError("invalid operational database metadata")
    if value["provided"] is not True:
        raise RegressionExportError("invalid database provided flag")
    _require_digest(value["schema_sha256"], "database schema digest")
    _require_nonnegative_int(value["table_count"], "database table count")


def _validate_safe_manifest(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "direction",
        "run_id",
        "as_of",
    }:
        raise RegressionExportError("invalid safe report manifest")
    _validate_nullable_string(value["direction"], "manifest direction")
    _validate_nullable_string(value["run_id"], "manifest run_id")
    _validate_nullable_date(value["as_of"], "manifest as_of")


def _validate_source_report(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"byte_count", "sha256"}:
        raise RegressionExportError("invalid source report reference")
    _require_nonnegative_int(value["byte_count"], "source report byte count")
    _require_digest(value["sha256"], "source report digest")


def _validate_operational_status(value: Any) -> None:
    expected = {
        "analysis_status",
        "draft_generation_status",
        "notification_status",
        "source_health_status",
        "draft_count",
        "draft_error_class",
        "notification_channel",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RegressionExportError("invalid operational status")
    _validate_nullable_enum(
        value["analysis_status"], _ANALYSIS_STATUSES, "analysis status"
    )
    _validate_nullable_enum(
        value["draft_generation_status"], _DRAFT_STATUSES, "draft status"
    )
    _validate_nullable_enum(
        value["notification_status"], _NOTIFICATION_STATUSES, "notification status"
    )
    _validate_nullable_enum(
        value["source_health_status"], _HEALTH_STATUSES, "source health status"
    )
    if value["draft_count"] is not None:
        _require_nonnegative_int(value["draft_count"], "draft count")
    _validate_nullable_string(value["draft_error_class"], "draft error class")
    _validate_nullable_enum(
        value["notification_channel"],
        _NOTIFICATION_CHANNELS,
        "notification channel",
    )


def _validate_candidate_gates(value: Any, leads: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "total_candidates",
        "by_gate",
    }:
        raise RegressionExportError("invalid candidate gate summary")
    _require_nonnegative_int(value["total_candidates"], "candidate total")
    if not isinstance(leads, list) or value["total_candidates"] != len(leads):
        raise RegressionExportError("candidate gate total does not match leads")
    if not isinstance(value["by_gate"], list):
        raise RegressionExportError("candidate gates by_gate must be a list")
    seen: set[str] = set()
    for item in value["by_gate"]:
        if not isinstance(item, Mapping) or set(item) != {
            "gate",
            "pass_count",
            "fail_count",
        }:
            raise RegressionExportError("invalid candidate gate item")
        gate = item["gate"]
        if not isinstance(gate, str) or not gate or gate in seen:
            raise RegressionExportError("invalid or duplicate gate name")
        seen.add(gate)
        _require_nonnegative_int(item["pass_count"], "gate pass count")
        _require_nonnegative_int(item["fail_count"], "gate fail count")
        actual_pass = sum(lead["gates"].get(gate) is True for lead in leads)
        actual_fail = sum(lead["gates"].get(gate) is False for lead in leads)
        if item["pass_count"] != actual_pass or item["fail_count"] != actual_fail:
            raise RegressionExportError("candidate gate counts do not match leads")
    expected_gates = sorted({key for lead in leads for key in lead["gates"]})
    if [item["gate"] for item in value["by_gate"]] != expected_gates:
        raise RegressionExportError("candidate gate list is incomplete or unordered")


def _validate_lead(value: Any, *, expected_rank: int) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "rank",
        "company",
        "score",
        "confidence_grade",
        "target_roles",
        "gates",
        "evidence",
    }:
        raise RegressionExportError("invalid projected lead")
    if value["rank"] != expected_rank:
        raise RegressionExportError("lead rank is not contiguous")
    _required_safe_string(value["company"], "company")
    if not _is_finite_number(value["score"]):
        raise RegressionExportError("invalid lead score")
    _validate_nullable_string(value["confidence_grade"], "confidence grade")
    if not isinstance(value["target_roles"], list):
        raise RegressionExportError("target_roles must be a list")
    if len(value["target_roles"]) != len(set(value["target_roles"])):
        raise RegressionExportError("target_roles must be unique")
    for role in value["target_roles"]:
        _required_safe_string(role, "target role")
    if not isinstance(value["gates"], Mapping) or not all(
        isinstance(key, str) and bool(key) and isinstance(item, bool)
        for key, item in value["gates"].items()
    ):
        raise RegressionExportError("invalid projected lead gates")
    if list(value["gates"]) != sorted(value["gates"]):
        raise RegressionExportError("projected lead gates must be sorted")
    if not isinstance(value["evidence"], list):
        raise RegressionExportError("invalid projected lead evidence")
    for item in value["evidence"]:
        _validate_evidence(item)
    evidence_identities = [
        (
            item["event_type"],
            item["event_date"],
            item["source"],
            item["evidence_url_sha256"],
        )
        for item in value["evidence"]
    ]
    if len(evidence_identities) != len(set(evidence_identities)):
        raise RegressionExportError("evidence entries must be unique")


def _validate_lead_collection(leads: Sequence[Mapping[str, Any]]) -> None:
    companies = [lead["company"] for lead in leads]
    if len(companies) != len(set(companies)):
        raise RegressionExportError("selected lead companies must be unique")
    scores = [float(lead["score"]) for lead in leads]
    if scores != sorted(scores, reverse=True):
        raise RegressionExportError("selected leads must be in descending score order")


def _validate_evidence(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "event_type",
        "event_date",
        "source",
        "evidence_url_sha256",
    }:
        raise RegressionExportError("invalid projected evidence")
    _validate_nullable_string(value["event_type"], "event type")
    _validate_nullable_date(value["event_date"], "event date")
    _validate_nullable_string(value["source"], "evidence source")
    if value["source"] is not None and (
        "://" in value["source"] or not _SAFE_ID.fullmatch(value["source"])
    ):
        raise RegressionExportError("evidence source must not be a URL")
    if value["evidence_url_sha256"] is not None:
        _require_digest(value["evidence_url_sha256"], "evidence URL digest")


def _validate_cooldown(
    value: Any, selected_leads: Sequence[Mapping[str, Any]]
) -> None:
    count_keys = {
        "input_count",
        "eligible_count",
        "selected_count",
        "suppressed_count",
        "new_evidence_count",
        "returning_count",
    }
    if not isinstance(value, Mapping) or set(value) != {
        "observed",
        *count_keys,
        "companies",
    }:
        raise RegressionExportError("invalid cooldown summary")
    if value["observed"] not in {True, None}:
        raise RegressionExportError("invalid cooldown observed flag")
    for key in count_keys:
        if value[key] is not None:
            _require_nonnegative_int(value[key], f"cooldown {key}")
    if value["companies"] is None:
        if value["observed"] is not None:
            raise RegressionExportError("observed cooldown must include companies")
        if any(value[key] is not None for key in count_keys):
            raise RegressionExportError(
                "unobserved cooldown must have null counts"
            )
        return
    company_keys = {
        "input",
        "eligible",
        "selected",
        "suppressed",
        "new_evidence",
        "returning",
    }
    companies = value["companies"]
    if not isinstance(companies, Mapping) or set(companies) != company_keys:
        raise RegressionExportError("invalid cooldown company segments")
    for items in companies.values():
        if (
            not isinstance(items, list)
            or items != sorted(set(items))
            or not all(isinstance(item, str) and bool(item) for item in items)
        ):
            raise RegressionExportError("invalid cooldown company list")
    pairs = {
        "input_count": "input",
        "eligible_count": "eligible",
        "selected_count": "selected",
        "suppressed_count": "suppressed",
        "new_evidence_count": "new_evidence",
        "returning_count": "returning",
    }
    for count_key, list_key in pairs.items():
        if value[count_key] is not None and value[count_key] != len(
            companies[list_key]
        ):
            raise RegressionExportError(
                f"cooldown {count_key} does not match company list"
            )
    selected_companies = sorted(lead["company"] for lead in selected_leads)
    if companies["selected"] != selected_companies:
        raise RegressionExportError(
            "cooldown selected companies do not match selected leads"
        )


def _validate_source_health(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "observed",
        "adapters",
        "dedicated_aggregate",
    }:
        raise RegressionExportError("invalid source health")
    if value["observed"] not in {True, None}:
        raise RegressionExportError("invalid source health observed flag")
    if not isinstance(value["adapters"], list):
        raise RegressionExportError("source health adapters must be a list")
    if value["observed"] is None and (
        value["adapters"] or value["dedicated_aggregate"] is not None
    ):
        raise RegressionExportError("unobserved source health cannot have adapters")
    _validate_dedicated_summary(value["dedicated_aggregate"])
    source_ids = []
    for item in value["adapters"]:
        _validate_adapter(item)
        source_ids.append(item["source_id"])
    if source_ids != sorted(set(source_ids)):
        raise RegressionExportError("source health adapters must be unique and sorted")


def _validate_dedicated_summary(value: Any) -> None:
    if value is None:
        return
    expected = {
        "source_count",
        "healthy_count",
        "failed_count",
        "open_dead_letter_count",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RegressionExportError("invalid dedicated aggregate summary")
    for key in expected:
        if value[key] is not None:
            _require_nonnegative_int(value[key], f"dedicated aggregate {key}")
    total_fields = (
        value["source_count"],
        value["healthy_count"],
        value["failed_count"],
    )
    if any(item is not None for item in total_fields):
        if not all(item is not None for item in total_fields):
            raise RegressionExportError(
                "dedicated aggregate source/healthy/failed totals must be observed together"
            )
        if value["healthy_count"] + value["failed_count"] != value["source_count"]:
            raise RegressionExportError(
                "dedicated aggregate healthy plus failed must equal source count"
            )


def _validate_adapter(value: Any) -> None:
    expected = {"source_id", "status", "error_class", *_ADAPTER_COUNT_KEYS}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RegressionExportError("invalid source adapter health")
    source_id = value["source_id"]
    if (
        not isinstance(source_id, str)
        or not _SAFE_ID.fullmatch(source_id)
        or "://" in source_id
    ):
        raise RegressionExportError("invalid adapter source_id")
    _validate_nullable_enum(value["status"], _ADAPTER_STATUSES, "adapter status")
    _validate_nullable_string(value["error_class"], "adapter error class")
    for key in _ADAPTER_COUNT_KEYS:
        if value[key] is not None:
            _require_nonnegative_int(value[key], f"adapter {key}")


def _reject_overlapping_paths(reports: Path, target: Path) -> None:
    reports_resolved = reports.resolve()
    target_resolved = target.resolve(strict=False)
    if (
        reports_resolved == target_resolved
        or reports_resolved in target_resolved.parents
        or target_resolved in reports_resolved.parents
    ):
        raise RegressionExportError("reports and output directories must not overlap")


def _require_safe_new_target(target: Path) -> None:
    if os.path.lexists(target):
        raise RegressionExportError("output target must not already exist")
    existing = target.parent
    while not os.path.lexists(existing):
        if existing == existing.parent:
            break
        existing = existing.parent
    _require_directory_no_reparse(existing, "output ancestor")


def _require_directory_no_reparse(path: Path, label: str) -> None:
    _require_no_reparse_chain(path, label)
    if not path.is_dir():
        raise RegressionExportError(f"{label} must be a real directory")


def _require_regular_no_reparse(path: Path, label: str) -> None:
    _require_no_reparse_chain(path, label)
    if not path.is_file():
        raise RegressionExportError(f"{label} must be a regular non-link file")


def _require_no_reparse_chain(path: Path, label: str) -> None:
    current = path.absolute()
    while True:
        if os.path.lexists(current) and _is_reparse(current):
            raise RegressionExportError(f"{label} must not traverse a link/reparse point")
        if current == current.parent:
            return
        current = current.parent


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _write_new_file(path: Path, content: bytes) -> None:
    if os.path.lexists(path):
        raise RegressionExportError(f"refusing existing export file: {path.name}")
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _source_domain(value: Any) -> str | None:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    hostname = parsed.hostname.lower()
    if not _SAFE_ID.fullmatch(hostname):
        return None
    return hostname


def _company_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        candidate = item.get("company") if isinstance(item, Mapping) else item
        text = _nullable_safe_string(candidate)
        if text is not None:
            names.append(text)
    return sorted(set(names))


def _first_count(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _enum_or_none(value: Any, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _required_safe_string(value: Any, label: str) -> str:
    text = _nullable_safe_string(value)
    if text is None:
        raise RegressionExportError(f"{label} must be a non-empty string")
    return text


def _nullable_safe_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegressionExportError("sanitized text values must be strings")
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > 300:
        raise RegressionExportError("sanitized text exceeds 300 characters")
    _scan_forbidden(text)
    return text


def _safe_string(value: Any) -> str:
    return _nullable_safe_string(value) or ""


def _safe_date(value: Any) -> str | None:
    text = _nullable_safe_string(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _validate_nullable_string(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value or len(value) > 300:
        raise RegressionExportError(f"invalid {label}")


def _validate_nullable_date(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise RegressionExportError(f"invalid {label}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise RegressionExportError(f"invalid {label}") from error
    if parsed.isoformat() != value:
        raise RegressionExportError(f"invalid {label}")


def _validate_nullable_enum(
    value: Any, allowed: set[str], label: str
) -> None:
    if value is not None and (not isinstance(value, str) or value not in allowed):
        raise RegressionExportError(f"invalid {label}")


def _require_nonnegative_int(value: Any, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RegressionExportError(f"invalid {label}")


def _require_digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise RegressionExportError(f"invalid {label}")


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _assert_finite(value: Any) -> None:
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_finite(item)
    elif isinstance(value, list):
        for item in value:
            _assert_finite(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise RegressionExportError("non-finite number is not valid canonical JSON")


def _scan_forbidden(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            text_key = str(key)
            if _is_forbidden_key(text_key) and not _is_safe_sensitive_metadata(
                text_key, item
            ):
                raise RegressionExportError(f"forbidden key at {path}.{text_key}")
            if (
                text_key.endswith("sha256")
                and isinstance(item, str)
                and _DIGEST.fullmatch(item)
            ):
                # Validated content digests are opaque and can coincidentally
                # contain an 11-digit run. Do not classify that as a phone.
                continue
            _scan_forbidden(item, f"{path}.{text_key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_forbidden(item, f"{path}[{index}]")
    elif isinstance(value, str):
        if (
            _EMAIL.search(value)
            or _contains_personal_number(value)
            or _ABSOLUTE_UNIX_PATH.search(value)
            or _WINDOWS_ABSOLUTE_PATH.search(value)
            or _UNC_PATH.search(value)
            or _CREDENTIAL_VALUE.search(value)
            or _AUTHORIZATION_HEADER.search(value)
            or _AUTH_SCHEME_VALUE.fullmatch(value)
            or _PRIVATE_KEY_MATERIAL.search(value)
            or _SPACED_CREDENTIAL_ASSIGNMENT.search(value)
            or _contains_credential_assignment(value)
            or _contains_signed_url(value)
        ):
            raise RegressionExportError(f"forbidden value at {path}")


def _key_terms(value: str) -> tuple[str, ...]:
    """Split snake, kebab, namespace, and camel-case identifiers safely."""

    with_acronym_boundaries = re.sub(
        r"(?<=[A-Z])(?=[A-Z][a-z])", "_", value
    )
    with_camel_boundaries = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])", "_", with_acronym_boundaries
    )
    return tuple(
        part.lower()
        for part in re.split(r"[^A-Za-z0-9]+", with_camel_boundaries)
        if part
    )


def _is_forbidden_key(value: str) -> bool:
    parts = _key_terms(value)
    if not parts:
        return False
    if any(part in _FORBIDDEN_KEY_TERMS for part in parts):
        return True
    if any(
        part.endswith(suffix)
        for part in parts
        for suffix in _FORBIDDEN_COMPACT_KEY_SUFFIXES
    ):
        return True
    for sequence in _FORBIDDEN_KEY_SEQUENCES:
        width = len(sequence)
        if any(
            parts[index : index + width] == sequence
            for index in range(len(parts))
        ):
            return True
    return False


def _is_safe_sensitive_metadata(value: str, item: Any) -> bool:
    """Allow only typed telemetry suffixes, never arbitrary secret-like text."""

    parts = _key_terms(value)
    if not parts or parts[-1] not in _SAFE_SENSITIVE_KEY_SUFFIXES:
        return False
    suffix = parts[-1]
    if suffix == "count":
        return (
            isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
        ) or (isinstance(item, str) and bool(re.fullmatch(r"\d{1,20}", item)))
    if not isinstance(item, str):
        return item is None
    if suffix == "class":
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,159}", item))
    if suffix == "status":
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", item))
    if suffix == "date":
        return bool(re.fullmatch(r"(?:18|19|20)\d{2}-\d{2}-\d{2}", item))
    if suffix == "version":
        return bool(re.fullmatch(r"v?\d+(?:[._-]\d+){0,5}", item, re.IGNORECASE))
    return False


def _contains_credential_assignment(value: str) -> bool:
    """Detect credential-bearing headers, env text, and namespaced assignments."""

    for match in _CREDENTIAL_ASSIGNMENT.finditer(value):
        if (
            _is_forbidden_key(match.group("name"))
            and not _is_safe_sensitive_metadata(
                match.group("name"), match.group("value")
            )
            and match.group("value")
        ):
            return True
    return False


def _contains_signed_url(value: str) -> bool:
    """Reject presigned/cloud-authenticated URLs without rejecting normal queries."""

    for match in _URL_WITH_QUERY.finditer(value):
        candidate = match.group(0)
        parsed = urlparse(
            candidate if not candidate.startswith("//") else f"https:{candidate}"
        )
        if not parsed.query:
            continue
        for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
            normalized = key.strip().lower().replace("_", "-")
            if normalized in _SIGNED_URL_QUERY_KEYS and query_value:
                return True
            if (
                _is_forbidden_key(key)
                and not _is_safe_sensitive_metadata(key, query_value)
                and query_value
            ):
                return True
    return False


def _contains_personal_number(value: str) -> bool:
    """Detect obvious phone numbers and Mainland citizen-ID values.

    This is an export-boundary guard, not a phone-number parser.  It rejects
    canonical international/E.164 values, formatted international and Hong
    Kong values, Mainland mobile/landline values, and citizen IDs.  It only
    removes separators from bounded number-like candidates and deliberately
    ignores standalone dates and space-grouped ordinary counts.  Opaque
    Git/content digests are separately exempted only in validated digest
    fields by :func:`_scan_forbidden`.
    """

    # A date adjacent to a time or count would otherwise be consumed as one
    # greedy number-like candidate (for example ``2026-08-18 10``).  Remove
    # only standalone calendar-shaped tokens; formatted citizen IDs are not
    # standalone because the date is joined to the address/sequence fields.
    scan_value = _STANDALONE_CALENDAR_DATE.sub(" ", value)
    for match in _NUMBERISH_PII.finditer(scan_value):
        raw = match.group(0).strip()
        compact = re.sub(r"[()（）\s./-]", "", raw)
        digits = re.sub(r"\D", "", compact)
        nearby_prefix = scan_value[max(0, match.start() - 24) : match.start()]
        non_contact_context = bool(_NON_CONTACT_NUMBER_CONTEXT.search(nearby_prefix))
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", raw) and all(
            int(part) <= 255 for part in raw.split(".")
        ):
            continue
        if _DATE_VERSION_CANDIDATE.fullmatch(raw) or (
            non_contact_context and not raw.startswith("+")
        ):
            continue

        # Mainland citizen IDs: 18-digit modern and 15-digit legacy forms.
        if re.fullmatch(
            r"[1-9]\d{5}(?:18|19|20)\d{2}"
            r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx]",
            compact,
        ):
            return True
        if re.fullmatch(
            r"[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}",
            compact,
        ):
            return True

        domestic = compact
        for prefix in ("+86", "0086", "86"):
            if domestic.startswith(prefix):
                domestic = domestic[len(prefix) :]
                break
        if re.fullmatch(r"1[3-9]\d{9}", domestic):
            return True
        if re.fullmatch(r"0\d{9,11}", domestic):
            return True

        # Explicit international-dialling prefixes are unambiguous enough for
        # an export guard.  E.164 permits at most fifteen digits; require at
        # least eight so small signed counts are not treated as contact data.
        explicit_international = compact.startswith(("+", "00"))
        if explicit_international and 8 <= len(digits) <= 15:
            return True

        # Hong Kong numbers are eight local digits.  Accept an explicit 852
        # country code even without a plus, and accept local numbers only when
        # their conventional 4+4 formatting makes them look like a contact.
        for prefix in ("+852", "00852", "852"):
            if compact.startswith(prefix):
                local = compact[len(prefix) :]
                if re.fullmatch(r"[2356789]\d{7}", local):
                    return True
        has_formatting = bool(re.search(r"[()（）\s./-]", raw))
        groups = re.findall(r"\d+", raw)
        if (
            has_formatting
            and re.fullmatch(r"[2356789]\d{7}", digits)
            and [len(group) for group in groups] == [4, 4]
        ):
            return True

        # Other formatted international/national numbers are phone-like when
        # they contain 10-15 digits.  Do not reject ordinary counts grouped in
        # thousands (``123 456 789 000``).
        if (
            has_formatting
            and 10 <= len(digits) <= 15
            and not _FORMATTED_THOUSANDS.fullmatch(raw)
        ):
            return True
    return False
