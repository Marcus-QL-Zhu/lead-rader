from ht_lead_radar.feishu_notify import build_summary


def test_daily_summary_includes_dedicated_aggregate_health(tmp_path):
    report = {
        "manifest": {
            "run_id": "run-aggregate",
            "as_of": "2026-07-29",
            "direction": "hardtech",
            "source_summary": {
                "failures": [],
                "runs": [
                    {
                        "provider": "reusable-source-packs",
                        "run_summary": {
                            "dedicated_aggregate": {
                                "source_count": 10,
                                "healthy_count": 9,
                                "failed_count": 1,
                                "open_dead_letter_count": 2,
                            }
                        },
                    }
                ],
            },
        },
        "leads": [],
    }

    text = build_summary(
        run_date="2026-07-29",
        direction="hardtech",
        task_exit_code=0,
        report_path=tmp_path / "report.json",
        report=report,
    )

    assert "\u4e13\u5c5e\u805a\u5408\u4fe1\u6e90\uff1a10 \u4e2a" in text
    assert "\u5065\u5eb7 9" in text
    assert "\u5f02\u5e38 1" in text
    assert "\u5f85\u5904\u7406 2" in text


def test_replayed_summary_uses_persisted_dedicated_health(tmp_path):
    report = {
        "manifest": {
            "run_id": "run-replayed",
            "as_of": "2026-07-30",
            "direction": "hardtech",
            "source_summary": {
                "failures": [],
                "runs": [
                    {
                        "provider": "reusable-source-packs",
                        "run_summary": {},
                        "health": {
                            "dedicated_aggregate": {
                                "source_count": 10,
                                "healthy_count": 10,
                                "failed_count": 0,
                                "open_dead_letter_count": 1,
                            }
                        },
                    }
                ],
            },
        },
        "leads": [],
    }

    text = build_summary(
        run_date="2026-07-30",
        direction="hardtech",
        task_exit_code=0,
        report_path=tmp_path / "report.json",
        report=report,
    )

    assert "\u4e13\u5c5e\u805a\u5408\u4fe1\u6e90\uff1a10 \u4e2a\uff0c\u5065\u5eb7 10\uff0c\u5f02\u5e38 0\uff0c\u5f85\u5904\u7406 1" in text