from ht_lead_radar.funding_benchmark import (
    FundingCandidate,
    deterministic_sample,
    is_announced_funding_title,
)


def test_funding_title_filter_excludes_false_positive_finance_and_unfunded_projects():
    assert is_announced_funding_title("同舟智航完成数千万元种子轮融资")
    assert is_announced_funding_title("Natural raises $30M to reinvent payments")
    assert not is_announced_funding_title("某项目 未融资 新材料研发")
    assert not is_announced_funding_title("18只高股息股7月以来获得融资净买入")
    assert not is_announced_funding_title("6月中国一级市场投融资月报")
    assert not is_announced_funding_title(
        "某公司完成A轮融资",
        "https://pitchhub.36kr.com/project/123",
    )


def test_deterministic_sample_is_repeatable_and_deduplicates_titles():
    rows = [
        FundingCandidate(f"公司{i}完成A轮融资", f"https://x/{i}", "source", "Source", "")
        for i in range(12)
    ]
    rows.append(rows[0])

    first = deterministic_sample(rows, size=10, seed=7)
    second = deterministic_sample(reversed(rows), size=10, seed=7)

    assert first == second
    assert len({item.title for item in first}) == 10
