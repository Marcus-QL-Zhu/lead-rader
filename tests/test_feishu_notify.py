import json

from ht_lead_radar.feishu_notify import (
    FeishuMessageClient,
    FeishuRecipient,
    NotificationState,
    FEISHU_TEXT_MAX_BYTES,
    build_summary,
    build_summary_parts,
    find_report,
    notification_key,
    resolve_recipient,
)


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