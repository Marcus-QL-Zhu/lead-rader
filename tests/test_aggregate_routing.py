import pytest

from ht_lead_radar.aggregate_adapters.routing import HARDTECH_CUE


@pytest.mark.parametrize(
    "text",
    [
        "\u673a\u68b0\u8bbe\u5907",
        "\u7535\u6c14\u81ea\u52a8\u5316",
        "\u8f6f\u4ef6\u7b97\u6cd5",
        "\u534a\u5bfc\u4f53\u82af\u7247",
        "\u5546\u4e1a\u822a\u5929\u706b\u7bad",
        "\u6838\u805a\u53d8\u80fd\u6e90",
        "\u751f\u7269\u533b\u836f",
        "\u91cf\u5b50\u8ba1\u7b97",
    ],
)
def test_broad_hardtech_router_covers_core_technical_domains(text):
    assert HARDTECH_CUE.search(text)


def test_broad_hardtech_router_does_not_match_generic_financial_tape():
    assert not HARDTECH_CUE.search(
        "\u6caa\u6307\u6536\u76d8\u4e0a\u6da8\uff0c"
        "\u4e24\u5e02\u6210\u4ea4\u989d\u6269\u5927"
    )
