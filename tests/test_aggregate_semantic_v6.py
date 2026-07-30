from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def test_explicit_year_month_before_publish_month_is_historical():
    assert MiniMaxSemanticProcessor._is_historical_event_quote(
        "2026\u5e741\u6708\uff0c\u5f6d\u96f7\u6b63\u5f0f"
        "\u521b\u529e\u4e86\u683c\u5f0f\u5854",
        "2026-07-14",
    )


def test_current_looking_secondary_quote_cannot_revive_historical_primary_quote():
    primary = (
        "\u4ec5\u4ec5\u95f4\u96944\u4e2a\u6708\uff0c"
        "\u683c\u5f0f\u5854\u5b8c\u62104.2\u4ebf"
        "\u5929\u4f7f+\u8f6e\u878d\u8d44"
    )
    secondary = (
        "\u4ece\u5929\u4f7f\u8f6e\u5230\u5929\u4f7f+\u8f6e\uff0c"
        "\u683c\u5f0f\u5854\u878d\u8d44\u91d1\u989d"
        "\u5df2\u63a5\u8fd11\u4ebf\u7f8e\u91d1"
    )
    body = (
        "7\u6708\u521d\uff0c\u683c\u5f0f\u5854\u5b8c\u6210"
        "\u65b0\u4e00\u8f6e\u878d\u8d44\u3002"
        f"{primary}\u3002{secondary}\u3002"
    )

    assert MiniMaxSemanticProcessor._event_evidence_is_historical(
        body,
        [primary, secondary],
        "2026-07-14",
    )
