from scripts.normalize_web_job_batches import build_queue


def test_build_queue_never_promotes_search_result_to_label():
    source = {
        "batches": [
            {
                "captured_at": "2026-07-28T12:00:00+08:00",
                "companies": ["示例科技"],
                "raw_result": (
                    "示例科技招聘商业化总监 "
                    "(https://jobs.example.com/job/123)\n"
                    "Published: 2026-05-09; 示例科技招聘商业化总监"
                ),
            }
        ]
    }
    queue = build_queue(
        source,
        aliases_by_company={"示例科技": []},
        window_start="2026-01-01",
        window_end_exclusive="2026-07-01",
    )

    assert len(queue) == 1
    assert queue[0]["company"] == "示例科技"
    assert queue[0]["within_query_window"] is True
    assert queue[0]["verification_status"] == "unverified_search_candidate"
    assert "label" not in queue[0]
