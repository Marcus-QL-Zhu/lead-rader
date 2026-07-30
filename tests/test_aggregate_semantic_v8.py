from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def test_existing_team_roster_is_not_reemitted_as_current_executive_change():
    quote = (
        "\u8fde\u7eed\u591a\u5e74\u5168\u7403\u6392\u540d"
        "\u7b2c\u4e00\u7684\u795e\u7ecf\u79d1\u5b66\u5bb6"
        "Trevor Robbins\u52a0\u5165\u683c\u5f0f\u5854"
        "\u79d1\u5b66\u987e\u95ee\u59d4\u5458\u4f1a"
    )
    body = (
        "\u683c\u5f0f\u5854\u5df2\u5438\u5f15\u4e86"
        "\u591a\u4f4d\u56fd\u9645\u4eba\u624d\u7684\u52a0\u5165\u3002"
        "\u4f8b\u5982\uff0cBashar Badran\u52a0\u5165"
        "\u683c\u5f0f\u5854\u5e76\u62c5\u4efb\u526f\u603b\u88c1\uff1b"
        f"{quote}\u3002"
    )

    assert MiniMaxSemanticProcessor._event_evidence_is_historical(
        body,
        [quote],
        "2026-07-14",
    )
