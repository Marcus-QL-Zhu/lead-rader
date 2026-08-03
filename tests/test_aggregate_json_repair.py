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


def test_fenced_json_is_parsed_without_greedy_regex():
    payload = MiniMaxSemanticProcessor._parse_json(
        "```json\n{\"events\": [], \"ambiguities\": []}\n```"
    )

    assert payload == {"events": [], "ambiguities": []}


def test_oversized_semantic_response_fails_closed():
    with pytest.raises(SemanticOutputError, match="maximum response size"):
        MiniMaxSemanticProcessor._parse_json(
            "x" * 256_001
        )


def test_json_with_explanatory_text_preserves_brace_fallback():
    payload = MiniMaxSemanticProcessor._parse_json(
        'Here is the JSON: {"events": [], "ambiguities": []}'
    )

    assert payload == {"events": [], "ambiguities": []}


def test_oversized_syntax_repair_fails_closed():
    malformed = '{"events": [' + ('{' * 32_000) + ('}' * 32_000) + '}'
    with pytest.raises(SemanticOutputError, match="syntax repair size limit"):
        MiniMaxSemanticProcessor._parse_json(
            malformed,
            allow_syntax_repair=True,
        )
