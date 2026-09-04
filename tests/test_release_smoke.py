import os
from pathlib import Path
import sqlite3

import pytest

from deployment.smoke_release import (
    ReleaseSmokeError,
    _josint_adapter_smoke,
    _josint_schema_smoke,
    smoke_release,
)
from ht_lead_radar.josint_snapshot import sqlite_family_fingerprint


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _owner_kwargs():
    if os.name == "nt":
        return {}
    return {"expected_owner_uid": os.geteuid(), "expected_owner_gid": os.getegid()}


def _canonical_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE canonical_jobs (
                canonical_job_id TEXT,
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


def _legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")


@pytest.mark.parametrize("schema", ["canonical", "legacy"])
def test_josint_schema_and_actual_selected_release_adapter_smoke(tmp_path, schema):
    database = tmp_path / "jobs.sqlite"
    if schema == "canonical":
        _canonical_database(database)
        assert _josint_schema_smoke(database) == "canonical_jobs"
    else:
        _legacy_database(database)
        assert _josint_schema_smoke(database) == "jobs"

    _josint_adapter_smoke(PROJECT_ROOT, database)


def test_josint_schema_smoke_fails_closed_on_incomplete_or_symlinked_database(
    tmp_path,
):
    incomplete = tmp_path / "incomplete.sqlite"
    with sqlite3.connect(incomplete) as connection:
        connection.execute("CREATE TABLE canonical_jobs (title TEXT)")

    with pytest.raises(ReleaseSmokeError, match="incomplete"):
        _josint_schema_smoke(incomplete)

    if os.name != "nt":
        linked = tmp_path / "linked.sqlite"
        linked.symlink_to(incomplete)
        with pytest.raises(ReleaseSmokeError, match="regular"):
            _josint_schema_smoke(linked)


def test_release_smoke_rejects_live_pointer_that_resolves_elsewhere(tmp_path):
    database = tmp_path / "jobs.sqlite"
    _legacy_database(database)
    other = tmp_path / "other"
    other.mkdir()

    with pytest.raises(ReleaseSmokeError, match="does not match"):
        smoke_release(
            PROJECT_ROOT,
            josint_db=database,
            env_file=tmp_path / "not-opened.env",
            expected_realpath=other,
        )


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX ownership and modes")
def test_full_release_smoke_uses_protected_env_and_actual_adapter(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=not-used-by-smoke\n"
        "FEISHU_APP_ID=not-used-by-smoke\n"
        "FEISHU_APP_SECRET=not-used-by-smoke\n"
        "FEISHU_NOTIFY_RECEIVE_ID=not-used-by-smoke\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    _legacy_database(database)

    result = smoke_release(
        PROJECT_ROOT,
        josint_db=database,
        env_file=env_file,
        expected_realpath=PROJECT_ROOT,
        **_owner_kwargs(),
    )

    assert result["josint_schema"] == "jobs"
    assert result["josint_adapter"] == "passed"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX ownership and modes")
def test_full_smoke_does_not_create_original_shm_for_wal_database(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n", encoding="utf-8"
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as base:
        base.execute("CREATE TABLE jobs (title TEXT)")
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("INSERT INTO jobs VALUES ('director')")
    connection.commit()
    db_bytes = database.read_bytes()
    wal = Path(f"{database}-wal")
    wal_bytes = wal.read_bytes()
    connection.close()
    database.write_bytes(db_bytes)
    wal.write_bytes(wal_bytes)
    shm = Path(f"{database}-shm")
    shm.unlink(missing_ok=True)
    before = sqlite_family_fingerprint(database)
    smoke_release(PROJECT_ROOT, josint_db=database, env_file=env_file, **_owner_kwargs())
    assert sqlite_family_fingerprint(database) == before
    assert not shm.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX ownership and modes")
def test_release_smoke_fails_before_adapter_when_required_env_key_is_missing(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text("METASO_API_KEY=present\n", encoding="utf-8")
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    _legacy_database(database)

    with pytest.raises(ReleaseSmokeError, match="FEISHU_APP_ID"):
        smoke_release(
            PROJECT_ROOT,
            josint_db=database,
            env_file=env_file,
            expected_realpath=PROJECT_ROOT,
            **_owner_kwargs(),
        )


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX ownership and modes")
def test_release_smoke_rejects_secret_file_control_keys(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "FEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\nMETASO_API_KEY=x\n"
        "PYTHONPATH=/attacker\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    _legacy_database(database)

    with pytest.raises(ValueError, match="process-control"):
        smoke_release(PROJECT_ROOT, josint_db=database, env_file=env_file, **_owner_kwargs())
