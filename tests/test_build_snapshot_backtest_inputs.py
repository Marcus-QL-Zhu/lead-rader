from datetime import date

from scripts.build_snapshot_backtest_inputs import build_snapshot


def test_snapshot_separates_precursor_evidence_from_future_job_labels():
    pool = {"companies": [{"company": "示例科技", "split": "test"}]}
    news = {
        "companies": [
            {
                "company": "示例科技",
                "results": [
                    {
                        "strict_evidence_ready": True,
                        "event_date_candidate": "2026-04-01",
                        "event_type": "leadership_change",
                        "title": "任命新负责人",
                        "source_url": "https://example.com/news",
                        "source_grade": "A",
                        "content_sha256": "a" * 64,
                        "storage_path": "data/a.html",
                    },
                    {
                        "strict_evidence_ready": True,
                        "event_date_candidate": "2026-05-01",
                        "event_type": "funding",
                        "title": "未来证据",
                        "source_url": "https://example.com/leak",
                        "source_grade": "A",
                        "content_sha256": "b" * 64,
                        "storage_path": "data/b.html",
                    },
                ],
            }
        ]
    }
    jobs = {
        "queue": [
            {
                "company": "示例科技",
                "review_priority": "high",
                "direct_job_page": True,
                "title": "示例科技正在招聘商业化总监",
                "publication_date_candidate": "2026-06-01",
                "publication_date_basis": "relative_months_estimate",
                "url": "https://linkedin.com/jobs/view/1",
                "result_sha256": "c" * 64,
            }
        ]
    }
    result = build_snapshot(
        pool=pool,
        news=news,
        jobs=jobs,
        cutoff=date(2026, 4, 30),
        horizon_end=date(2026, 7, 31),
    )
    assert len(result["evidence"]) == 1
    assert len(result["job_label_candidates"]) == 1
    assert result["job_label_candidates"][0]["source_url"] not in str(
        result["evidence"]
    )


def test_snapshot_excludes_manager_only_job_from_test_labels():
    result = build_snapshot(
        pool={"companies": [{"company": "示例科技", "split": "test"}]},
        news={"companies": []},
        jobs={
            "queue": [
                {
                    "company": "示例科技",
                    "review_priority": "high",
                    "direct_job_page": True,
                    "title": "示例科技正在招聘销售经理",
                    "publication_date_candidate": "2026-06-01",
                    "url": "https://linkedin.com/jobs/view/1",
                    "result_sha256": "c" * 64,
                }
            ]
        },
        cutoff=date(2026, 4, 30),
        horizon_end=date(2026, 7, 31),
    )
    assert result["job_label_candidates"] == []
