from datetime import date

from scripts.fetch_web_news_artifacts import assess_verification


def test_assess_verification_requires_company_date_window_and_grade():
    result = assess_verification(
        company="示例科技",
        title="示例科技完成新一轮融资",
        body_text="示例科技于2026年4月8日宣布完成新一轮融资。",
        event_date_candidate="2026-04-08",
        source_grade="B",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 6, 30),
    )
    assert result["strict_evidence_ready"] is True


def test_assess_verification_rejects_search_date_not_present_in_artifact():
    result = assess_verification(
        company="示例科技",
        title="示例科技完成新一轮融资",
        body_text="示例科技宣布完成新一轮融资。",
        event_date_candidate="2026-04-08",
        source_grade="B",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 6, 30),
    )
    assert result["strict_evidence_ready"] is False
