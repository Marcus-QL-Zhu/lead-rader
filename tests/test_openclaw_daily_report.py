import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
from test_talent_pool import sample_report


SCRIPT = Path(__file__).parents[1] / "scripts" / "openclaw_daily_report.py"
spec = importlib.util.spec_from_file_location("openclaw_daily_report", SCRIPT)
assert spec and spec.loader
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def _store(tmp_path):
    database = tmp_path / "talent.sqlite"
    store = TalentPoolStore(database)
    bundle = generate_draft_bundle(sample_report())
    store.save_bundle(bundle.to_dict())
    return store, bundle


def test_report_receipt_is_pending_claimable_and_idempotent(tmp_path):
    store, bundle = _store(tmp_path)
    pending = store.pending_openclaw_report()
    assert pending is not None
    assert pending["run_date"] == bundle.run_date
    assert pending["ordered_draft_ids"] == [item.draft_id for item in bundle.drafts]
    claimed = store.pending_openclaw_report(claim=True)
    assert claimed is not None
    assert store.mark_openclaw_reported(claimed["snapshot_id"])
    assert store.pending_openclaw_report() is None
    store.save_bundle(bundle.to_dict())
    assert store.pending_openclaw_report() is None
    assert store.latest_openclaw_context()["snapshot_id"] == claimed["snapshot_id"]


def test_new_snapshot_invalidates_old_displayed_approval_context(tmp_path):
    store, bundle = _store(tmp_path)
    first = store.pending_openclaw_report()
    store.mark_openclaw_reported(first["snapshot_id"])
    changed = bundle.to_dict()
    changed["generation_error"] = "partial generation"
    store.save_bundle(changed)
    with pytest.raises(RuntimeError, match="no longer current"):
        store.apply_command(
            run_date=bundle.run_date,
            direction=bundle.direction,
            command="发布 1",
            actor="test-actor",
            expected_snapshot_id=first["snapshot_id"],
        )


def test_current_context_prefers_newer_pending_day_and_blocks_approval(tmp_path):
    store, first_bundle = _store(tmp_path)
    first = store.pending_openclaw_report()
    assert first is not None
    store.mark_openclaw_reported(first["snapshot_id"])

    next_report = sample_report()
    next_report["manifest"]["as_of"] = "2026-07-27"
    next_report["manifest"]["run_id"] = "run-20260727"
    next_bundle = generate_draft_bundle(next_report)
    store.save_bundle(next_bundle.to_dict())

    current = store.latest_openclaw_context()
    assert current is not None
    assert current["run_date"] == "2026-07-27"
    assert current["status"] == "pending"
    with pytest.raises(RuntimeError, match="not been shown completely"):
        store.apply_command(
            run_date=next_bundle.run_date,
            direction=next_bundle.direction,
            command="发布 1",
            actor="test-actor",
            expected_snapshot_id=current["snapshot_id"],
        )


def test_pending_selector_never_falls_back_to_older_backlog(tmp_path):
    store, _ = _store(tmp_path)
    next_report = sample_report()
    next_report["manifest"]["as_of"] = "2026-07-27"
    next_report["manifest"]["run_id"] = "run-20260727"
    store.save_bundle(generate_draft_bundle(next_report).to_dict())

    today = store.pending_openclaw_report(claim=True)
    assert today is not None
    assert today["run_date"] == "2026-07-27"
    assert store.pending_openclaw_report() is None

    store.mark_openclaw_reported(today["snapshot_id"])
    assert store.pending_openclaw_report() is None


def test_command_examples_never_reference_missing_drafts(tmp_path):
    store, _ = _store(tmp_path)
    row = store.pending_openclaw_report()
    assert row is not None

    one = dict(row)
    one["bundle"] = dict(row["bundle"])
    one["bundle"]["drafts"] = list(row["bundle"]["drafts"][:1])
    assert bridge.render_context(one)["commands"] == [
        "查看 1 的完整广告 JSON",
        "发布全部",
        "发布 1",
        "跳过全部",
    ]

    three = dict(row)
    three["bundle"] = dict(row["bundle"])
    three["bundle"]["drafts"] = list(row["bundle"]["drafts"][:3])
    assert "查看 2 的完整广告 JSON" in bridge.render_context(three)["commands"]
    assert "发布 1,2,3" in bridge.render_context(three)["commands"]

    empty = dict(row)
    empty["bundle"] = dict(row["bundle"])
    empty["bundle"]["drafts"] = []
    assert bridge.render_context(empty)["commands"] == []


