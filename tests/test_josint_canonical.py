from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import os

from ht_lead_radar.collectors import collect_josint
import ht_lead_radar.josint_snapshot as josint_snapshot
from ht_lead_radar.josint_adapter import open_readonly_josint


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


def test_josint_connection_is_explicitly_query_only(tmp_path):
    database = tmp_path / "readonly.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")

    connection = open_readonly_josint(database)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden (value TEXT)")
    finally:
        connection.close()


def test_wal_without_shm_never_creates_shm_beside_original(tmp_path):
    database = tmp_path / "wal.sqlite"
    with sqlite3.connect(database) as base:
        base.execute("CREATE TABLE jobs (title TEXT)")
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("INSERT INTO jobs VALUES ('director')")
    connection.commit()
    db_bytes = database.read_bytes()
    wal_path = Path(f"{database}-wal")
    wal_bytes = wal_path.read_bytes()
    connection.close()
    database.write_bytes(db_bytes)
    wal_path.write_bytes(wal_bytes)
    shm_path = Path(f"{database}-shm")
    shm_path.unlink(missing_ok=True)
    before = (database.read_bytes(), wal_path.read_bytes(), database.stat().st_mtime_ns, wal_path.stat().st_mtime_ns)
    snapshot_connection = open_readonly_josint(database)
    try:
        assert snapshot_connection.execute("SELECT title FROM jobs").fetchone()[0] == "director"
    finally:
        snapshot_connection.close()
    after = (database.read_bytes(), wal_path.read_bytes(), database.stat().st_mtime_ns, wal_path.stat().st_mtime_ns)
    assert after == before
    assert not shm_path.exists()


def test_wal_without_shm_is_recovered_consistently_from_disposable_family(tmp_path):
    database = tmp_path / "wal-loop.sqlite"
    with sqlite3.connect(database) as base:
        base.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT)")
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.executemany(
        "INSERT INTO jobs(title) VALUES (?)",
        [(f"director-{index}",) for index in range(40)],
    )
    writer.commit()
    db_bytes = database.read_bytes()
    wal_path = Path(f"{database}-wal")
    wal_bytes = wal_path.read_bytes()
    writer.close()
    # Recreate the production edge case: committed WAL frames exist, while no
    # process owns a shared-memory index beside the original database.
    database.write_bytes(db_bytes)
    wal_path.write_bytes(wal_bytes)
    shm_path = Path(f"{database}-shm")
    shm_path.unlink(missing_ok=True)

    def family_state():
        output = []
        for member in (database, wal_path, shm_path):
            if not member.exists():
                output.append(None)
                continue
            info = member.lstat()
            output.append(
                (
                    member.read_bytes(),
                    info.st_mode,
                    info.st_ino,
                    info.st_nlink,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
            )
        return tuple(output)

    before = family_state()
    for _ in range(12):
        with open_readonly_josint(database) as snapshot:
            count, latest = snapshot.execute(
                "SELECT COUNT(*), MAX(title) FROM jobs"
            ).fetchone()
            assert count == 40
            assert latest == "director-9"

    assert family_state() == before
    assert not shm_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlink/no-atime semantics")
def test_josint_snapshot_rejects_symlink_database(tmp_path):
    database = tmp_path / "real.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    linked = tmp_path / "linked.sqlite"
    linked.symlink_to(database)
    with pytest.raises(ValueError, match="symlink"):
        open_readonly_josint(linked)


def test_josint_snapshot_rejects_repeated_family_race(tmp_path, monkeypatch):
    database = tmp_path / "race.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    original = josint_snapshot._snapshot_once

    def changing(source, target):
        original(source, target)
        raise josint_snapshot._FamilyChanged("simulated concurrent writer")

    monkeypatch.setattr(josint_snapshot, "_snapshot_once", changing)
    with pytest.raises(ValueError, match="bounded retries"):
        with josint_snapshot.readonly_sqlite_snapshot(database):
            pass


@pytest.mark.parametrize("mutation", ["appear", "disappear", "replace"])
def test_family_member_mutation_after_fingerprint_is_retryable(
    tmp_path, monkeypatch, mutation
):
    database = tmp_path / "family.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    wal = Path(f"{database}-wal")
    if mutation in {"disappear", "replace"}:
        wal.write_bytes(b"old-sidecar")

    original = josint_snapshot._read_verified
    mutated = {"done": False}

    def mutate_after_fingerprint(path, target=None):
        if target is not None and not mutated["done"]:
            if mutation == "appear" and path == database:
                wal.write_bytes(b"new-sidecar")
                mutated["done"] = True
            elif path == wal:
                if mutation == "disappear":
                    wal.unlink()
                else:
                    replacement = tmp_path / "replacement"
                    replacement.write_bytes(b"replacement-sidecar")
                    os.replace(replacement, wal)
                mutated["done"] = True
        return original(path, target)

    monkeypatch.setattr(josint_snapshot, "_read_verified", mutate_after_fingerprint)
    with pytest.raises(josint_snapshot._FamilyChanged):
        josint_snapshot._snapshot_once(database, tmp_path / "copy.sqlite")


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
