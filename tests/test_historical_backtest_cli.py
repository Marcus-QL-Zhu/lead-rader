import hashlib
import json

import pytest

from scripts.run_historical_backtest import _verify_uniform_label_audit


def _audit(company="company-a", artifact=None):
    artifact_fields = {}
    if artifact is not None:
        artifact_fields = {
            "artifact_path": str(artifact),
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "result_urls": [],
        }
    return {
        "company": company,
        "searched_at": "2026-07-28T20:00:00+08:00",
        "window_start": "2026-04-01",
        "window_end_exclusive": "2026-07-01",
        "searches": [
            {
                "channel": "official_careers",
                "query": f"{company} official careers Director Head",
                "executed_at": "2026-07-28T20:00:00+08:00",
                "outcome_summary": "No eligible official role found.",
                **artifact_fields,
            },
            {
                "channel": "public_web_search",
                "query": f"{company} Director Head China 2026",
                "executed_at": "2026-07-28T20:01:00+08:00",
                "outcome_summary": "No eligible public role found.",
                **artifact_fields,
            },
        ],
        "result": "no_eligible_job",
    }


def test_uniform_label_audit_requires_replayable_protocol(tmp_path):
    artifact = tmp_path / "search.json"
    artifact.write_text('{"results": []}', encoding="utf-8")
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({
        "search_protocol_version": "uniform-director-plus-v1",
        "jobs": [],
        "audits": [_audit(artifact=artifact)],
    }), encoding="utf-8")
    assert _verify_uniform_label_audit(
        path,
        ["company-a"],
        window_start="2026-04-01",
        window_end_exclusive="2026-07-01",
        eligible_job_companies=set(),
    )


def test_uniform_label_audit_rejects_unarchived_template(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({
        "search_protocol_version": "uniform-director-plus-v1",
        "jobs": [],
        "audits": [_audit()],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="replayable artifact"):
        _verify_uniform_label_audit(
            path,
            ["company-a"],
            window_start="2026-04-01",
            window_end_exclusive="2026-07-01",
            eligible_job_companies=set(),
        )


def test_uniform_label_audit_rejects_name_only_bundle(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps({"audits": [{"company": "company-a"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="uniform-director-plus-v1"):
        _verify_uniform_label_audit(
            path,
            ["company-a"],
            window_start="2026-04-01",
            window_end_exclusive="2026-07-01",
            eligible_job_companies=set(),
        )


def test_uniform_label_audit_rejects_fake_queries_and_timestamp(tmp_path):
    artifact = tmp_path / "search.json"
    artifact.write_text('{"results": []}', encoding="utf-8")
    path = tmp_path / "jobs.json"
    audit = _audit(artifact=artifact)
    audit["searches"][0]["query"] = "foo"
    audit["searched_at"] = "T"
    path.write_text(json.dumps({
        "search_protocol_version": "uniform-director-plus-v1",
        "jobs": [],
        "audits": [audit],
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        _verify_uniform_label_audit(
            path,
            ["company-a"],
            window_start="2026-04-01",
            window_end_exclusive="2026-07-01",
            eligible_job_companies=set(),
        )


def test_uniform_label_audit_result_must_match_actual_eligible_jobs(tmp_path):
    artifact = tmp_path / "search.json"
    artifact.write_text('{"results": []}', encoding="utf-8")
    path = tmp_path / "audit.json"
    audit = _audit(artifact=artifact)
    audit["result"] = "matched"
    path.write_text(json.dumps({
        "search_protocol_version": "uniform-director-plus-v1",
        "jobs": [{
            "company": "company-a",
            "title": "Manager",
            "published_at": "1900-01-01",
        }],
        "audits": [audit],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="result/jobs mismatch"):
        _verify_uniform_label_audit(
            path,
            ["company-a"],
            window_start="2026-04-01",
            window_end_exclusive="2026-07-01",
            eligible_job_companies=set(),
        )
