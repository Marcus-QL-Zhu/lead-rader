from ht_lead_radar.liepin_guest import (
    FetchedPage,
    collect_company,
    parse_company_page,
    parse_job_detail,
)


COMPANY_HTML = """
<html><body>
<h1>测试机器人有限公司</h1>
<a class="job-card" href="https://m.liepin.com/job/123.shtml?x=1">
  <h3><div class="job-title"><span class="ellipsis">制造总监</span></div><small>50-70k</small></h3>
  <label>上海</label><label>10年以上</label>
</a>
<a class="job-card" href="/job/123.shtml"><h3><span>制造总监</span></h3></a>
<a class="job-card" href="/job/456.shtml">
  <h3><div class="job-title"><span class="ellipsis">高级算法专家</span></div></h3>
</a>
</body></html>
"""


def page(url: str, html: str, captured_at: str = "2026-07-28T12:00:00+08:00"):
    return FetchedPage(url, html, captured_at, "a" * 64)


def test_company_parser_deduplicates_jobs_and_filters_director_plus():
    result = parse_company_page(
        page("https://m.liepin.com/company/1/", COMPANY_HTML),
        company="测试",
    )
    assert result["liepin_company_name"] == "测试机器人有限公司"
    assert len(result["jobs"]) == 2
    assert result["jobs"][0]["title"] == "制造总监"
    assert result["jobs"][0]["job_url"] == "https://m.liepin.com/job/123.shtml"
    assert result["jobs"][0]["eligible_director_plus"] is True
    assert result["jobs"][1]["eligible_director_plus"] is False


def test_detail_parser_retains_update_text_without_inventing_published_at():
    detail = parse_job_detail(
        page(
            "https://m.liepin.com/job/123.shtml",
            """
            <span class="update-time">7月27日更新</span>
            <div data-selector="job-intro-content"><p>负责团队搭建</p><p>管理预算</p></div>
            """,
        )
    )
    assert detail["displayed_update_text"] == "7月27日更新"
    assert "负责团队搭建" in detail["description"]
    assert "published_at" not in detail


def test_collect_company_fetches_details_only_for_director_plus():
    fetched: list[str] = []

    def fake_fetch(url: str) -> FetchedPage:
        fetched.append(url)
        if "/company/" in url:
            return page(url, COMPANY_HTML)
        return page(url, '<span class="update-time">今天更新</span>')

    result = collect_company(
        company="测试",
        company_page_url="https://m.liepin.com/company/1/",
        fetcher=fake_fetch,
        delay_seconds=0,
    )
    assert fetched == [
        "https://m.liepin.com/company/1/",
        "https://m.liepin.com/job/123.shtml",
    ]
    assert result["jobs"][0]["detail_observed_at"] == "2026-07-28T12:00:00+08:00"
    assert "detail_observed_at" not in result["jobs"][1]
