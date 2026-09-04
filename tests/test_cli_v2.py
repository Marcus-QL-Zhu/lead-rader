import json

from ht_lead_radar.cli_v2 import main
from ht_lead_radar.runtime import RunStore


def _paths(tmp_path):
    return [
        "--runtime-db", str(tmp_path / "runtime.sqlite"),
        "--fact-db", str(tmp_path / "facts.sqlite"),
        "--relationship-db", str(tmp_path / "relationships.sqlite"),
        "--budget-db", str(tmp_path / "budget.sqlite"),
        "--source-state-db", str(tmp_path / "sources.sqlite"),
        "--feishu-state-db", str(tmp_path / "feishu.sqlite"),
        "--audit-db", str(tmp_path / "audit.sqlite"),
        "--output-dir", str(tmp_path / "reports"),
        "--metaso-verify-limit", "0",
    ]


def test_v2_legacy_run_and_natural_language_ask(tmp_path):
    run_id_file = tmp_path / "active-run-id"
    code = main([
        "run", "--direction", "灵巧手", "--demo",
        "--run-id-file", str(run_id_file), *_paths(tmp_path)
    ])
    assert code == 0
    reports = list((tmp_path / "reports").glob("*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["manifest"]["policy"]["director_plus_only"]
    run_id = run_id_file.read_text(encoding="ascii").strip()
    assert run_id.startswith("run_")
    assert "run_id_file" not in RunStore(tmp_path / "runtime.sqlite").get_run(
        run_id
    ).input


def test_ask_refuses_float_to_enforce_ephemeral_entry_point(tmp_path):
    code = main([
        "ask",
        "--question",
        "我有一个数据采集总监候选人，哪些公司可能会要他？",
        "--demo",
        *_paths(tmp_path),
    ])
    assert code == 1


def test_finalize_interrupted_run_cli_accepts_an_exact_run_id(tmp_path):
    database = tmp_path / "runtime.sqlite"
    store = RunStore(database)
    run = store.ensure_run("interrupted-cli", {"direction": "test"})
    store.set_run_state(run.run_id, "running", current_stage="collect")

    code = main(
        [
            "finalize-interrupted-run",
            "--runtime-db",
            str(database),
            "--run-id",
            run.run_id,
            "--error-class",
            "PortfolioWallClockTimeout",
        ]
    )

    assert code == 0
    finalized = store.get_run(run.run_id)
    assert finalized.status == "failed"
    assert finalized.error == "PortfolioWallClockTimeout"


def test_python_module_entrypoint_uses_v2_cli():
    from ht_lead_radar import __main__ as module_entrypoint
    from ht_lead_radar import cli_v2

    assert module_entrypoint.main is cli_v2.main
