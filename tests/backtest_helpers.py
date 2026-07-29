from ht_lead_radar.backtest import (
    HISTORICAL_PROMPT_VERSION,
    HISTORICAL_TEMPORAL_EMBARGO,
    _anonymize_prediction_packet,
    _stable_hash,
)
from ht_lead_radar.company_demand_v2 import (
    COMPANY_DEMAND_SYSTEM_PROMPT,
    build_single_company_demand_prompt,
)


def auditable_snapshot(
    *,
    company,
    company_type,
    analyses,
    cutoff="2026-05-01",
):
    packet = {
        "lead_index": 1,
        "company": company,
        "direction": "test",
        "simulated_as_of": cutoff,
        "evidence": [],
        "known_context": {},
        "company_type": company_type,
    }
    system_prompt = (
        COMPANY_DEMAND_SYSTEM_PROMPT + "\n\n" + HISTORICAL_TEMPORAL_EMBARGO
    )
    model_packet = _anonymize_prediction_packet(packet)
    user_prompt = (
        HISTORICAL_TEMPORAL_EMBARGO
        + "\n\n"
        + build_single_company_demand_prompt(model_packet, max_roles=5)
    )
    response = "{}"
    return {
        "manifest": {
            "cutoff": cutoff,
            "horizon_months": 3,
            "workforce_precursors_enabled": False,
            "prediction_inputs_exclude_job_ads": True,
            "snapshot_schema_version": 3,
            "synthetic_test_snapshot": True,
            "prompt_version": HISTORICAL_PROMPT_VERSION,
            "prediction_packets_sha256": _stable_hash([packet]),
            "model_packets_sha256": _stable_hash([model_packet]),
            "system_prompt_sha256": _stable_hash(system_prompt),
            "runner": {
                "provider": "test",
                "model": "test-model",
                "api_kind": "openai-completions",
            },
        },
        "prediction_packets": [packet],
        "model_packets": [model_packet],
        "prompt_audit": [
            {
                "company": company,
                "session_id": "test",
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response": response,
                "system_prompt_sha256": _stable_hash(system_prompt),
                "user_prompt_sha256": _stable_hash(user_prompt),
                "response_sha256": _stable_hash(response),
            }
        ],
        "company_types": {company: company_type},
        "analyses": analyses,
        "failures": [],
    }
