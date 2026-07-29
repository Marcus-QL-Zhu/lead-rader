import hashlib

import pytest

from ht_lead_radar.historical_label_quality import verify_historical_job_labels


SOURCE_TEXT = "Acme 制造总监；领导制造团队并对工厂运营结果负责；雇主字段为Acme。"


def _job(**overrides):
    value = {
        "company": "Acme",
        "title": "制造总监",
        "scope_evidence": "领导制造团队并对工厂运营结果负责",
        "employer_evidence": "雇主字段为Acme",
        "source_excerpt": SOURCE_TEXT,
        "publication_interval_start": "2026-06-01",
        "publication_interval_end_exclusive": "2026-06-03",
    }
    value.update(overrides)
    return value


def test_v23_requires_complete_replayable_source_label(tmp_path):
    artifact = tmp_path / "job.txt"
    artifact.write_text(SOURCE_TEXT, encoding="utf-8")
    job = _job(
        source_artifact_path=artifact.name,
        source_artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )
    assert verify_historical_job_labels(
        {
            "label_quality_protocol": "v23-employer-scope-date-complete",
            "jobs": [job],
        },
        window_start="2026-05-01",
        window_end_exclusive="2026-08-01",
        artifact_root=tmp_path,
    )


def test_conditional_head_without_source_scope_is_rejected():
    with pytest.raises(ValueError, match="source-backed scope"):
        verify_historical_job_labels(
            {
                "label_quality_protocol": "v21-source-backed-seniority",
                "jobs": [_job(title="机器人创意设计负责人", scope_evidence="")],
            },
            window_start="2026-05-01",
            window_end_exclusive="2026-08-01",
        )


def test_unarchived_source_is_rejected():
    with pytest.raises(ValueError, match="replayable source artifact"):
        verify_historical_job_labels(
            {
                "label_quality_protocol": "v21-source-backed-seniority",
                "jobs": [_job()],
            },
            window_start="2026-05-01",
            window_end_exclusive="2026-08-01",
        )


def test_relative_date_interval_must_be_wholly_inside_window(tmp_path):
    artifact = tmp_path / "job.txt"
    artifact.write_text(SOURCE_TEXT, encoding="utf-8")
    with pytest.raises(ValueError, match="date interval"):
        verify_historical_job_labels(
            {
                "label_quality_protocol": "v22-bounded-relative-dates",
                "jobs": [
                    _job(
                        source_artifact_path=artifact.name,
                        source_artifact_sha256=hashlib.sha256(
                            artifact.read_bytes()
                        ).hexdigest(),
                        publication_interval_start="2026-04-20",
                        publication_interval_end_exclusive="2026-05-20",
                    )
                ],
            },
            window_start="2026-05-01",
            window_end_exclusive="2026-08-01",
            artifact_root=tmp_path,
        )
