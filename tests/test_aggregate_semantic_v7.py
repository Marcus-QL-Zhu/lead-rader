from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def test_bridge_event_inherits_earlier_historical_anchor_in_same_paragraph():
    quote = "\u683c\u5f0f\u5854\u4e0a\u6d77\u603b\u90e8\u4e5f\u6b63\u5f0f\u542f\u7528"
    body = (
        "7\u6708\u521d\uff0c\u683c\u5f0f\u5854\u5b8c\u6210"
        "4.2\u4ebf\u5929\u4f7f+\u8f6e\u878d\u8d44\u3002"
        "\u4ec5\u4ec5\u95f4\u96944\u4e2a\u6708\uff0c"
        "\u683c\u5f0f\u5854\u5b8c\u6210\u65b0\u4e00\u8f6e\u878d\u8d44\u3002"
        "\u4ece\u5929\u4f7f\u8f6e\u5230\u5929\u4f7f+\u8f6e\uff0c"
        "\u878d\u8d44\u91d1\u989d\u5df2\u63a5\u8fd11\u4ebf\u7f8e\u91d1\u3002"
        f"\u4e0e\u6b64\u540c\u65f6\uff0c{quote}\u3002"
    )

    assert MiniMaxSemanticProcessor._event_evidence_is_historical(
        body,
        [quote],
        "2026-07-14",
    )


def test_unnamed_subfund_is_normalized_to_identifiable_parent():
    normalize = MiniMaxSemanticProcessor._normalize_investor_name

    assert normalize("\u5174\u6e58\u8d44\u672c\u65d7\u4e0b\u57fa\u91d1") == (
        "\u5174\u6e58\u8d44\u672c"
    )
    assert normalize("\u5174\u6e58\u8d44\u672c") == "\u5174\u6e58\u8d44\u672c"
