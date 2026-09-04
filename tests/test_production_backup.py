import json
import os
from pathlib import Path
import sqlite3
import subprocess

from ht_lead_radar.cli_v2 import (
    PRODUCTION_MATERIAL_DATABASES,
    PRODUCTION_SOURCE_MANIFESTS,
    main,
)


GIT_SHA = "a" * 40
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sqlite(path: Path, value: str = "value") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _production_inputs(root: Path) -> None:
    for index, name in enumerate(PRODUCTION_MATERIAL_DATABASES):
        _sqlite(root / name, f"db-{index}")
    for index, name in enumerate(PRODUCTION_SOURCE_MANIFESTS):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"manifest": index}, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


def test_production_backup_is_complete_private_canonical_and_restorable(
    tmp_path, monkeypatch, capsys
):
    _production_inputs(tmp_path)
    _sqlite(tmp_path / "data" / "additional-material.sqlite3", "extra")
    monkeypatch.chdir(tmp_path)

    assert main(["backup", "--backup-dir", "backups", "--git-sha", GIT_SHA]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["verified"] is True
    assert summary["item_count"] == len(PRODUCTION_MATERIAL_DATABASES) + 1 + len(
        PRODUCTION_SOURCE_MANIFESTS
    )
    manifest_path = Path(summary["backup_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["git_sha"] == GIT_SHA
    assert manifest["allow_missing_nonproduction"] is False
    assert manifest_path.read_bytes() == (
        json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    names = {Path(item["source_path"]).name for item in manifest["items"]}
    assert "talent-pool.sqlite" in names
    assert "feishu-notifications.sqlite" in names
    assert "additional-material.sqlite3" in names
    for item in manifest["items"]:
        backup = manifest_path.parent / item["backup_path"]
        assert backup.stat().st_size == item["backup_size"]
        if item["kind"] == "sqlite":
            assert item["sqlite_integrity_check"] == "ok"
            assert item["restore_integrity_check"] == "ok"
        else:
            assert item["sqlite_integrity_check"] is None
            assert item["restore_integrity_check"] == "sha256-match"
        if os.name != "nt":
            assert backup.stat().st_mode & 0o077 == 0
    if os.name != "nt":
        assert manifest_path.stat().st_mode & 0o077 == 0

    assert main(["verify-backup", "--manifest", str(manifest_path)]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["manifest_sha256"] == manifest["manifest_sha256"]


def test_production_backup_fails_closed_on_missing_required_database(
    tmp_path, monkeypatch
):
    _production_inputs(tmp_path)
    (tmp_path / "data" / "talent-pool.sqlite").unlink()
    monkeypatch.chdir(tmp_path)
    assert main(["backup", "--backup-dir", "backups", "--git-sha", GIT_SHA]) == 1
    assert not list((tmp_path / "backups").glob("production-predeploy-*"))


def test_missing_inputs_need_explicit_nonproduction_override(tmp_path, monkeypatch):
    _sqlite(tmp_path / "data" / "only.sqlite")
    manifest = tmp_path / "config" / "fixed-sources.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "backup",
                "--backup-dir",
                "backups",
                "--git-sha",
                GIT_SHA,
                "--nonproduction-allow-missing",
            ]
        )
        == 0
    )
    manifest_path = next((tmp_path / "backups").glob("*/manifest.json"))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["allow_missing_nonproduction"] is True


def test_backup_verifier_rejects_tampering(tmp_path, monkeypatch, capsys):
    _production_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["backup", "--backup-dir", "backups", "--git-sha", GIT_SHA]) == 0
    summary = json.loads(capsys.readouterr().out)
    manifest_path = Path(summary["backup_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = manifest_path.parent / manifest["items"][0]["backup_path"]
    with target.open("ab") as stream:
        stream.write(b"tamper")
    assert main(["verify-backup", "--manifest", str(manifest_path)]) == 1


def test_backup_verifier_rejects_mismatched_source_manifest_metadata(
    tmp_path, monkeypatch, capsys
):
    _production_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["backup", "--backup-dir", "backups", "--git-sha", GIT_SHA]) == 0
    summary = json.loads(capsys.readouterr().out)
    manifest_path = Path(summary["backup_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    item = next(entry for entry in manifest["items"] if entry["kind"] == "source_manifest")
    item["source_size"] += 1
    from ht_lead_radar.cli_v2 import _canonical_digest

    manifest["manifest_sha256"] = _canonical_digest(manifest)
    manifest_path.write_text(
        json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["verify-backup", "--manifest", str(manifest_path)]) == 1


def test_backup_safely_resolves_head_when_git_sha_is_omitted(
    tmp_path, monkeypatch, capsys
):
    database = tmp_path / "data" / "only.sqlite"
    _sqlite(database)
    monkeypatch.chdir(PROJECT_ROOT)
    assert (
        main(
            [
                "backup",
                "--backup-dir",
                str(tmp_path / "backups"),
                "--databases",
                str(database),
                "--discover-data-dir",
                str(database.parent),
                "--nonproduction-allow-missing",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    manifest = json.loads(
        Path(summary["backup_manifest"]).read_text(encoding="utf-8")
    )
    expected = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert manifest["git_sha"] == expected


def test_backup_without_explicit_sha_fails_closed_outside_git_checkout(
    tmp_path, monkeypatch
):
    database = tmp_path / "data" / "only.sqlite"
    _sqlite(database)
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "backup",
                "--backup-dir",
                "backups",
                "--databases",
                str(database),
                "--discover-data-dir",
                str(database.parent),
                "--nonproduction-allow-missing",
            ]
        )
        == 1
    )
    assert not (tmp_path / "backups").exists()
