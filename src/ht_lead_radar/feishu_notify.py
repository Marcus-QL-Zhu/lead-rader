from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode


@dataclass(frozen=True)
class FeishuRecipient:
    receive_id: str
    receive_id_type: str


class FeishuMessageClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = ""

    def _post_json(self, url: str, payload: Mapping[str, Any], token: str = "") -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raw_body = error.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                body = {}
            code = body.get("code", error.code)
            message = body.get("msg") or error.reason
            violations = (body.get("error") or {}).get("field_violations") or []
            details = "; ".join(
                f"{item.get('field')}: {item.get('description')}"
                for item in violations
                if isinstance(item, Mapping)
            )
            suffix = f"; {details}" if details else ""
            raise RuntimeError(
                f"Feishu HTTP {error.code} error {code}: {message}{suffix}"
            ) from error
        if result.get("code", 0) != 0:
            raise RuntimeError(
                f"Feishu API error {result.get('code')}: {result.get('msg')}"
            )
        return result

    def token(self) -> str:
        if not self._token:
            result = self._post_json(
                "https://open.feishu.cn/open-apis/auth/v3/"
                "tenant_access_token/internal",
                {"app_id": self.app_id, "app_secret": self.app_secret},
            )
            self._token = str(result["tenant_access_token"])
        return self._token

    def send_text(
        self,
        recipient: FeishuRecipient,
        text: str,
        *,
        idempotency_key: str,
    ) -> str:
        if not idempotency_key:
            raise ValueError("idempotency_key must not be empty")
        request_uuid = idempotency_key[:50]
        query = urlencode({"receive_id_type": recipient.receive_id_type})
        result = self._post_json(
            f"https://open.feishu.cn/open-apis/im/v1/messages?{query}",
            {
                "receive_id": recipient.receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "uuid": request_uuid,
            },
            self.token(),
        )
        message_id = str((result.get("data") or {}).get("message_id") or "")
        if not message_id:
            raise RuntimeError("Feishu API returned success without message_id")
        return message_id


class NotificationState:
    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feishu_daily_notifications (
                    notification_key TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def was_sent(self, notification_key: str) -> bool:
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT 1 FROM feishu_daily_notifications "
                "WHERE notification_key=?",
                (notification_key,),
            ).fetchone()
        return row is not None

    def record(self, notification_key: str, message_id: str) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO feishu_daily_notifications"
                "(notification_key, message_id) VALUES (?, ?)",
                (notification_key, message_id),
            )


