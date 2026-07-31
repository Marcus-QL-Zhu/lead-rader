import pytest

from ht_lead_radar.aggregate_adapters.semantic import (
    MiniMaxSemanticProcessor,
    SemanticOutputError,
)


def test_malformed_llm_json_requires_a_model_repair_attempt():
    with pytest.raises(SemanticOutputError, match="invalid JSON"):
        MiniMaxSemanticProcessor._parse_json(
            """
            ```json
            {'events': [], 'ambiguities': ['syntax repaired',],}
            ```
            """
        )


def test_json_repair_does_not_bypass_object_contract():
    with pytest.raises(SemanticOutputError, match="must be an object"):
        MiniMaxSemanticProcessor._parse_json('["not", "an", "object"]')


def test_syntax_repair_is_available_only_after_model_repair_attempt():
    payload = MiniMaxSemanticProcessor._parse_json(
        '{"events": [], "ambiguities": ["summary says "quoted" text"]}',
        allow_syntax_repair=True,
    )

    assert payload["events"] == []
    assert payload["ambiguities"]
