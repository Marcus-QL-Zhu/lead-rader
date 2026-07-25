from __future__ import annotations

import json
import sqlite3

from ht_lead_radar.collectors import collect_josint


def test_collect_josint_prefers_canonical_target_table_and_deduplicates(tmp_path):
    database = tmp_path / "jobs.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE canonical_jobs (
            canonical_job_id TEXT PRIMARY KEY,
            title TEXT,
            company_name TEXT,
            guessed_employer TEXT,
            location TEXT,
            jd_text TEXT,
            industry_label TEXT,
            function_label TEXT,
            target_reason TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            source_urls_json TEXT,
            is_target_job INTEGER
        )
        """
    )
    connection.executemany(
        "INSERT INTO canonical_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "job:1", "灵巧手研发总监", "示例机器人公司", None, "上海",
                "负责灵巧手研发团队和产品开发", "机器人", "研发", "总监级目标岗位",
                "2026-07-20", "2026-07-24",
                json.dumps(["https://watchjobs.net/job/1", "https://talent.com/view/1"]),
                1,
            ),
            (
                "job:2", "灵巧手研发经理", "另一家公司", None, "北京",
                "负责灵巧手项目", "机器人", "研发", "低于总监级",
                "2026-07-20", "2026-07-24", "[]", 0,
            ),
        ],
    )
    connection.commit()
    connection.close()

    evidence = collect_josint(database, "灵巧手")

    assert len(evidence) == 1
    assert evidence[0].company == "示例机器人公司"
    assert evidence[0].title == "灵巧手研发总监"
    assert evidence[0].source_url == "https://watchjobs.net/job/1"

def test_partial_canonical_schema_falls_back_instead_of_crashing(tmp_path):
    from ht_lead_radar.josint_adapter import read_canonical_evidence

    database = tmp_path / "partial.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE canonical_jobs ("
        "canonical_job_id TEXT PRIMARY KEY, title TEXT, is_target_job INTEGER)"
    )
    connection.commit()
    connection.close()

    assert read_canonical_evidence(
        database, terms=("灵巧手",), direction="灵巧手"
    ) is None
