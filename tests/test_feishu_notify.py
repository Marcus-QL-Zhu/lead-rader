import json
import time
import urllib.error

import pytest

import ht_lead_radar.feishu_notify as feishu_notify
from ht_lead_radar.feishu_notify import (
    FeishuMessageClient,
    FeishuRecipient,
    NotificationState,
    FEISHU_TEXT_MAX_BYTES,
    build_summary,
    build_summary_parts,
    find_report,
    main,
    notification_key,
    resolve_recipient,
)
from ht_lead_radar.talent_pool_store import TalentPoolStore


def _report() -> dict:
    return {
        "manifest": {
            "run_id": "run-1",
            "as_of": "2026-07-26",
            "direction": "具身智能",
            "source_summary": {"failures": [{"source": "example"}]},
        },
        "leads": [
            {
                "company": "示例机器人",
                "score": 81,
                "confidence_grade": "B",
                "target_roles": ["研发总监", "数据平台主管"],
            }
        ],
    }


def test_resolve_recipient_supports_generic_chat_and_open_ids():
    assert resolve_recipient(
        {
            "FEISHU_NOTIFY_RECEIVE_ID": "ou-generic",
            "FEISHU_NOTIFY_RECEIVE_ID_TYPE": "union_id",
            "FEISHU_NOTIFY_CHAT_ID": "oc-chat",
        }
    ) == FeishuRecipient("ou-generic", "union_id")
    assert resolve_recipient(
        {"FEISHU_NOTIFY_CHAT_ID": "oc-chat"}
    ) == FeishuRecipient("oc-chat", "chat_id")
    assert resolve_recipient(
        {"FEISHU_NOTIFY_OPEN_ID": "ou-open"}
    ) == FeishuRecipient("ou-open", "open_id")


def test_message_client_limits_feishu_uuid_to_50_characters(monkeypatch):
    calls = []
    client = FeishuMessageClient("app-id", "app-secret")

    def fake_post(url, payload, token=""):
        calls.append((url, payload, token))
        if "tenant_access_token" in url:
            return {"tenant_access_token": "tenant-token"}
        return {"data": {"message_id": "om-message"}}

    monkeypatch.setattr(client, "_post_json", fake_post)
    message_id = client.send_text(
        FeishuRecipient("ou-open", "open_id"),
        "daily summary",
        idempotency_key="a" * 64,
    )

    assert message_id == "om-message"
    message_url, payload, token = calls[1]
    assert message_url.endswith("messages?receive_id_type=open_id")
    assert token == "tenant-token"
    assert payload["receive_id"] == "ou-open"
    assert json.loads(payload["content"]) == {"text": "daily summary"}
    assert payload["uuid"] == "a" * 50


class _DeadlineResponse:
    def __init__(self, pieces, *, delay=0.0):
        self._pieces = iter(pieces)
        self._delay = delay
        self.closed = False
        self._sock = self

    def settimeout(self, _seconds):
        pass

    def read(self, _size=-1):
        if self._delay:
            time.sleep(self._delay)
        return next(self._pieces, b"")

    def close(self):
        self.closed = True


def test_feishu_connect_has_real_wallclock_boundary(monkeypatch):
    monkeypatch.setattr(feishu_notify, "FEISHU_HTTP_WALLCLOCK_SECONDS", 0.05)

    def stuck_open(*_args, **_kwargs):
        time.sleep(1)

    monkeypatch.setattr(feishu_notify.urllib.request, "urlopen", stuck_open)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="connect exceeded"):
        FeishuMessageClient("app-id", "app-secret")._post_json(
            "https://open.feishu.cn/test", {"test": True}
        )
    assert time.monotonic() - started < 0.2


def test_feishu_drip_body_cannot_extend_request_deadline(monkeypatch):
    monkeypatch.setattr(feishu_notify, "FEISHU_HTTP_WALLCLOCK_SECONDS", 0.06)
    response = _DeadlineResponse([b"{", b'"code"', b":", b"0", b"}"], delay=0.02)
    monkeypatch.setattr(
        feishu_notify.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: response,
    )
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="read exceeded"):
        FeishuMessageClient("app-id", "app-secret")._post_json(
            "https://open.feishu.cn/test", {"test": True}
        )
    assert time.monotonic() - started < 0.2