def test_render_context_keeps_company_role_mapping_and_json_is_on_demand(tmp_path):
    store, _ = _store(tmp_path)
    row = store.pending_openclaw_report(claim=True)
    rendered = bridge.render_context(row)
    assert rendered["drafts"][0]["targets"][0]["company"]
    assert rendered["drafts"][0]["targets"][0]["company_roles"]
    assert "public_payload" not in rendered["drafts"][0]
    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--state-db",
            str(store.database),
            "show-draft",
            "--index",
            "1",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["draft"]["public_payload"]["cities"] == ["上海"]


def test_reset_guide_leaves_delivery_status_to_outer_bridge():
    guide = (bridge.ROOT / "references" / "openclaw-daily-operator.md").read_text(
        encoding="utf-8"
    )
    assert "show-snapshot --snapshot-id" in guide
    assert "不要执行 `mark-reported`" in guide
    assert "delivery 失败则标记 `failed`" in guide


def test_wake_runs_current_main_session_and_marks_reported(tmp_path):
    store, _ = _store(tmp_path)
    calls = []
    sessions = tmp_path / "sessions.json"
    sessions.write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "session-123",
                    "deliveryContext": {
                        "channel": "feishu",
                        "to": "user:test-open-id",
                        "accountId": "feishubot",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        second = bridge.wake(
            store,
            session_key="agent:main:main",
            source="concurrent-test",
            openclaw_bin="openclaw",
            sessions_file=sessions,
            runner=lambda *_args, **_kwargs: None,
        )
        assert second == {"status": "no_pending_report"}
        claimed = store.latest_openclaw_context()
        assert claimed is not None and claimed["status"] == "reporting"
        assert store.mark_openclaw_read(claimed["snapshot_id"])
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = bridge.wake(
        store,
        session_key="agent:main:main",
        source="test",
        openclaw_bin="openclaw",
        sessions_file=sessions,
        runner=fake_runner,
    )
    assert result["status"] == "reported"
    assert store.pending_openclaw_report() is None
    command = calls[0][0]
    assert command[:3] == ["openclaw", "agent", "--session-id"]
    assert command[3] == "session-123"
    assert "--deliver" in command
    assert command[command.index("--reply-channel") + 1] == "feishu"
    assert command[command.index("--reply-to") + 1] == "user:test-open-id"
    assert command[command.index("--reply-account") + 1] == "feishubot"
    event = command[command.index("--message") + 1]
    assert "LEAD_RADAR_DAILY_READY_V1" in event
    assert "public_payload" not in event
    assert "openclaw-daily-operator.md" in event
    assert str(bridge.ROOT / "SKILL.md") in event
    assert "/home/admin/.pyenv/versions/3.11.14/bin/python3" in event
    assert "--state-db" in event
    assert event.index("--state-db") < event.index("show-snapshot")
    assert "--snapshot-id" in event


def test_wake_fails_closed_without_feishu_main_route(tmp_path):
    store, _ = _store(tmp_path)
    sessions = tmp_path / "sessions.json"
    sessions.write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "session-123",
                    "deliveryContext": {"channel": "wecom", "to": "user:test"},
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Feishu delivery route"):
        bridge.wake(
            store,
            session_key="agent:main:main",
            source="test",
            openclaw_bin="openclaw",
            sessions_file=sessions,
        )
    assert store.latest_openclaw_context()["status"] == "failed"


def test_wake_does_not_mark_reported_when_agent_skips_pending_read(tmp_path):
    store, _ = _store(tmp_path)
    sessions = tmp_path / "sessions.json"
    sessions.write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "session-123",
                    "deliveryContext": {
                        "channel": "feishu",
                        "to": "user:test-open-id",
                        "accountId": "feishubot",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="ignored", stderr="")

    with pytest.raises(RuntimeError, match="without reading"):
        bridge.wake(
            store,
            session_key="agent:main:main",
            source="test",
            openclaw_bin="openclaw",
            sessions_file=sessions,
            runner=fake_runner,
        )
    assert store.latest_openclaw_context()["status"] == "failed"


def test_delivery_failure_after_exact_read_remains_retriable(tmp_path):
    store, _ = _store(tmp_path)
    sessions = tmp_path / "sessions.json"
    sessions.write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "session-123",
                    "deliveryContext": {
                        "channel": "feishu",
                        "to": "user:test-open-id",
                        "accountId": "feishubot",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    def failing_runner(command, **kwargs):
        claimed = store.latest_openclaw_context()
        assert claimed is not None and claimed["status"] == "reporting"
        assert store.mark_openclaw_read(claimed["snapshot_id"])
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="delivery failed"
        )

    with pytest.raises(RuntimeError, match="delivery failed"):
        bridge.wake(
            store,
            session_key="agent:main:main",
            source="test",
            openclaw_bin="openclaw",
            sessions_file=sessions,
            runner=failing_runner,
        )
    current = store.latest_openclaw_context()
    assert current is not None and current["status"] == "failed"
    assert store.pending_openclaw_report()["snapshot_id"] == current["snapshot_id"]
