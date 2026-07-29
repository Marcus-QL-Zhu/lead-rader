from datetime import date

from scripts.collect_snapshot_news_candidates import (
    _company_tokens,
    _in_window,
    _mentions_company,
    _rss_date,
)


def test_company_tokens_keep_foreign_and_chinese_core_names():
    assert "瑞萨电子" in _company_tokens("瑞萨电子（中国）")
    assert "asml" in _company_tokens("ASML（中国）")


def test_company_mention_requires_target_token():
    assert _mentions_company(
        "银河通用机器人",
        "银河通用机器人完成新融资",
        "",
    )
    assert not _mentions_company(
        "银河通用机器人",
        "另一家公司完成融资",
        "机器人行业持续升温",
    )


def test_rss_date_and_window_are_deterministic():
    value = _rss_date("Tue, 12 May 2026 08:00:00 GMT")
    assert value == "2026-05-12"
    assert _in_window(value, date(2026, 1, 1), date(2026, 6, 30))
