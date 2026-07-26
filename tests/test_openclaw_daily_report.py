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


def test_render_context_separates_omitted_theme_from_displayed_draft_one(tmp_path):
    store, _ = _store(tmp_path)
    row = store.pending_openclaw_report()
    assert row is not None
    row["bundle"] = dict(row["bundle"])
    row["bundle"]["talent_themes"] = [
        {
            "theme_id": "theme_2ddbfd722857",
            "recommended_title": "具身智能行业应用与商业化总监",
            "source_lead_indices": [1],
        }
    ]
    row["bundle"]["company_demand_analysis"] = [
        {
            "lead_index": 1,
            "company": "戴盟机器人",
            "hypotheses": [{"specific_title": "具身智能行业应用与商业化总监"}],
        }
    ]
    row["bundle"]["generation_error"] = (
        "theme theme_2ddbfd722857: DirectTalentGenerationError: "
        "failed after one repair: draft 1 title is too broad or not Director+; "
        "draft 1 talent_persona is empty; draft 1 public_payload must be an object; "
        "recommended_title must equal the talent theme title"
    )

    rendered = bridge.render_context(row)

    assert rendered["generation_status"] == "partial"
    assert rendered["valid_draft_count"] == rendered["draft_count"]
    assert rendered["omitted_failure_count"] == 1
    failure = rendered["omitted_generation_failures"][0]
    assert failure["scope"] == "omitted_talent_theme"
    assert failure["companies"] == ["戴盟机器人"]
    assert failure["recommended_title"] == "具身智能行业应用与商业化总监"
    assert failure["displayed_draft_index"] is None
    assert failure["affects_displayed_drafts"] is False
    assert failure["reason"] == (
        "LLM output failed deterministic validation after one repair"
    )
    assert rendered["drafts"][0]["validation_status"] == "valid"
    assert rendered["drafts"][0]["validation_issues"] == []


def test_unclassified_generation_error_is_never_assigned_to_displayed_index(tmp_path):
    store, _ = _store(tmp_path)
    row = store.pending_openclaw_report()
    assert row is not None
    row["bundle"] = dict(row["bundle"])
    row["bundle"]["generation_error"] = "partial generation"

    failure = bridge.render_context(row)["omitted_generation_failures"][0]

    assert failure["scope"] == "omitted_generation_item"
    assert failure["displayed_draft_index"] is None
    assert failure["affects_displayed_drafts"] is False


def test_operator_can_requeue_only_exact_current_completed_report(tmp_path):
    store, _ = _store(tmp_path)
    current = store.pending_openclaw_report(claim=True)
    assert current is not None
    assert store.mark_openclaw_reported(current["snapshot_id"])

    assert store.requeue_openclaw_report(current["snapshot_id"])
    pending = store.pending_openclaw_report()
    assert pending is not None
    assert pending["snapshot_id"] == current["snapshot_id"]
    assert not store.requeue_openclaw_report(current["snapshot_id"])
    assert not store.requeue_openclaw_report("missing-snapshot")


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
        include_agent_response=True,
    )
    assert result["status"] == "reported"
    assert result["agent_response"] == "ok"
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


def test_payload_validation_is_deterministic_and_handles_non_object(tmp_path):
    store, _ = _store(tmp_path)
    row = store.pending_openclaw_report()
    assert row is not None
    draft = dict(row["bundle"]["drafts"][0])

    draft["payload_hash"] = "tampered"
    summary = bridge._draft_summary(draft, 1)
    assert summary["validation_status"] == "invalid"
    assert "payload_hash does not match" in " ".join(summary["validation_issues"])

    draft["public_payload"] = ["not", "an", "object"]
    summary = bridge._draft_summary(draft, 1)
    assert summary["validation_status"] == "invalid"
    assert "public_payload must be an object" in summary["validation_issues"]


def test_duplicate_omission_is_attributed_to_its_theme(tmp_path):
    store, _ = _store(tmp_path)
    row = store.pending_openclaw_report()
    assert row is not None
    bundle = dict(row["bundle"])
    theme = {
        "theme_id": "theme_abcdef123456",
        "recommended_title": "机器人量产工程化总监",
        "source_lead_indices": [1],
    }
    bundle["talent_themes"] = [theme]
    bundle["company_demand_analysis"] = [
        {"lead_index": 1, "company": "测试机器人公司", "hypotheses": []}
    ]
    duplicate_id = bridge._theme_draft_id(
        theme,
        run_date=bundle["run_date"],
        direction=bundle["direction"],
    )
    bundle["generation_error"] = f"draft {duplicate_id}: duplicate generated payload"

    failure = bridge._generation_failures(bundle)[0]
    assert failure["scope"] == "omitted_duplicate_draft"
    assert failure["companies"] == ["测试机器人公司"]
    assert failure["recommended_title"] == "机器人量产工程化总监"
    assert failure["reason"] == "duplicate generated payload was omitted"
    assert failure["displayed_draft_index"] is None


def test_requeue_rejects_active_wrong_session_and_stale_snapshots(tmp_path):
    store, first_bundle = _store(tmp_path)
    first = store.pending_openclaw_report(claim=True)
    assert first is not None
    assert not store.requeue_openclaw_report(first["snapshot_id"])
    assert store.mark_openclaw_read(first["snapshot_id"])
    assert not store.requeue_openclaw_report(first["snapshot_id"])
    assert store.mark_openclaw_reported(first["snapshot_id"])
    assert not store.requeue_openclaw_report(
        first["snapshot_id"], session_key="agent:other:main"
    )

    next_report = sample_report()
    next_report["manifest"]["as_of"] = "2026-07-28"
    next_report["manifest"]["run_id"] = "run-20260728"
    next_bundle = generate_draft_bundle(next_report)
    store.save_bundle(next_bundle.to_dict())
    assert not store.requeue_openclaw_report(first["snapshot_id"])

    current = store.pending_openclaw_report(claim=True)
    assert current is not None
    assert store.mark_openclaw_report_failed(current["snapshot_id"], "retry")
    assert store.requeue_openclaw_report(current["snapshot_id"])


def test_event_prompt_never_requests_approval_for_blocked_payloads():
    prompt = bridge.event_text("a" * 64, source="test")
    assert "If approval_blocked is true" in prompt
    assert "do not ask for approval" in prompt
    assert "Only when approval_blocked is false" in prompt
