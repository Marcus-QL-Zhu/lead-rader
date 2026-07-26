import json

from ht_lead_radar.feishu_notify import FeishuRecipient, main
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
from test_talent_pool import sample_report


class FakeClient:
    sent: list[tuple[str, str, FeishuRecipient, str]] = []

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    def send_text(
        self,
        recipient: FeishuRecipient,
        text: str,
        *,
        idempotency_key: str,
    ) -> str:
        assert len(idempotency_key) == 64
        self.sent.append((self.app_id, self.app_secret, recipient, text))
        return "om-message"


def test_main_merges_recipient_fallback_and_suppresses_duplicate(tmp_path):
    FakeClient.sent.clear()
    primary_env = tmp_path / "lead.env"
    fallback_env = tmp_path / "josint.env"
    primary_env.write_text(
        "FEISHU_APP_ID=app-id\nFEISHU_APP_SECRET=app-secret\n",
        encoding="utf-8",
    )
    fallback_env.write_text(
        "FEISHU_NOTIFY_OPEN_ID=ou-open\n",
        encoding="utf-8",
    )
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "daily.json").write_text(
        json.dumps(
            {
                "manifest": {
                    "run_id": "run-1",
                    "as_of": "2026-07-26",
                    "direction": "具身智能",
                },
                "leads": [{"company": "示例机器人", "score": 80}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    args = [
        "--direction",
        "具身智能",
        "--task-exit-code",
        "0",
        "--run-date",
        "2026-07-26",
        "--report-dir",
        str(report_dir),
        "--state-db",
        str(tmp_path / "notifications.sqlite"),
        "--env-file",
        str(primary_env),
        "--fallback-env-file",
        str(fallback_env),
    ]

    assert main(args, client_class=FakeClient) == 0
    assert main(args, client_class=FakeClient) == 0
    assert len(FakeClient.sent) == 1
    app_id, app_secret, recipient, text = FakeClient.sent[0]
    assert (app_id, app_secret) == ("app-id", "app-secret")
    assert recipient == FeishuRecipient("ou-open", "open_id")
    assert "示例机器人" in text

def test_main_does_not_mix_stale_same_day_talent_bundle(tmp_path):
    FakeClient.sent.clear()
    env_file = tmp_path / "lead.env"
    env_file.write_text(
        "FEISHU_APP_ID=app-id\n"
        "FEISHU_APP_SECRET=app-secret\n"
        "FEISHU_NOTIFY_OPEN_ID=ou-open\n",
        encoding="utf-8",
    )
    old_report = sample_report(leads=1)
    old_bundle = generate_draft_bundle(old_report, target_count=3)
    talent_db = tmp_path / "talent.sqlite"
    TalentPoolStore(talent_db).save_bundle(old_bundle.to_dict())
    talent_dir = tmp_path / "talent"
    talent_dir.mkdir()
    (talent_dir / "old.json").write_text(
        json.dumps(old_bundle.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    current_report = sample_report(leads=1)
    current_report["manifest"]["run_id"] = "run-20260726-rerun"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "current.json").write_text(
        json.dumps(current_report, ensure_ascii=False),
        encoding="utf-8",
    )
    args = [
        "--direction",
        current_report["manifest"]["direction"],
        "--task-exit-code",
        "0",
        "--run-date",
        current_report["manifest"]["as_of"],
        "--report-dir",
        str(report_dir),
        "--state-db",
        str(tmp_path / "notifications.sqlite"),
        "--env-file",
        str(env_file),
        "--talent-state-db",
        str(talent_db),
        "--talent-output-dir",
        str(talent_dir),
        "--talent-draft-exit-code",
        "71",
    ]

    assert main(args, client_class=FakeClient) == 0
    text = FakeClient.sent[0][3]
    assert "退出码 71" in text
    assert "今日建议发布的人才蓄水职位" not in text
    assert old_bundle.drafts[0].draft_id not in text
