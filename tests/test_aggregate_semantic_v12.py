from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def test_total_funding_scale_is_cumulative_context():
    text = "\u603b\u878d\u8d44\u89c4\u6a21\u8fd110\u4ebf\u5143"

    assert MiniMaxSemanticProcessor._is_cumulative_context(
        text,
        "\u8fd110\u4ebf\u5143",
    )
