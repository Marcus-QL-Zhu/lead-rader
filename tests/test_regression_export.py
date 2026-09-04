from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from ht_lead_radar.regression_export import (
    DAILY_KEYS,
    MANIFEST_KEYS,
    RegressionExportError,
    _contains_personal_number,
    _project_source_health,
    _scan_forbidden,
    build_regression_set,
    expected_dates,
    sha256_json,
    stable_json_bytes,
    strict_json_loads,
    validate_daily_export,
    validate_regression_set,
)
from ht_lead_radar.daily_opportunity_selection import select_daily_opportunities


GIT_SHA = "a" * 40


def _runtime_join(*parts: str) -> str:
    """Build test-only credential shapes without storing a complete token blob."""

    return "".join(parts)


def test_source_health_preserves_observed_fields_across_repeated_summaries():
    health = _project_source_health(
        {
            "manifest": {
                "source_summary": {
                    "runs": [
                        {
                            "run_summary": {
                                "sources": {
                                    "kr36": {
                                        "status": "ok",
                                        "discovered_count": 5,
                                        "detail_error_count": 1,
                                    }
                                },
                                "dedicated_aggregate": {
                                    "sources": {
                                        "kr36": {
                                            "status": "partial",
                                            "error_class": "TimeoutError",
                                            "listing_count": 8,
                                            "semantic_accepted_count": 3,
                                        }
                                    }
                                },
                            }
                        }
                    ]
                }
            }
        }
    )

    assert health["adapters"] == [
        {
            "source_id": "kr36",
            "status": "partial",
            "error_class": "TimeoutError",
            "listing_count": 8,
            "discovered_count": 5,
            "incremental_count": None,
            "detail_success_count": None,
            "detail_failure_count": 1,
            "semantic_attempt_count": None,
            "semantic_accepted_count": 3,
            "semantic_prefiltered_count": None,
            "semantic_failure_count": None,
            "rule_event_count": None,
            "minimax_event_count": None,
            "evidence_count": None,
            "open_dead_letter_count": None,
        }
    ]


def _report(day, history_database):
    report = {
        "direction": "具身智能",
        "manifest": {
            "run_id": f"run-{day}",
            "as_of": day,
            "direction": "具身智能",
            "source_summary": {
                "runs": [
                    {
                        "run_summary": {
                            "sources": {
                                "general-source": {
                                    "status": "partial",
                                    "discovered_count": 5,
                                    "detail_error_count": 1,
                                    "evidence_count": 2,
                                    "error": "TimeoutError: bounded",
                                }
                            },
                            "dedicated_aggregate": {
                                "source_count": 1,
                                "healthy_count": 1,
                                "failed_count": 0,
                                "open_dead_letter_count": 4,
                                "sources": {
                                    "kr36": {
                                        "status": "ok",
                                        "listing_count": 8,
                                        "incremental_count": 3,
                                        "detail_success_count": 3,
                                        "detail_failure_count": 0,
                                        "rule_event_count": 1,
                                        "minimax_event_count": 1,
                                        "prefiltered_count": 4,
                                        "semantic_failure_count": 1,
                                        "evidence_count": 2,
                                    }
                                }
                            },
                        }
                    }
                ]
            },
        },
        "leads": [
            {
                "company": f"公司{day[-2:]}",
                "score": 65.0,
                "confidence_grade": "B",
                "target_roles": ["商业化总监"],
                "gates": {"director_plus": True, "has_upstream_signal": False},
                "evidence": [
                    {
                        "event_type": "funding",
                        "event_date": day,
                        "source_url": "https://user:password@example.com:8443/article?ignored=value",
                    }
                ],
            }
        ],
        "late_opportunities": [],
    }
    return select_daily_opportunities(
        report,
        history_database=history_database,
        cooldown_days=7,
        target_count=20,
    )