def test_feishu_http_error_body_shares_connect_deadline(monkeypatch):
    monkeypatch.setattr(feishu_notify, "FEISHU_HTTP_WALLCLOCK_SECONDS", 0.07)
    error_body = _DeadlineResponse(
        [b'{"code":', b"999", b',"msg":"slow"}'], delay=0.03
    )
    error = urllib.error.HTTPError(
        "https://open.feishu.cn/test", 500, "server error", {}, error_body
    )

    def slow_http_error(*_args, **_kwargs):
        time.sleep(0.04)
        raise error

    monkeypatch.setattr(feishu_notify.urllib.request, "urlopen", slow_http_error)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="read exceeded"):
        FeishuMessageClient("app-id", "app-secret")._post_json(
            "https://open.feishu.cn/test", {"test": True}
        )
    assert time.monotonic() - started < 0.2


def test_build_success_summary_contains_rank_score_roles_and_source_health(tmp_path):
    path = tmp_path / "report.json"
    text = build_summary(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=0,
        report_path=path,
        report=_report(),
    )

    assert "✅ 已完成" in text
    assert "入选公司：1 家" in text
    assert "信源异常：1 个" in text
    assert "1. 示例机器人｜81分｜置信度 B｜研发总监、数据平台主管" in text
    assert str(path) in text


def test_build_failure_summary_does_not_present_stale_leads():
    text = build_summary(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=1,
        report_path=None,
        report=None,
    )

    assert "❌ 任务失败" in text
    assert "报告：未生成" in text


def test_find_report_and_notification_state_are_idempotent(tmp_path):
    (tmp_path / "feishu-change-set.json").write_text(
        json.dumps([{"operation": "create"}]),
        encoding="utf-8",
    )
    report_path = tmp_path / "lead-radar.json"
    report_path.write_text(
        json.dumps(_report(), ensure_ascii=False),
        encoding="utf-8",
    )
    path, report = find_report(
        tmp_path,
        run_date="2026-07-26",
        direction="具身智能",
    )
    assert path == report_path
    assert report is not None

    key = notification_key(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=0,
        report=report,
    )
    state = NotificationState(tmp_path / "notifications.sqlite")
    assert not state.was_sent(key)
    state.record(key, "om-message")
    assert state.was_sent(key)


def test_notification_key_changes_for_failure_or_new_run():
    first = notification_key(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=0,
        report=_report(),
    )
    failed = notification_key(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=1,
        report=None,
    )
    changed = _report()
    changed["manifest"]["run_id"] = "run-2"
    second = notification_key(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=0,
        report=changed,
    )
    assert len({first, failed, second}) == 3


def test_large_liepin_json_is_split_without_exceeding_feishu_limit():
    draft = {
        "draft_id": "tp-large-1",
        "recommended_title": "具身智能商业化总监",
        "talent_persona": "商业化负责人",
        "attraction_angle": "新业务",
        "why_now": "融资后扩张",
        "source_leads": [
            {"company": "示例机器人", "role_hypotheses": ["商业化总监"]}
        ],
        "public_payload": {
            "position_name": "具身智能商业化总监",
            "position_scope": "具体职责" * 1_200,
            "cities": ["上海"],
        },
    }
    second = json.loads(json.dumps(draft, ensure_ascii=False))
    second["draft_id"] = "tp-large-2"
    parts = build_summary_parts(
        run_date="2026-07-26",
        direction="具身智能",
        task_exit_code=0,
        report_path=None,
        report=_report(),
        talent_drafts=[draft, second],
    )

    assert len(parts) == 3
    assert all(len(part.encode("utf-8")) <= FEISHU_TEXT_MAX_BYTES for part in parts)
    assert all('"position_name":"具身智能商业化总监"' in part for part in parts[1:])
    assert "示例机器人 → 商业化总监" in "".join(parts)


def test_single_oversized_liepin_json_fails_instead_of_sending_broken_json():
    draft = {
        "draft_id": "tp-too-large",
        "recommended_title": "商业化总监",
        "source_leads": [{"company": "示例机器人"}],
        "public_payload": {"position_scope": "职责" * 20_000},
    }
    import pytest

    with pytest.raises(ValueError, match="exceeds Feishu text limit"):
        build_summary_parts(
            run_date="2026-07-26",
            direction="具身智能",
            task_exit_code=0,
            report_path=None,
            report=_report(),
            talent_drafts=[draft],
        )


