import json

from ht_lead_radar.feishu_notify import FeishuRecipient, main


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