def _write_reports(path):
    path.mkdir(parents=True)
    for day in expected_dates():
        (path / f"lead-radar-具身智能-{day}.json").write_text(
            json.dumps(
                _report(day, path.parent / "empty-history.sqlite"),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _build(tmp_path, *, database=None):
    reports = tmp_path / "reports"
    _write_reports(reports)
    output = tmp_path / "export"
    manifest = build_regression_set(
        reports,
        output,
        generator_git_sha=GIT_SHA,
        sqlite_path=database,
    )
    return reports, output, manifest


def _reseal_day(output: Path, day: str, content: bytes):
    manifest_path = output / "manifest.json"
    manifest = strict_json_loads(manifest_path.read_bytes())
    entry = next(item for item in manifest["days"] if item["capture_date"] == day)
    entry["byte_count"] = len(content)
    entry["sha256"] = sha256(content).hexdigest()
    (output / entry["file"]).write_bytes(content)
    manifest["overall_sha256"] = sha256_json(
        {key: value for key, value in manifest.items() if key != "overall_sha256"}
    )
    manifest_path.write_bytes(stable_json_bytes(manifest) + b"\n")


def test_exports_complete_sanitized_14_day_fixture_and_is_deterministic(tmp_path):
    database = tmp_path / "state.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE safe_state (id INTEGER)")
    reports, output, manifest = _build(tmp_path, database=database)

    assert len(manifest["days"]) == 14
    assert manifest["generator_git_sha"] == GIT_SHA
    assert len(list(output.iterdir())) == 15
    assert {item.name for item in output.iterdir()} == {
        "manifest.json",
        *(f"{day}.json" for day in expected_dates()),
    }
    assert manifest["operational_database"]["provided"] is True
    assert manifest["operational_database"]["table_count"] == 1
    daily = strict_json_loads((output / "2026-08-18.json").read_bytes())
    assert daily["capture_version"] == "pre-hotfix"
    assert daily["legacy"] is None
    evidence = daily["selected_leads"][0]["evidence"][0]
    assert evidence["source"] == "example.com"
    assert "article?ignored" not in json.dumps(daily)
    assert daily["candidate_gates"]["by_gate"] == [
        {"fail_count": 0, "gate": "director_plus", "pass_count": 1},
        {"fail_count": 1, "gate": "has_upstream_signal", "pass_count": 0},
    ]
    assert daily["cooldown"]["input_count"] == 1
    assert daily["cooldown"]["selected_count"] == 1
    assert daily["cooldown"]["companies"]["selected"] == ["公司18"]
    assert daily["cooldown"]["companies"]["new_evidence"] == []
    health = {item["source_id"]: item for item in daily["source_health"]["adapters"]}
    assert health["general-source"]["status"] == "partial"
    assert health["general-source"]["detail_failure_count"] == 1
    assert health["kr36"]["semantic_attempt_count"] is None
    assert health["kr36"]["semantic_accepted_count"] is None
    assert health["kr36"]["rule_event_count"] == 1
    assert health["kr36"]["minimax_event_count"] == 1
    assert health["kr36"]["listing_count"] == 8
    assert health["kr36"]["discovered_count"] is None
    assert daily["source_health"]["dedicated_aggregate"][
        "open_dead_letter_count"
    ] == 4
    assert validate_regression_set(output)["overall_sha256"] == manifest["overall_sha256"]

    repeat = tmp_path / "repeat"
    repeated = build_regression_set(
        reports, repeat, generator_git_sha=GIT_SHA, sqlite_path=database
    )
    assert repeated["overall_sha256"] == manifest["overall_sha256"]
    assert (output / "manifest.json").read_bytes() == (repeat / "manifest.json").read_bytes()


def test_schema_documents_match_implementation_required_keys():
    jsonschema = pytest.importorskip("jsonschema")
    root = Path(__file__).parents[1]
    manifest_schema = json.loads(
        (root / "docs/schemas/production-regression-set-v2.schema.json").read_text()
    )
    daily_schema = json.loads(
        (root / "docs/schemas/production-regression-day-v2.schema.json").read_text()
    )
    assert set(manifest_schema["required"]) == MANIFEST_KEYS
    assert set(manifest_schema["properties"]) == MANIFEST_KEYS
    assert set(daily_schema["required"]) == DAILY_KEYS
    assert set(daily_schema["properties"]) == DAILY_KEYS
    jsonschema.Draft202012Validator.check_schema(manifest_schema)
    jsonschema.Draft202012Validator.check_schema(daily_schema)


def test_generated_fixture_validates_against_both_json_schemas(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    _, output, manifest = _build(tmp_path)
    root = Path(__file__).parents[1]
    manifest_schema = json.loads(
        (root / "docs/schemas/production-regression-set-v2.schema.json").read_text()
    )
    daily_schema = json.loads(
        (root / "docs/schemas/production-regression-day-v2.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(manifest_schema).validate(manifest)
    validator = jsonschema.Draft202012Validator(daily_schema)
    for day in expected_dates():
        validator.validate(strict_json_loads((output / f"{day}.json").read_bytes()))


def test_validator_rejects_tampered_day_hash(tmp_path):
    _, output, _ = _build(tmp_path)
    target = output / "2026-08-18.json"
    payload = strict_json_loads(target.read_bytes())
    payload["selected_leads"][0]["score"] = 1
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RegressionExportError, match="mismatch"):
        validate_regression_set(output)


def test_recomputed_hash_cannot_hide_extra_nested_key_or_invalid_enum(tmp_path):
    _, output, _ = _build(tmp_path)
    target = output / "2026-08-18.json"
    payload = strict_json_loads(target.read_bytes())
    payload["operational_status"]["unexpected"] = 1
    _reseal_day(output, "2026-08-18", stable_json_bytes(payload) + b"\n")
    with pytest.raises(RegressionExportError, match="operational status"):
        validate_regression_set(output)

    payload["operational_status"].pop("unexpected")
    payload["operational_status"]["analysis_status"] = "mostly_done"
    _reseal_day(output, "2026-08-18", stable_json_bytes(payload) + b"\n")
    with pytest.raises(RegressionExportError, match="analysis status"):
        validate_regression_set(output)


@pytest.mark.parametrize(
    "value",
    [
        _runtime_join("person", "@example.com"),
        _runtime_join("to", "ken=", "private-value"),
        _runtime_join("api_", "key=", "private-value"),
        _runtime_join("api ", "key = ", "private-value"),
        _runtime_join("pass", "word=", "private-value"),
        _runtime_join("access ", "to", "ken=", "private-value"),
        _runtime_join("Authorization: ", "Basic ", "dXNlcjpwYXNzd29yZA=="),
        _runtime_join("Authorization: ", "Bearer ", "opaque-access-value-123"),
        _runtime_join("Basic ", "dXNlcjpwYXNzd29yZA=="),
        _runtime_join("Bearer ", "opaque-access-value-123"),
        _runtime_join("Coo", "kie", ": session=", "private-value"),
        _runtime_join("Set-", "Coo", "kie: sid=", "private-value"),
        _runtime_join("session", "id=", "private-value"),
        _runtime_join("openclaw.feishu.app_", "secret=", "private-value"),
        _runtime_join("provider/api", "Token=", "private-value"),
        _runtime_join("client-", "id=", "private-value"),
        _runtime_join("client_", "secret=", "private-value"),
        _runtime_join(
            "https://example.invalid/file?X-Amz-",
            "Signature=", "abcdef1234567890",
        ),
        _runtime_join(
            "https://example.invalid/file?version=2&",
            "sig=", "abcdef1234567890",
        ),
        _runtime_join("gh", "p_", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join(
            "github_",
            "pat_11AAABBBCCCDDDEEEFFF_abcdefghijklmnopqrstuvwxyz1234567890",
        ),
        _runtime_join("gh", "o_", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join("gl", "pat-", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join("AI", "za", "abcdefghijklmnopqrstuvwxyz12345"),
        _runtime_join("GOC", "SPX-", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join("ya", "29.", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join("AK", "IA", "ABCDEFGHIJKLMNOP"),
        _runtime_join("AS", "IA", "ABCDEFGHIJKLMNOP"),
        _runtime_join("xox", "b-", "1234567890-abcdef"),
        _runtime_join("-----BEGIN ", "PRIVATE KEY-----"),
        "/home/admin/.openclaw/workspace/secret",
        "/srv/lead-radar/state",
        "/tmp/private.json",
        "/usr/local/bin/private",
        "/mnt/private/state.json",
        "C:/Users/admin/private.json",
        r"\\server\share\private.json",
        "prefix/home/admin/private.json",
        "prefix/srv/private.json",
        "prefix/tmp/private.json",
        "prefixC:/Users/admin/private.json",
        r"prefix\\server\share\private.json",
        "13800138000",
        "138-0013-8000",
        "+86 138 0013 8000",
        "(+86) 138-0013-8000",
        "010-1234-5678",
        "021 12345678",
        "(+86) (021) 1234 5678",
        "+1 415 555 2671",
        "+1.415.555.2671",
        "+44/20/7946/0958",
        "(+44)/20/7946/0958",
        "+44-20-7946-0958",
        "0049 (30) 901820",
        "0049.30.901820",
        "0044/20/7946/0958",
        "+852 9123 4567",
        "(+852) 9123-4567",
        "(852) 2123-4567",
        "9123 4567",
        "9123-4567",
        "400-123-4567",
        "110105 1949 12 31 002X",
        "110105-1949-12-31-002X",
        "130503 6704 01 001",
    ],
)
def test_export_rejects_secrets_pii_and_absolute_paths(tmp_path, value):
    reports = tmp_path / "reports"
    _write_reports(reports)
    report_path = next(reports.glob("*2026-08-18.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["leads"][0]["company"] = value
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RegressionExportError):
        build_regression_set(
            reports, tmp_path / "export", generator_git_sha=GIT_SHA
        )
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-18",
        "2026/08/18",
        "2026.08.18",
        "2026-08-18 10:30",
        "18-08-2026",
        "版本 2026-08-18-r3",
        "版本 1234-5678-9012",
        "build: 2026-08-18-001",
        "source-2026-08-18-001-002",
        "v3.10.12",
        "192.168.1.123",
        "version 2026.08.18-r3",
        "123 456 789 000",
        "123.456.789.000",
        "普通计数 123456789012",
        "总计 13800138000",
        "0123456789abcdef" * 4,
        "a" * 40,
    ],
)
def test_pii_scan_does_not_reject_dates_versions_or_hashes(value):
    assert _contains_personal_number(value) is False


@pytest.mark.parametrize(
    "key",
    [
        _runtime_join("Authorization"),
        _runtime_join("Set", "Cookie"),
        _runtime_join("session", "Id"),
        _runtime_join("client", "Secret"),
        _runtime_join("client-", "id"),
        _runtime_join("CLIENT", "ID"),
        _runtime_join("feishu.app.", "secret"),
        _runtime_join("FEISHUAPP", "SECRET"),
        _runtime_join("provider.", "APIKey"),
        _runtime_join("provider/api_", "token"),
        _runtime_join("tenant-", "credential"),
        _runtime_join("AWS_", "ACCESS_KEY"),
        _runtime_join("google.private", "Key"),
    ],
)
def test_scan_rejects_header_and_namespaced_credential_keys(key):
    with pytest.raises(RegressionExportError, match="forbidden key"):
        _scan_forbidden({key: "redacted-looking-but-still-disallowed"})


@pytest.mark.parametrize(
    "value",
    [
        _runtime_join("Authorization ", "Basic ", "dXNlcjpwYXNzd29yZA=="),
        _runtime_join("Authorization: ", "Bearer ", "opaque-access-value-123"),
        _runtime_join("Coo", "kie", ": session=", "private-value"),
        _runtime_join("Set-", "Coo", "kie: sid=", "private-value"),
        _runtime_join("session", "id=", "private-value"),
        _runtime_join("api ", "key=", "private-value"),
        _runtime_join("vendor.app.", "secret=", "private-value"),
        _runtime_join("vendor/api", "Credential=", "private-value"),
        _runtime_join("client-", "id=", "private-value"),
        _runtime_join("client", "Secret=", "private-value"),
        _runtime_join(
            "https://example.invalid/object?X-Goog-",
            "Signature=", "abcdef1234567890",
        ),
        _runtime_join("gh", "p_", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join(
            "github_",
            "pat_11AAABBBCCCDDDEEEFFF_abcdefghijklmnopqrstuvwxyz1234567890",
        ),
        _runtime_join("gl", "pat-", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join("AK", "IA", "ABCDEFGHIJKLMNOP"),
        _runtime_join("AS", "IA", "ABCDEFGHIJKLMNOP"),
        _runtime_join("AI", "za", "abcdefghijklmnopqrstuvwxyz12345"),
        _runtime_join("GOC", "SPX-", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join("ya", "29.", "abcdefghijklmnopqrstuvwxyz123456"),
        _runtime_join("1", "//", "abcdefghijklmnopqrstuvwxyz123456"),
    ],
)
def test_scan_directly_rejects_extended_credential_value_shapes(value):
    with pytest.raises(RegressionExportError, match="forbidden value"):
        _scan_forbidden(value)


def test_sensitive_metadata_suffix_does_not_allow_arbitrary_text():
    with pytest.raises(RegressionExportError, match="forbidden key"):
        _scan_forbidden({"token_count": "not-a-count"})
    with pytest.raises(RegressionExportError, match="forbidden value"):
        _scan_forbidden("token_count=not-a-count")


def test_credential_scan_accepts_safe_classes_counts_dates_versions_and_hashes():
    safe = {
        "error_class": "TokenExpiredError",
        "credential_error_class": "CredentialUnavailableError",
        "authorization_status": "not_attempted",
        "token_count": 3,
        "secret_scan_count": 0,
        "release_date": "2026-08-18",
        "api_version": "v3.10.12",
        "source_report_sha256": "0123456789abcdef" * 4,
        "ordinary_count": 13800138000,
        "description": "BearerMarketEvent and CookiePolicyChanged are class names",
        "company_alias": "SK-hynix-semiconductor-supply-chain",
        "normal_url": "https://example.invalid/news?version=2&count=13800138000",
    }
    _scan_forbidden(safe)


def test_extended_credential_scan_accepts_synthetic_fourteen_day_fixture(tmp_path):
    _, output, manifest = _build(tmp_path)
    assert len(manifest["days"]) == 14
    validated = validate_regression_set(output)
    assert validated["overall_sha256"] == manifest["overall_sha256"]


def test_validate_only_cli_fails_closed_on_resealed_contact_pii(tmp_path):
    _, output, _ = _build(tmp_path)
    root = Path(__file__).parents[1]
    command = [
        sys.executable,
        "scripts/export_production_regression_set.py",
        "--output-dir",
        str(output),
        "--validate-only",
    ]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    valid = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr

    target = output / "2026-08-18.json"
    payload = strict_json_loads(target.read_bytes())
    payload["selected_leads"][0]["company"] = "+852 9123-4567"
    _reseal_day(output, "2026-08-18", stable_json_bytes(payload) + b"\n")

    rejected = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True
    )
    assert rejected.returncode != 0
    assert "forbidden value" in rejected.stdout


def test_hook_failed_is_preserved_as_an_undelivered_intermediate_status(tmp_path):
    reports = tmp_path / "reports"
    _write_reports(reports)
    report_path = next(reports.glob("*2026-08-18.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["operational_status"] = {"notification_status": "hook_failed"}
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "export"
    build_regression_set(reports, output, generator_git_sha=GIT_SHA)
    daily = strict_json_loads((output / "2026-08-18.json").read_bytes())
    assert daily["operational_status"]["notification_status"] == "hook_failed"
    validate_regression_set(output)


def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers():
    with pytest.raises(RegressionExportError, match="duplicate"):
        strict_json_loads('{"a":1,"a":2}')
    for constant in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(RegressionExportError, match="non-finite"):
            strict_json_loads(f'{{"value":{constant}}}')
    with pytest.raises(RegressionExportError, match="non-finite"):
        stable_json_bytes({"value": float("nan")})


@pytest.mark.parametrize("bad_json", [b'{"schema_version":2,"schema_version":2}', b'{"score":NaN}'])
def test_validator_rejects_duplicate_or_nonfinite_day_after_reseal(tmp_path, bad_json):
    _, output, _ = _build(tmp_path)
    _reseal_day(output, "2026-08-18", bad_json)
    with pytest.raises(RegressionExportError):
        validate_regression_set(output)


def test_build_rejects_existing_target_overlap_and_nonfixed_window(tmp_path):
    reports = tmp_path / "reports"
    _write_reports(reports)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(RegressionExportError, match="must not already exist"):
        build_regression_set(reports, existing, generator_git_sha=GIT_SHA)
    with pytest.raises(RegressionExportError, match="must not overlap"):
        build_regression_set(
            reports, reports / "nested-output", generator_git_sha=GIT_SHA
        )
    with pytest.raises(RegressionExportError, match="frozen window"):
        build_regression_set(
            reports,
            tmp_path / "wrong-window",
            generator_git_sha=GIT_SHA,
            start=date(2026, 8, 19),
        )


def test_failure_does_not_publish_partial_target(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    _write_reports(reports)
    next(reports.glob("*2026-08-31.json")).unlink()
    monkeypatch.setattr(
        "socket.create_connection",
        lambda *args, **kwargs: pytest.fail("network must not be used"),
    )
    output = tmp_path / "export"
    with pytest.raises(RegressionExportError, match="missing dates"):
        build_regression_set(reports, output, generator_git_sha=GIT_SHA)
    assert not output.exists()


def test_validator_rejects_extra_entry_and_database_shape_after_reseal(tmp_path):
    _, output, _ = _build(tmp_path)
    (output / "extra.txt").write_text("extra")
    with pytest.raises(RegressionExportError, match="extra entries"):
        validate_regression_set(output)
    (output / "extra.txt").unlink()

    manifest_path = output / "manifest.json"
    manifest = strict_json_loads(manifest_path.read_bytes())
    manifest["operational_database"] = {
        "provided": True,
        "schema_sha256": "b" * 64,
        "table_count": 1,
        "raw_path": "/tmp/state.sqlite",
    }
    manifest["overall_sha256"] = sha256_json(
        {key: value for key, value in manifest.items() if key != "overall_sha256"}
    )
    manifest_path.write_bytes(stable_json_bytes(manifest) + b"\n")
    with pytest.raises(RegressionExportError, match="database metadata"):
        validate_regression_set(output)


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("as_of", "as_of"),
        ("selected_count", "selected_count"),
        ("duplicate_role", "target_roles"),
        ("duplicate_evidence", "evidence"),
        ("rank", "rank"),
        ("duplicate_company", "companies"),
        ("score_order", "descending score"),
    ],
)
def test_cross_field_and_uniqueness_invariants_survive_resealing(
    tmp_path, mutation, message
):
    _, output, _ = _build(tmp_path)
    target = output / "2026-08-18.json"
    payload = strict_json_loads(target.read_bytes())
    lead = payload["selected_leads"][0]
    if mutation == "as_of":
        payload["safe_report_manifest"]["as_of"] = "2026-08-19"
    elif mutation == "selected_count":
        payload["cooldown"]["selected_count"] = 0
    elif mutation == "duplicate_role":
        lead["target_roles"].append(lead["target_roles"][0])
    elif mutation == "duplicate_evidence":
        lead["evidence"].append(dict(lead["evidence"][0]))
    elif mutation == "rank":
        lead["rank"] = 2
    else:
        second = json.loads(json.dumps(lead))
        second["rank"] = 2
        second["company"] = (
            lead["company"] if mutation == "duplicate_company" else "另一家公司"
        )
        second["score"] = 70.0 if mutation == "score_order" else lead["score"]
        payload["selected_leads"].append(second)
        payload["candidate_gates"]["total_candidates"] = 2
        for gate in payload["candidate_gates"]["by_gate"]:
            gate["pass_count"] *= 2
            gate["fail_count"] *= 2
        payload["cooldown"]["selected_count"] = 2
        payload["cooldown"]["companies"]["selected"] = sorted(
            {item["company"] for item in payload["selected_leads"]}
        )
    _reseal_day(output, "2026-08-18", stable_json_bytes(payload) + b"\n")
    with pytest.raises(RegressionExportError, match=message):
        validate_regression_set(output)


def test_validator_requires_canonical_daily_and_manifest_bytes(tmp_path):
    _, output, _ = _build(tmp_path)
    day_path = output / "2026-08-18.json"
    payload = strict_json_loads(day_path.read_bytes())
    pretty = json.dumps(payload, ensure_ascii=False, indent=2).encode() + b"\n"
    _reseal_day(output, "2026-08-18", pretty)
    with pytest.raises(RegressionExportError, match="not canonical"):
        validate_regression_set(output)

    _, second_output, _ = _build(tmp_path / "second")
    manifest_path = second_output / "manifest.json"
    manifest = strict_json_loads(manifest_path.read_bytes())
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RegressionExportError, match="manifest is not canonical"):
        validate_regression_set(second_output)


def test_source_id_url_is_rejected_and_url_uses_hostname_only(tmp_path):
    reports = tmp_path / "reports"
    _write_reports(reports)
    report_path = next(reports.glob("*2026-08-18.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["leads"][0]["evidence"][0]["source_id"] = "https://example.com/source"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RegressionExportError, match="source_id"):
        build_regression_set(
            reports, tmp_path / "export", generator_git_sha=GIT_SHA
        )


def test_legacy_cooldown_aliases_are_read_without_inventing_counts(tmp_path):
    reports = tmp_path / "reports"
    _write_reports(reports)
    report_path = next(reports.glob("*2026-08-18.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    segments = payload["daily_opportunity_segments"]
    segments["input"] = segments.pop("input_companies")
    segments["eligible"] = segments.pop("eligible_companies")
    segments["selected"] = segments.pop("selected_companies")
    for key in (
        "input_company_count",
        "eligible_company_count",
        "selected_company_count",
        "suppressed_company_count",
        "new_evidence_company_count",
        "returning_company_count",
    ):
        segments.pop(key)
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "export"
    build_regression_set(reports, output, generator_git_sha=GIT_SHA)
    daily = strict_json_loads((output / "2026-08-18.json").read_bytes())
    assert daily["cooldown"]["selected_count"] is None
    assert daily["cooldown"]["companies"]["selected"] == ["公司18"]


def test_same_url_may_support_distinct_event_types(tmp_path):
    reports = tmp_path / "reports"
    _write_reports(reports)
    report_path = next(reports.glob("*2026-08-18.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    second = dict(payload["leads"][0]["evidence"][0])
    second["event_type"] = "major_order"
    payload["leads"][0]["evidence"].append(second)
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    output = tmp_path / "export"
    build_regression_set(reports, output, generator_git_sha=GIT_SHA)
    daily = strict_json_loads((output / "2026-08-18.json").read_bytes())
    evidence = daily["selected_leads"][0]["evidence"]
    assert len(evidence) == 2
    assert evidence[0]["evidence_url_sha256"] == evidence[1]["evidence_url_sha256"]
    assert {item["event_type"] for item in evidence} == {"funding", "major_order"}


def test_raw_duplicate_evidence_is_stably_collapsed_before_export(tmp_path):
    reports = tmp_path / "reports"
    _write_reports(reports)
    report_path = next(reports.glob("*2026-08-18.json"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    original = dict(payload["leads"][0]["evidence"][0])
    payload["leads"][0]["evidence"] = [
        original,
        dict(original),
        {**original, "event_type": "major_order"},
        dict(original),
    ]
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    output = tmp_path / "export"
    build_regression_set(reports, output, generator_git_sha=GIT_SHA)
    daily = strict_json_loads((output / "2026-08-18.json").read_bytes())
    evidence = daily["selected_leads"][0]["evidence"]
    assert [item["event_type"] for item in evidence] == ["funding", "major_order"]


def test_unobserved_cooldown_requires_all_counts_null_after_reseal(tmp_path):
    _, output, _ = _build(tmp_path)
    target = output / "2026-08-18.json"
    payload = strict_json_loads(target.read_bytes())
    payload["cooldown"] = {
        "observed": None,
        "input_count": 1,
        "eligible_count": None,
        "selected_count": None,
        "suppressed_count": None,
        "new_evidence_count": None,
        "returning_count": None,
        "companies": None,
    }
    _reseal_day(output, "2026-08-18", stable_json_bytes(payload) + b"\n")
    with pytest.raises(RegressionExportError, match="null counts"):
        validate_regression_set(output)


def test_dedicated_aggregate_totals_follow_production_definition(tmp_path):
    _, output, _ = _build(tmp_path)
    target = output / "2026-08-18.json"
    payload = strict_json_loads(target.read_bytes())
    totals = payload["source_health"]["dedicated_aggregate"]
    totals.update({"source_count": 1, "healthy_count": 5, "failed_count": 5})
    _reseal_day(output, "2026-08-18", stable_json_bytes(payload) + b"\n")
    with pytest.raises(RegressionExportError, match="healthy plus failed"):
        validate_regression_set(output)


def test_validator_rejects_symlink_day_when_supported(tmp_path):
    _, output, _ = _build(tmp_path)
    day = output / "2026-08-18.json"
    backing = tmp_path / "backing.json"
    day.rename(backing)
    try:
        os.symlink(backing, day)
    except OSError:
        backing.rename(day)
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(RegressionExportError, match="non-link"):
        validate_regression_set(output)


def test_daily_validator_rejects_forbidden_and_extra_keys(tmp_path):
    _, output, _ = _build(tmp_path)
    payload = strict_json_loads((output / "2026-08-18.json").read_bytes())
    payload["raw_html"] = "not allowed"
    with pytest.raises(RegressionExportError, match="unexpected or missing"):
        validate_daily_export(payload)
