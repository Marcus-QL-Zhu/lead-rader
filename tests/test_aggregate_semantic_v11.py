from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def test_quote_grounding_returns_original_layout_span():
    source = (
        "\u6df1\u5733\u5e02\u57c3\u82af\u534a\u5bfc\u4f53\u79d1\u6280\u6709\u9650\u516c\u53f8"
        "\uff08\u7b80\u79f0 \u201c\u57c3\u82af\u534a\u5bfc\u4f53\u201d\uff09 "
        "\u53d1\u6587\u5ba3\u5e03\uff0c\u8fd1\u65e5\u5176 "
        "\u5df2\u987a\u5229\u5b8c\u6210B+\u8f6e\u878d\u8d44 \uff0c "
        "\u603b\u878d\u8d44\u89c4\u6a21\u8fd110\u4ebf\u5143 \u3002"
    )
    model_quote = (
        "\u6df1\u5733\u5e02\u57c3\u82af\u534a\u5bfc\u4f53\u79d1\u6280\u6709\u9650\u516c\u53f8"
        '\uff08\u7b80\u79f0"\u57c3\u82af\u534a\u5bfc\u4f53"\uff09'
        "\u53d1\u6587\u5ba3\u5e03\uff0c\u8fd1\u65e5\u5176"
        "\u5df2\u987a\u5229\u5b8c\u6210B+\u8f6e\u878d\u8d44\uff0c"
        "\u603b\u878d\u8d44\u89c4\u6a21\u8fd110\u4ebf\u5143\u3002"
    )

    assert MiniMaxSemanticProcessor._ground_quote(source, model_quote) == source


def test_quote_grounding_rejects_changed_fact():
    source = "\u67d0\u516c\u53f8\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\u3002"
    changed = "\u67d0\u516c\u53f8\u5b8c\u62102\u4ebf\u5143A\u8f6e\u878d\u8d44\u3002"

    assert MiniMaxSemanticProcessor._ground_quote(source, changed) == ""
