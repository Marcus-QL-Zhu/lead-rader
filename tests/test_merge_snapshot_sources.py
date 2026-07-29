from scripts.merge_snapshot_sources import merge_jobs, merge_news


def test_merge_news_deduplicates_company_url():
    base = {
        "window_start": "2026-01-01",
        "window_end_inclusive": "2026-06-30",
        "companies": [
            {
                "company": "示例科技",
                "results": [{"source_url": "https://example.com/a"}],
            }
        ],
    }
    result = merge_news([base, base])
    assert len(result["companies"]) == 1
    assert len(result["companies"][0]["results"]) == 1


def test_merge_jobs_deduplicates_company_url():
    base = {
        "queue": [
            {"company": "示例科技", "url": "https://example.com/job"}
        ]
    }
    assert len(merge_jobs([base, base])["queue"]) == 1