def load_env_files(*paths: str | Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_path in reversed(paths):
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    values.update(os.environ)
    return values


def resolve_recipient(env: Mapping[str, str]) -> FeishuRecipient:
    generic_id = str(env.get("FEISHU_NOTIFY_RECEIVE_ID") or "").strip()
    if generic_id:
        receive_type = str(
            env.get("FEISHU_NOTIFY_RECEIVE_ID_TYPE") or "open_id"
        ).strip()
        if receive_type not in {
            "open_id",
            "chat_id",
            "thread_id",
            "user_id",
            "union_id",
            "email",
        }:
            raise ValueError(f"unsupported Feishu receive_id_type: {receive_type}")
        return FeishuRecipient(generic_id, receive_type)

    chat_id = str(env.get("FEISHU_NOTIFY_CHAT_ID") or "").strip()
    if chat_id:
        return FeishuRecipient(chat_id, "chat_id")

    open_id = str(env.get("FEISHU_NOTIFY_OPEN_ID") or "").strip()
    if open_id:
        return FeishuRecipient(open_id, "open_id")
    raise ValueError(
        "missing FEISHU_NOTIFY_RECEIVE_ID, FEISHU_NOTIFY_CHAT_ID "
        "or FEISHU_NOTIFY_OPEN_ID"
    )


def find_report(
    report_dir: str | Path,
    *,
    run_date: str,
    direction: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for path in Path(report_dir).glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        manifest = payload.get("manifest") or {}
        if (
            str(manifest.get("as_of") or "") == run_date
            and str(manifest.get("direction") or "") == direction
        ):
            candidates.append((path.stat().st_mtime, path, payload))
    if not candidates:
        return None, None
    _, path, payload = max(candidates, key=lambda item: item[0])
    return path, payload


def notification_key(
    *,
    run_date: str,
    direction: str,
    task_exit_code: int,
    report: Mapping[str, Any] | None,
    talent_drafts: list[Mapping[str, Any]] | None = None,
    talent_generation_error: str = "",
) -> str:
    manifest = (report or {}).get("manifest") or {}
    value = {
        "run_date": run_date,
        "direction": direction,
        "task_exit_code": task_exit_code,
        "run_id": str(manifest.get("run_id") or ""),
        "talent_drafts": [
            {
                "draft_id": str(item.get("draft_id") or ""),
                "payload_hash": str(item.get("payload_hash") or ""),
            }
            for item in (talent_drafts or ())
        ],
        "talent_generation_error": talent_generation_error,
    }
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_summary(
    *,
    run_date: str,
    direction: str,
    task_exit_code: int,
    report_path: Path | None,
    report: Mapping[str, Any] | None,
    talent_drafts: list[Mapping[str, Any]] | None = None,
    talent_generation_error: str = "",
) -> str:
    if task_exit_code == 0:
        status = "✅ 已完成"
    elif task_exit_code == 2:
        status = "✅ 已完成，当天没有符合条件的公司"
    else:
        status = f"❌ 任务失败（退出码 {task_exit_code}）"

    lines = [
        "【Lead Rader 05:00 自动任务汇总】",
        f"日期：{run_date}",
        f"方向：{direction}",
        f"状态：{status}",
    ]
    if not report:
        lines.extend(
            [
                "入选公司：0 家",
                "报告：未生成",
                "请检查服务器 logs/fixed-source-daily.log。",
            ]
        )
        return "\n".join(lines)

    leads = report.get("leads") or []
    manifest = report.get("manifest") or {}
    source_summary = manifest.get("source_summary") or {}
    source_failures = source_summary.get("failures") or []
    lines.extend(
        [
            f"入选公司：{len(leads)} 家",
            f"信源异常：{len(source_failures)} 个",
        ]
    )
    if leads:
        lines.append("")
        lines.append("公司排序：")
        for index, lead in enumerate(leads[:20], start=1):
            company = str(lead.get("company") or "公司未识别").strip()
            score = _format_score(lead.get("score"))
            grade = str(lead.get("confidence_grade") or "-").strip()
            roles = [
                str(role).strip()
                for role in (lead.get("target_roles") or [])
                if str(role).strip()
            ]
            role_text = "、".join(roles[:3]) or "岗位待验证"
            lines.append(
                f"{index}. {company}｜{score}分｜置信度 {grade}｜{role_text}"
            )
    if report_path:
        lines.extend(["", f"服务器报告：{report_path}"])
    lines.append("得分依据和完整证据请查看报告，由人工做最终判断。")
    lines.append("")
    if talent_generation_error:
        lines.append(f"人才蓄水草稿生成失败：{talent_generation_error}")
    elif talent_drafts:
        lines.append(
            f"今日建议发布的人才蓄水职位（共 {len(talent_drafts)} 个）"
        )
        for index, item in enumerate(talent_drafts, start=1):
            lines.extend(
                [
                    "",
                    f"{index}. [{item.get('draft_id', '-')}] "
                    f"{item.get('recommended_title', '未命名草稿')}",
                    f"   人才画像：{item.get('talent_persona', '-')}",
                    f"   吸引角度：{item.get('attraction_angle', '-')}",
                    f"   为什么现在蓄水：{item.get('why_now', '-')}",
                ]
            )
        lines.extend(
            [
                "",
                "可用指令：",
                "- 发布全部",
                "- 发布 1,3,5",
                "- 跳过全部",
                "- 查看 2 的完整广告 JSON",
                "只有以上明确发布指令才构成批准；其他回复不会触发发布。",
            ]
        )
    else:
        lines.append("今日没有人才蓄水草稿；不会读取或沿用历史草稿。")
    return "\n".join(lines)


def load_talent_drafts(
    database: str | Path,
    *,
    run_date: str,
    direction: str,
) -> list[dict[str, Any]]:
    path = Path(database)
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT d.draft_id, d.payload_hash, d.status, d.draft_json
            FROM talent_pool_drafts d
            JOIN talent_pool_current_batches b
              ON b.run_date=d.run_date AND b.direction=d.direction
             AND b.source_run_id=d.source_run_id
            WHERE d.run_date=? AND d.direction=?
            ORDER BY d.ordinal
            """,
            (run_date, direction),
        ).fetchall()
    result = []
    for row in rows:
        draft = json.loads(row["draft_json"])
        result.append(
            {
                **draft,
                "draft_id": row["draft_id"],
                "payload_hash": row["payload_hash"],
                "status": row["status"],
            }
        )
    return result


def _format_score(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send Lead Rader daily Feishu summary")
    parser.add_argument("--direction", required=True)
    parser.add_argument("--task-exit-code", required=True, type=int)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--report-dir", default="reports-daily")
    parser.add_argument("--state-db", default="data/feishu-notifications.sqlite")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--fallback-env-file")
    parser.add_argument("--talent-state-db", default="data/talent-pool.sqlite")
    parser.add_argument("--talent-draft-exit-code", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client_class: type[FeishuMessageClient] = FeishuMessageClient,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        env = load_env_files(args.env_file, args.fallback_env_file)
        app_id = str(env.get("FEISHU_APP_ID") or "").strip()
        app_secret = str(env.get("FEISHU_APP_SECRET") or "").strip()
        if not app_id or not app_secret:
            raise ValueError("missing FEISHU_APP_ID or FEISHU_APP_SECRET")
        recipient = resolve_recipient(env)
        report_path, report = find_report(
            args.report_dir,
            run_date=args.run_date,
            direction=args.direction,
        )
        if args.task_exit_code not in {0, 2}:
            report_path = None
            report = None
        talent_drafts = (
            load_talent_drafts(
                args.talent_state_db,
                run_date=args.run_date,
                direction=args.direction,
            )
            if args.talent_draft_exit_code == 0 and report
            else []
        )
        talent_generation_error = (
            f"退出码 {args.talent_draft_exit_code}"
            if args.talent_draft_exit_code != 0 and report
            else ""
        )
        key = notification_key(
            run_date=args.run_date,
            direction=args.direction,
            task_exit_code=args.task_exit_code,
            report=report,
            talent_drafts=talent_drafts,
            talent_generation_error=talent_generation_error,
        )
        state = NotificationState(args.state_db)
        if state.was_sent(key) and not args.force:
            print("Feishu daily summary already sent; skipping duplicate.")
            return 0
        text = build_summary(
            run_date=args.run_date,
            direction=args.direction,
            task_exit_code=args.task_exit_code,
            report_path=report_path,
            report=report,
            talent_drafts=talent_drafts,
            talent_generation_error=talent_generation_error,
        )
        message_id = client_class(app_id, app_secret).send_text(
            recipient,
            text,
            idempotency_key=key,
        )
        state.record(key, message_id)
        print("Feishu daily summary sent.")
        return 0
    except Exception as error:
        print(
            f"Feishu daily summary failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
