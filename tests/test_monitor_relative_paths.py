import sqlite3

from ht_lead_radar.ops import build_daily_monitoring_report


def test_monitor_accepts_relative_database_paths(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.sqlite"
    with sqlite3.connect(runtime) as connection:
        connection.execute(
            """
            CREATE TABLE pipeline_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                current_stage TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
    monkeypatch.chdir(tmp_path)

    report = build_daily_monitoring_report(runtime_db="runtime.sqlite")

    assert report.metrics["runtime"]["available"] is True
