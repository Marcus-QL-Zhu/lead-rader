from ht_lead_radar.historical_job_triage import (
    company_aliases, parse_publication_date, triage_candidate,
)


def test_company_aliases_expand_bilingual_names():
    aliases = company_aliases("海柔创新 Hai Robotics", ["海柔"])
    assert "海柔创新" in aliases
    assert "hai" in aliases
    assert "海柔" in aliases


def test_parse_publication_date_accepts_chinese_date():
    assert parse_publication_date("2025年12月23日").isoformat() == "2025-12-23"


def test_liepin_search_landing_is_only_medium_priority():
    result = triage_candidate(
        company="文远知行 WeRide",
        query='"文远知行 WeRide" 总监招聘 after:2024-01-01 before:2026-07-01',
        title="【文远知行WeRide法务总监】招聘",
        snippet="文远知行正在招聘法务总监",
        url="https://www.liepin.com/s/example/",
        published_at="2025年12月23日",
    )
    assert result["review_priority"] == "medium"
    assert result["within_query_window"] is True
    assert result["verification_status"] == "unverified_search_candidate"


def test_exact_job_page_can_be_high_priority():
    result = triage_candidate(
        company="Momenta",
        query='"Momenta" Director after:2024-01-01 before:2026-07-01',
        title="Momenta 产品总监招聘", snippet="Momenta 招聘产品总监",
        url="https://www.liepin.com/job/123456.shtml", published_at="2025年05月03日",
    )
    assert result["review_priority"] == "high"
    assert result["direct_job_page"] is True


def test_unrelated_title_with_query_terms_only_in_snippet_is_low_priority():
    result = triage_candidate(
        company="优必选", query='"优必选" 总监招聘 after:2024-01-01 before:2026-07-01',
        title="京东电商运营总监", snippet="优必选也有招聘信息",
        url="https://www.liepin.com/a/57704753.shtml", published_at="2024年08月19日",
    )
    assert result["review_priority"] == "low"


def test_out_of_window_result_is_low_priority():
    result = triage_candidate(
        company="比亚迪",
        query='"比亚迪" 总监招聘 after:2024-01-01 before:2026-07-01',
        title="比亚迪招聘专场", snippet="销售总监岗位",
        url="https://www.liepin.com/s/example/", published_at="2026年07月28日",
    )
    assert result["review_priority"] == "low"
    assert result["within_query_window"] is False


def test_product_named_director_is_not_treated_as_job():
    result = triage_candidate(
        company="DataMesh",
        query='"DataMesh" Director 招聘 after:2024-01-01 before:2026-07-01',
        title="DataMesh Director 新版本发布",
        snippet="产品版本正式发布", url="https://example.com/product",
        published_at="2024年11月19日",
    )
    assert result["review_priority"] == "low"
    assert any(str(x).startswith("non_job_title:") for x in result["reasons"])
