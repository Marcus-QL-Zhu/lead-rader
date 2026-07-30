import pytest

from ht_lead_radar.aggregate_adapters.semantic import (
    MiniMaxSemanticProcessor,
    SemanticOutputError,
)


def test_malformed_llm_json_is_repaired_before_schema_validation():
    payload = MiniMaxSemanticProcessor._parse_json(
        """
        ```json
        {'events': [], 'ambiguities': ['syntax repaired',],}
        ```
        """
    )

    assert payload == {
        "events": [],
        "ambiguities": ["syntax repaired"],
    }


def test_json_repair_does_not_bypass_object_contract():
    with pytest.raises(SemanticOutputError, match="must be an object"):
        MiniMaxSemanticProcessor._parse_json("['not', 'an', 'object']")
