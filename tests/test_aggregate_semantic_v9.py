from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def test_bridge_inside_primary_quote_backtracks_historical_chain():
    quote = "\u4e0e\u6b64\u540c\u65f6\uff0c\u683c\u5f0f\u5854\u4e0a\u6d77\u603b\u90e8\u4e5f\u6b63\u5f0f\u542f\u7528\u3002"
    body = (
        "7\u6708\u521d\uff0c\u683c\u5f0f\u5854\u5df2\u5728\u4e0a\u6d77\u5b8c\u6210\u65b0\u4e00\u8f6e\u878d\u8d44\u3002"
        "\u4ec5\u4ec5\u95f4\u9694\u5341\u5929\uff0c\u516c\u53f8\u53c8\u5ba3\u5e03\u4e86\u8fdb\u5c55\u3002"
        f"{quote}"
    )

    assert MiniMaxSemanticProcessor._event_evidence_is_historical(
        body,
        [quote],
        "2026-07-14",
    )
