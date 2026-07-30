from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def test_round_normalization_collapses_descriptive_parentheses():
    assert (
        MiniMaxSemanticProcessor._normalize_round("G轮（Pre IPO轮）")
        == "G轮"
    )
    assert MiniMaxSemanticProcessor._normalize_round("Pre-A+轮") == "Pre-A+"
    assert MiniMaxSemanticProcessor._normalize_round("战略轮") == "战略融资"