def test_exit_71_fallback_reads_zero_draft_snapshot_and_records_delivery(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report = _report()
    (report_dir / "lead-report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    talent_store = TalentPoolStore(tmp_path / "talent.sqlite")
    talent_store.save_bundle(
        {
            "run_date": "2026-07-26",
            "direction": report["manifest"]["direction"],
            "source_run_id": "run-1",
            "generation_provider": "direct-llm",
            "generation_model": "MiniMax-M3",
            "generation_error": "ProviderError: draft generation failed",
            "drafts": [],
            "final_report_opportunities": [
                {
                    "company": "Example Robotics",
                    "score": 81,
                    "role_hypotheses": ["R&D Director"],
                    "evidence_urls": ["https://example.com/evidence"],
                }
            ],
            "completion_status": {
                "analysis_status": "completed",
                "draft_generation_status": "failed",
                "notification_status": "pending",
                "source_health_status": "warning",
            },
        }
    )
    env_file = tmp_path / "lead-radar.env"
    env_file.write_text(
        "FEISHU_APP_ID=test-app\n"
        "FEISHU_APP_SECRET=test-secret\n"
        "FEISHU_NOTIFY_OPEN_ID=test-open-id\n",
        encoding="utf-8",
    )
    messages = []

    class FakeClient:
        def __init__(self, app_id, app_secret):
            assert app_id == "test-app"
            assert app_secret == "test-secret"

        def send_text(self, recipient, text, *, idempotency_key):
            messages.append((recipient, text, idempotency_key))
            return f"message-{len(messages)}"

    exit_code = main(
        [
            "--direction",
            report["manifest"]["direction"],
            "--run-date",
            "2026-07-26",
            "--task-exit-code",
            "0",
            "--report-dir",
            str(report_dir),
            "--state-db",
            str(tmp_path / "notifications.sqlite"),
            "--env-file",
            str(env_file),
            "--talent-state-db",
            str(talent_store.database),
            "--talent-draft-exit-code",
            "71",
        ],
        client_class=FakeClient,
    )

    assert exit_code == 0
    assert messages
    bundle = talent_store.current_bundle(
        "2026-07-26", report["manifest"]["direction"]
    )
    deliveries = talent_store.delivery_records(bundle["_snapshot_id"])
    assert deliveries == [
        {
            "delivery_channel": "feishu_fallback",
            "status": "delivered",
            "delivered_at": deliveries[0]["delivered_at"],
            "error_class": "",
            "error_detail": "",
        }
    ]


def test_analysis_timeout_fallback_records_reportless_completion_delivery(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    talent_store = TalentPoolStore(tmp_path / "talent.sqlite")
    talent_store.save_bundle(
        {
            "run_date": "2026-07-26",
            "direction": "硬科技组合",
            "source_run_id": "analysis-failure:2026-07-26:硬科技组合",
            "generation_provider": "",
            "generation_model": "",
            "generation_error": "",
            "drafts": [],
            "final_report_opportunities": [],
            "completion_status": {
                "analysis_status": "failed",
                "draft_generation_status": "not_run",
                "notification_status": "pending",
                "source_health_status": "critical",
            },
        }
    )
    env_file = tmp_path / "lead-radar.env"
    env_file.write_text(
        "FEISHU_APP_ID=test-app\n"
        "FEISHU_APP_SECRET=test-secret\n"
        "FEISHU_NOTIFY_OPEN_ID=test-open-id\n",
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, _app_id, _app_secret):
            pass

        @staticmethod
        def send_text(_recipient, _text, *, idempotency_key):
            assert idempotency_key
            return "message-analysis-timeout"

    exit_code = main(
        [
            "--direction",
            "硬科技组合",
            "--run-date",
            "2026-07-26",
            "--task-exit-code",
            "124",
            "--report-dir",
            str(report_dir),
            "--state-db",
            str(tmp_path / "notifications.sqlite"),
            "--env-file",
            str(env_file),
            "--talent-state-db",
            str(talent_store.database),
            "--talent-draft-exit-code",
            "0",
            "--talent-completion-ready",
            "1",
        ],
        client_class=FakeClient,
    )

    bundle = talent_store.current_bundle("2026-07-26", "硬科技组合")
    deliveries = talent_store.delivery_records(bundle["_snapshot_id"])
    assert exit_code == 0
    assert deliveries[0]["delivery_channel"] == "feishu_fallback"
    assert deliveries[0]["status"] == "delivered"


def test_failed_fallback_records_bounded_delivery_failure(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report = _report()
    (report_dir / "lead-report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    talent_store = TalentPoolStore(tmp_path / "talent.sqlite")
    talent_store.save_bundle(
        {
            "run_date": "2026-07-26",
            "direction": report["manifest"]["direction"],
            "source_run_id": "run-1",
            "generation_provider": "direct-llm",
            "generation_model": "MiniMax-M3",
            "generation_error": "",
            "drafts": [],
            "final_report_opportunities": [],
        }
    )
    env_file = tmp_path / "lead-radar.env"
    env_file.write_text(
        "FEISHU_APP_ID=test-app\n"
        "FEISHU_APP_SECRET=test-secret\n"
        "FEISHU_NOTIFY_OPEN_ID=test-open-id\n",
        encoding="utf-8",
    )

    class FailingClient:
        def __init__(self, _app_id, _app_secret):
            pass

        def send_text(self, _recipient, _text, *, idempotency_key):
            raise RuntimeError(
                f"Feishu unavailable; token=secret-value; key={idempotency_key}"
            )

    exit_code = main(
        [
            "--direction",
            report["manifest"]["direction"],
            "--run-date",
            "2026-07-26",
            "--task-exit-code",
            "0",
            "--report-dir",
            str(report_dir),
            "--state-db",
            str(tmp_path / "notifications.sqlite"),
            "--env-file",
            str(env_file),
            "--talent-state-db",
            str(talent_store.database),
            "--talent-draft-exit-code",
            "71",
        ],
        client_class=FailingClient,
    )

    bundle = talent_store.current_bundle(
        "2026-07-26", report["manifest"]["direction"]
    )
    deliveries = talent_store.delivery_records(bundle["_snapshot_id"])
    assert exit_code == 70
    assert deliveries[0]["delivery_channel"] == "feishu_fallback"
    assert deliveries[0]["status"] == "failed"
    assert deliveries[0]["error_class"] == "RuntimeError"
    assert "secret-value" not in deliveries[0]["error_detail"]


def test_watchdog_failure_record_preserves_openclaw_hook_failure(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report = _report()
    (report_dir / "lead-report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    talent_store = TalentPoolStore(tmp_path / "talent.sqlite")
    talent_store.save_bundle(
        {
            "run_date": "2026-07-26",
            "direction": report["manifest"]["direction"],
            "source_run_id": "run-1",
            "generation_provider": "direct-llm",
            "generation_model": "MiniMax-M3",
            "generation_error": "",
            "drafts": [],
            "final_report_opportunities": [],
        }
    )
    bundle = talent_store.current_bundle("2026-07-26", "具身智能")
    talent_store.record_delivery(
        bundle["_snapshot_id"],
        channel="openclaw_hook",
        status="failed",
        error="OpenClawHookTimeout",
    )

    exit_code = main(
        [
            "--direction",
            "具身智能",
            "--run-date",
            "2026-07-26",
            "--task-exit-code",
            "0",
            "--report-dir",
            str(report_dir),
            "--state-db",
            str(tmp_path / "notifications.sqlite"),
            "--talent-state-db",
            str(talent_store.database),
            "--record-fallback-failure",
            "FeishuFallbackWallClockTimeout",
        ]
    )

    assert exit_code == 0
    deliveries = talent_store.delivery_records(bundle["_snapshot_id"])
    assert {(row["delivery_channel"], row["status"]) for row in deliveries} == {
        ("openclaw_hook", "failed"),
        ("feishu_fallback", "failed"),
    }
    fallback = next(
        row for row in deliveries if row["delivery_channel"] == "feishu_fallback"
    )
    assert fallback["error_class"] == "FeishuFallbackWallClockTimeout"


def test_direct_fallback_is_noop_after_any_confirmed_delivery(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    report = _report()
    (report_dir / "lead-report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    talent_store = TalentPoolStore(tmp_path / "talent.sqlite")
    talent_store.save_bundle(
        {
            "run_date": "2026-07-26",
            "direction": report["manifest"]["direction"],
            "source_run_id": "run-1",
            "generation_provider": "direct-llm",
            "generation_model": "MiniMax-M3",
            "generation_error": "",
            "drafts": [],
            "final_report_opportunities": [],
        }
    )
    bundle = talent_store.current_bundle(
        "2026-07-26", report["manifest"]["direction"]
    )
    assert bundle is not None
    talent_store.record_delivery(
        bundle["_snapshot_id"], channel="openclaw_hook", status="delivered"
    )
    env_file = tmp_path / "lead-radar.env"
    env_file.write_text("", encoding="utf-8")

    class MustNotConstruct:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("fallback client must not be constructed")

    exit_code = main(
        [
            "--direction",
            report["manifest"]["direction"],
            "--run-date",
            "2026-07-26",
            "--task-exit-code",
            "0",
            "--report-dir",
            str(report_dir),
            "--state-db",
            str(tmp_path / "notifications.sqlite"),
            "--env-file",
            str(env_file),
            "--talent-state-db",
            str(talent_store.database),
            "--talent-draft-exit-code",
            "0",
        ],
        client_class=MustNotConstruct,
    )

    assert exit_code == 0
    assert talent_store.delivery_records(bundle["_snapshot_id"]) == [
        {
            "delivery_channel": "openclaw_hook",
            "status": "delivered",
            "delivered_at": talent_store.delivery_records(bundle["_snapshot_id"])[0][
                "delivered_at"
            ],
            "error_class": "",
            "error_detail": "",
        }
    ]
