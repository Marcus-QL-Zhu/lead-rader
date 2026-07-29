from ht_lead_radar.backtest import role_family


def test_strategic_alliance_titles_are_ecosystem_not_generic_sales():
    assert role_family(
        "\u4e2d\u56fd\u533a\u751f\u6001\u5408\u4f5c\u4e0e"
        "\u6218\u7565\u5ba2\u6237\u526f\u603b\u88c1"
    ) == "channel_ecosystem"
    assert role_family(
        "Strategy Alliance Lead - Director / Senior Manager "
        "\u6218\u7565\u5408\u4f5c\u8d1f\u8d23\u4eba"
    ) == "channel_ecosystem"
