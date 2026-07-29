from ht_lead_radar.historical_job_triage import triage_candidate


def test_linkedin_jobs_view_is_a_direct_job_page():
    result = triage_candidate(
        company="ASML（中国）",
        aliases=["ASML"],
        query="after:2026-05-01 before:2026-08-01",
        title="ASML正在招聘Head of CS China DUV/YS（上海市）| 领英",
        snippet="ASML 上海市 1 个月前",
        url="https://cn.linkedin.com/jobs/view/head-of-cs-at-asml-123",
        published_at="2026-06-28",
    )
    assert result["direct_job_page"] is True
    assert result["review_priority"] == "high"
