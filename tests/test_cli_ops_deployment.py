import json
import sqlite3
from pathlib import Path

from ht_lead_radar.cli_v2 import main


def test_backup_with_relative_directory_does_not_duplicate_path(
    tmp_path, monkeypatch, capsys
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "runtime.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE runs (id INTEGER)")
        connection.execute("INSERT INTO runs VALUES (1)")
    monkeypatch.chdir(tmp_path)

    code = main([
        "backup",
        "--backup-dir", "backups",
        "--databases", "data/runtime.sqlite",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["backups"]) == 1
    target = Path(payload["backups"][0]["target"])
    assert target.parent == (tmp_path / "backups").resolve()
    assert target.exists()
    assert not (tmp_path / "backups" / "backups").exists()


def test_source_health_closes_collector(monkeypatch, tmp_path, capsys):
    from ht_lead_radar import source_pack_collector

    state = {"closed": False}

    class FakeCollector:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            state["closed"] = True

        def source_health_summary(self):
            return {"source_count": 0}

    monkeypatch.setattr(
        source_pack_collector,
        "SourcePackCollector",
        FakeCollector,
    )
    registry = (
        Path(__file__).resolve().parents[1] / "config" / "source-packs.json"
    )

    code = main([
        "source-health",
        "--source-packs", str(registry),
        "--source-state-db", str(tmp_path / "sources.sqlite"),
    ])

    assert code == 0
    assert state["closed"] is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["collector_health"]["source_count"] == 0
