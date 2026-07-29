from ht_lead_radar.search_snapshot_dates import relative_publication_date


def test_relative_date_ignores_crawler_age_and_uses_job_age():
    value, basis = relative_publication_date(
        "Crawled: yesterday; ASML 上海市 1 个月前 成为前 25 位申请者",
        captured_at="2026-07-28T12:00:00+08:00",
    )
    assert value == "2026-06-28"
    assert basis == "relative_months_estimate"


def test_relative_date_does_not_invent_date_without_job_age():
    assert relative_publication_date(
        "Crawled: last week; Job Description",
        captured_at="2026-07-28T12:00:00+08:00",
    ) == ("", "")
