import json
import os
from pathlib import Path
import subprocess
import sys


def test_plan_freezes_all_splits_and_keeps_jobs_out_of_news(tmp_path: Path):
    pool = tmp_path / "pool.json"
    output = tmp_path / "planned"
    pool.write_text(
        json.dumps(
            {
                "companies": [
                    {
                        "company": "甲公司",
                        "company_type": "startup_private",
                        "sector": "robotics",
                        "split": "train",
                    },
                    {
                        "company": "乙公司",
                        "company_type": "listed",
                        "sector": "semiconductor",
                        "split": "test",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/plan_snapshot_expansion.py",
            "--pool",
            str(pool),
            "--output-dir",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(
        (output / "liepin-company-manifest.json").read_text(encoding="utf-8")
    )
    news = json.loads(
        (output / "snapshot-news-tasks.json").read_text(encoding="utf-8")
    )
    assert [item["split"] for item in manifest["companies"]] == ["train", "test"]
    assert all(item["company_page_url"] is None for item in manifest["companies"])
    assert news["counts"]["tasks"] == 2
    assert news["counts"]["queries"] == 28
    assert all("Job advertisements are labels only" in task["prediction_boundary"] for task in news["tasks"])
