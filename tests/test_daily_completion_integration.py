import importlib.util
import json
import subprocess
from pathlib import Path

from ht_lead_radar.application import (
    LeadRadarApplication,
    default_idempotency_key,
)
from ht_lead_radar.feishu_notify import main as feishu_main
from ht_lead_radar.requests import plan_opportunity_request
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore


ROOT = Path(__file__).parents[1]


def _load_script(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_script(
    "daily_completion_generator",
    "scripts/generate_talent_pool_drafts.py",
)
bridge = _load_script(
    "daily_completion_bridge",
    "scripts/openclaw_daily_report.py",
)


def test_runtime_generator_hook_and_fallback_are_same_day_idempotent(
    tmp_path,
    monkeypatch,
):
    direction = "灵巧手"
    plan = plan_opportunity_request("最近灵巧手有哪些公司可能招总监以上？")
    report_dir = tmp_path / "reports"
    payload = {
        "command": "ask",
        "direction": direction,
        "request_plan": plan.to_dict(),
        "demo": True,
        "runtime_db": str(tmp_path / "runtime.sqlite"),
        "fact_db": str(tmp_path / "facts.sqlite"),
        "relationship_db": str(tmp_path / "relationships.sqlite"),
        "budget_db": str(tmp_path / "budget.sqlite"),
        "source_state_db": str(tmp_path / "sources.sqlite"),
        "feishu_state_db": str(tmp_path / "feishu.sqlite"),
        "audit_db": str(tmp_path / "audit.sqlite"),
        "ops_metrics_db": str(tmp_path / "ops.sqlite"),
        "output_dir": str(report_dir),
        "metaso_verify_limit": 0,
        "skip_feishu_projection": True,
    }
    result = LeadRadarApplication(payload["runtime_db"]).run(
        payload,
        default_idempotency_key(payload, refresh=True),
    )
    report_path = Path(result.output["json_path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    run_date = report["manifest"]["as_of"]
    source_run_id = report["manifest"]["run_id"]
    talent_database = tmp_path / "talent.sqlite"
    generation_calls = []

    def offline_generator(value, *, target_count):
        generation_calls.append(value["manifest"]["run_id"])
        return generate_draft_bundle(value, target_count=target_count)

    monkeypatch.setattr(generator, "generate_direct_talent_bundle", offline_generator)
    generator_args = [
        "--direction",
        direction,
        "--run-date",
        run_date,
        "--report",
        str(report_path),
        "--state-db",
        str(talent_database),
        "--output-dir",
        str(tmp_path / "talent-output"),
        "--disable-cooldown",
    ]
    assert generator.main(generator_args) == 0
    assert generator.main(generator_args) == 0
    assert generation_calls == [source_run_id]

    store = TalentPoolStore(talent_database)
    current = store.current_bundle(
        run_date,
        direction,
        source_run_id=source_run_id,
    )
    assert current is not None
    snapshot_id = current["_snapshot_id"]
    sessions = tmp_path / "sessions.json"
    sessions.write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "session-integration",
                    "deliveryContext": {
                        "channel": "feishu",
                        "to": "user:test",
                        "accountId": "feishubot",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_openclaw(command, **_kwargs):
        claimed = store.latest_openclaw_context()
        assert claimed is not None and claimed["snapshot_id"] == snapshot_id
        assert claimed["status"] == "reporting"
        assert store.mark_openclaw_read(snapshot_id)
        return subprocess.CompletedProcess(command, 0, stdout="reported", stderr="")

    hook = bridge.wake(
        store,
        session_key="agent:main:main",
        source="integration-test",
        openclaw_bin="openclaw",
        sessions_file=sessions,
        runner=fake_openclaw,
    )
    assert hook["status"] == "reported"

    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")

    class MustNotConstruct:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("direct fallback ran after hook delivery")

    assert feishu_main(
        [
            "--direction",
            direction,
            "--run-date",
            run_date,
            "--task-exit-code",
            "0",
            "--report-dir",
            str(report_dir),
            "--state-db",
            str(tmp_path / "notification.sqlite"),
            "--env-file",
            str(env_file),
            "--talent-state-db",
            str(talent_database),
            "--talent-draft-exit-code",
            "0",
        ],
        client_class=MustNotConstruct,
    ) == 0
    deliveries = store.delivery_records(snapshot_id)
    assert [(item["delivery_channel"], item["status"]) for item in deliveries] == [
        ("openclaw_hook", "delivered")
    ]
