import json
from datetime import date

import pytest

from ht_lead_radar.backtest import (
    BacktestConfig,
    HistoricalJob,
    evidence_before_cutoff,
    run_historical_predictions,
    validate_predictions,
)
from ht_lead_radar.models import Evidence


class _Config:
    provider = "test-provider"
    model = "test-model"
    api_kind = "openai-completions"
    temperature = 0.0


class _Runner:
    config = _Config()

    def run(self, prompt, *, session_id, system_prompt=""):
        del session_id, system_prompt
        company = json.loads(
            prompt.split("\u516c\u53f8\u4e8b\u5b9e\u5305\uff1a", 1)[1]
            .split("\n\n\u4efb\u52a1\uff1a", 1)[0]
        )["company"]
        return json.dumps(
            {
                "lead_index": 1,
                "company": company,
                "stage_transition": "\u8bc1\u636e\u4e0d\u8db3",
                "organizational_gaps": [],
                "role_hypotheses": [],
                "watch_for": ["\u89c2\u5bdf\u540e\u7eed\u8fd0\u8425\u4fe1\u53f7"],
            },
            ensure_ascii=False,
        )


def _evidence(**overrides):
    values = {
        "company": "audit-company",
        "event_type": "funding",
        "phase": "strategy_capital",
        "event_date": "2026-03-01",
        "title": "Series A",
        "snippet": "Capital for product and delivery.",
        "source_url": "https://example.com/funding",
        "source_name": "example",
        "source_grade": "A",
        "direction": "robotics",
        "published_at": "2026-03-02",
        "company_type": "startup_private",
        "source_kind": "company_official",
        "source_excerpt": "Capital was raised for product and delivery.",
    }
    values.update(overrides)
    return Evidence(**values)


def test_public_availability_not_event_date_controls_cutoff():
    config = BacktestConfig(cutoff=date(2026, 4, 1))
    values = [
        _evidence(published_at=""),
        _evidence(source_url="https://example.com/late", observed_at="2026-04-02"),
        _evidence(
            source_url="https://example.com/conflict",
            published_at="2026-04-02",
            observed_at="2026-03-01",
        ),
        _evidence(source_url="https://example.com/ok"),
    ]

    selected = evidence_before_cutoff(values, config)

    assert [item.source_url for item in selected] == ["https://example.com/ok"]


def test_recruiting_provenance_is_fail_closed_even_with_operating_event_type():
    config = BacktestConfig(cutoff=date(2026, 4, 1))
    values = [
        _evidence(
            event_type="factory_or_capacity",
            title="新工厂招聘机械工程师和软件专家",
        ),
        _evidence(
            event_type="factory_or_capacity",
            source_kind="ats",
            title="新工厂投产",
        ),
        _evidence(
            event_type="factory_or_capacity",
            is_recruiting_input=True,
            title="新工厂投产",
        ),
        _evidence(
            event_type="factory_or_capacity",
            source_kind="",
            title="New plant opening while we are hiring senior engineers",
        ),
        _evidence(
            event_type="factory_or_capacity",
            title="New plant now hiring 200 engineers",
        ),
        _evidence(
            event_type="factory_or_capacity",
            title="Applications open for new factory positions",
        ),
        _evidence(
            event_type="factory_or_capacity",
            title="Applications are now open for factory roles",
        ),
        _evidence(
            event_type="factory_or_capacity",
            title="Application is open for plant leadership",
        ),
        _evidence(
            event_type="factory_or_capacity",
            title="The new plant recruits engineers",
        ),
    ]
    assert evidence_before_cutoff(values, config) == []


def test_snapshot_freezes_packets_prompts_hashes_and_model_metadata():
    snapshot = run_historical_predictions(
        [_evidence(source_excerpt="audit-company raised capital for delivery.")],
        BacktestConfig(cutoff=date(2026, 4, 1)),
        _Runner(),
    )

    assert snapshot["manifest"]["snapshot_schema_version"] == 3
    assert snapshot["manifest"]["runner"] == {
        "provider": "test-provider",
        "model": "test-model",
        "api_kind": "openai-completions",
        "temperature": 0.0,
    }
    assert snapshot["prediction_packets"][0]["evidence"][0]["available_at"] == (
        "2026-03-02"
    )
    assert snapshot["prediction_packets"][0]["company_type"] == "startup_private"
    assert snapshot["prediction_packets"][0]["evidence"][0]["fact"] == (
        "audit-company raised capital for delivery."
    )
    assert snapshot["prompt_audit"][0]["response"]
    assert "\u7981\u6b62\u4f7f\u7528" in snapshot["prompt_audit"][0]["system_prompt"]
    prompt = snapshot["prompt_audit"][0]["user_prompt"]
    assert "audit-company" not in prompt
    assert "example.com" not in prompt
    assert snapshot["model_packets"][0]["company"] == "Candidate-001"


def test_validation_rejects_manifest_bypass_and_packet_tampering():
    snapshot = run_historical_predictions(
        [_evidence()],
        BacktestConfig(cutoff=date(2026, 4, 1)),
        _Runner(),
    )
    jobs = [
        HistoricalJob(
            company="audit-company",
            title="Product Director",
            description="Director-level product responsibility",
            published_at="2026-05-01",
            source_url="https://example.com/job",
        )
    ]

    invalid = dict(snapshot)
    invalid["manifest"] = {
        **snapshot["manifest"],
        "workforce_precursors_enabled": True,
    }
    with pytest.raises(ValueError, match="workforce"):
        validate_predictions(invalid, jobs)

    tampered = dict(snapshot)
    tampered["prediction_packets"] = [
        {**snapshot["prediction_packets"][0], "company": "changed"}
    ]
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_predictions(tampered, jobs)

    company_type_tampered = dict(snapshot)
    company_type_tampered["company_types"] = {"audit-company": "foreign"}
    with pytest.raises(ValueError, match="company_types"):
        validate_predictions(company_type_tampered, jobs)

    response_tampered = dict(snapshot)
    response_tampered["prompt_audit"] = [
        {**snapshot["prompt_audit"][0], "response": "changed"}
    ]
    with pytest.raises(ValueError, match="prompt audit hash mismatch"):
        validate_predictions(response_tampered, jobs)
    analysis_tampered = dict(snapshot)
    analysis_tampered["analyses"] = [
        {**snapshot["analyses"][0], "stage_transition": "changed"}
    ]
    with pytest.raises(ValueError, match="stored analysis differs"):
        validate_predictions(analysis_tampered, jobs)


def test_historical_company_type_must_be_frozen_and_valid():
    with pytest.raises(ValueError, match="company_type"):
        run_historical_predictions(
            [_evidence(company_type="")],
            BacktestConfig(cutoff=date(2026, 4, 1)),
            _Runner(),
        )


def test_historical_source_excerpt_hash_must_match():
    with pytest.raises(ValueError, match="content hash mismatch"):
        run_historical_predictions(
            [_evidence(content_sha256="0" * 64)],
            BacktestConfig(cutoff=date(2026, 4, 1)),
            _Runner(),
        )

def test_historical_job_content_hash_must_match():
    snapshot = run_historical_predictions(
        [_evidence()],
        BacktestConfig(cutoff=date(2026, 4, 1)),
        _Runner(),
    )
    jobs = [
        HistoricalJob(
            company="audit-company",
            title="Product Director",
            description="Director-level product responsibility",
            published_at="2026-05-01",
            source_url="https://example.com/job",
            content_sha256="0" * 64,
        )
    ]

    with pytest.raises(ValueError, match="job content hash mismatch"):
        validate_predictions(snapshot, jobs)
