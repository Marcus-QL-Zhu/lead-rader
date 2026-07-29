import json
import os
from pathlib import Path
import subprocess
import sys


def test_normalizer_uses_observation_time_and_director_scope(tmp_path: Path):
    source = tmp_path / "browser.json"
    output = tmp_path / "normalized.json"
    source.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-28T01:00:00.000Z",
                "companies": [
                    {
                        "company": "测试公司",
                        "liepin_company_name": "测试公司有限公司",
                        "company_page_url": "https://m.liepin.com/company/1/",
                        "jobs": [
                            {
                                "title": "研发总监",
                                "card_text": "研发总监 50-70k 昨天",
                                "job_url": "https://m.liepin.com/job/1.shtml",
                            },
                            {
                                "title": "算法专家",
                                "card_text": "算法专家 40-60k 昨天",
                                "job_url": "https://m.liepin.com/job/2.shtml",
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/normalize_liepin_browser_export.py",
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    jobs = payload["companies"][0]["jobs"]
    assert jobs[0]["eligible_director_plus"] is True
    assert jobs[1]["eligible_director_plus"] is False
    assert jobs[0]["observed_at"] == "2026-07-28T01:00:00.000Z"
    assert "published_at" not in jobs[0]
