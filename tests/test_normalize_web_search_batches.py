from scripts.normalize_web_search_batches import _blocks


def test_blocks_parse_web_connector_result_format():
    value = """银河通用完成融资 (https://example.com/a)
Published: 3 months ago; 2026年3月2日完成融资
--------------------------------------------------------------------------------
开普勒完成融资 (https://example.com/b)
2026年4月8日完成A轮
"""
    assert _blocks(value) == [
        (
            "银河通用完成融资",
            "https://example.com/a",
            "Published: 3 months ago; 2026年3月2日完成融资\n"
            "--------------------------------------------------------------------------------",
        ),
        (
            "开普勒完成融资",
            "https://example.com/b",
            "2026年4月8日完成A轮",
        ),
    ]
