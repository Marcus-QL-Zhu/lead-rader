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


def find_talent_bundle(
    output_dir: str | Path,
    *,
    run_date: str,
    direction: str,
    source_run_id: str = "",
) -> dict[str, Any] | None:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for path in Path(output_dir).glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        if (
            str(payload.get("run_date") or "") == run_date
            and str(payload.get("direction") or "") == direction
            and (
                not source_run_id
                or str(payload.get("source_run_id") or "") == source_run_id
            )
        ):
            candidates.append((path.stat().st_mtime, dict(payload)))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def notification_key(
    *,
    run_date: str,
    direction: str,
    task_exit_code: int,
    report: Mapping[str, Any] | None,
    talent_drafts: list[Mapping[str, Any]] | None = None,
    talent_generation_error: str = "",
    talent_generation_model: str = "",
    talent_snapshot_id: str = "",
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
        "talent_generation_model": talent_generation_model,
        "talent_snapshot_id": talent_snapshot_id,
    }
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _draft_target_text(item: Mapping[str, Any]) -> str:
    targets: list[str] = []
    for source in item.get("source_leads") or ():
        if not isinstance(source, Mapping):
            continue
        company = str(source.get("company") or "").strip()
        if not company:
            continue
        roles = [
            str(role).strip()
            for role in source.get("role_hypotheses") or ()
            if str(role).strip()
        ]
        role_text = "、".join(roles) or str(
            item.get("recommended_title") or "岗位待核"
        )
        targets.append(f"{company} → {role_text}")
    return "；".join(targets) or "关联公司与岗位待核"


def _liepin_json_text(item: Mapping[str, Any]) -> str:
    payload = item.get("public_payload")
    if not isinstance(payload, Mapping):
        return "{}"
    return json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def build_summary(
    *,
    run_date: str,
    direction: str,
    task_exit_code: int,
    report_path: Path | None,
    report: Mapping[str, Any] | None,
    talent_drafts: list[Mapping[str, Any]] | None = None,
    talent_generation_error: str = "",
    talent_generation_model: str = "",
    company_demands: list[Mapping[str, Any]] | None = None,
    include_liepin_json: bool = True,
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
    demand_values = company_demands or []
    demand_by_index = {
        int(item["lead_index"]): item
        for item in demand_values
        if isinstance(item, Mapping) and isinstance(item.get("lead_index"), int)
    }
    analysis_failures = sum(
        1 for item in demand_values if str(item.get("analysis_error") or "")
    )
    hypothesis_companies = sum(
        1 for item in demand_values if item.get("hypotheses")
    )
    lines.extend(
        [
            f"入选公司：{len(leads)} 家",
            f"信源异常：{len(source_failures)} 个",
        ]
    )
    dedicated_health = {}
    for source_run in source_summary.get("runs") or []:
        if not isinstance(source_run, Mapping):
            continue
        run_summary = source_run.get("run_summary") or {}
        candidate = run_summary.get("dedicated_aggregate") or {}
        if not candidate:
            health = source_run.get("health") or {}
            candidate = health.get("dedicated_aggregate") or {}
        if isinstance(candidate, Mapping) and candidate:
            dedicated_health = candidate
            break
    if dedicated_health:
        lines.append(
            f"\u4e13\u5c5e\u805a\u5408\u4fe1\u6e90\uff1a"
            f"{dedicated_health.get('source_count', 0)} \u4e2a\uff0c"
            f"\u5065\u5eb7 {dedicated_health.get('healthy_count', 0)}\uff0c"
            f"\u5f02\u5e38 {dedicated_health.get('failed_count', 0)}\uff0c"
            f"\u5f85\u5904\u7406 {dedicated_health.get('open_dead_letter_count', 0)}"
        )
    if talent_generation_model:
        lines.append(f"LLM 模型：{talent_generation_model}")
    if demand_values:
        lines.extend(
            [
                f"MiniMax 已分析：{len(demand_values)} 家",
                f"形成具体岗位假设：{hypothesis_companies} 家",
                f"分析失败：{analysis_failures} 家",
            ]
        )
    if leads:
        lines.append("")
        lines.append("公司排序：")
        for index, lead in enumerate(leads[:20], start=1):
            company = str(lead.get("company") or "公司未识别").strip()
            score = _format_score(lead.get("score"))
            grade = str(lead.get("confidence_grade") or "-").strip()
            demand = demand_by_index.get(index) or {}
            hypotheses = demand.get("hypotheses") or []
            roles = [
                str(item.get("specific_title") or "").strip()
                for item in hypotheses
                if isinstance(item, Mapping)
                and str(item.get("specific_title") or "").strip()
            ]
            if not demand_values:
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
        detail = talent_generation_error[:500]
        lines.append(f"职位草稿生成存在失败：{detail}")
    if talent_drafts:
        lines.append(
            f"今日建议发布职位（共 {len(talent_drafts)} 个）"
        )
        for index, item in enumerate(talent_drafts, start=1):
            lines.extend(
                [
                    "",
                    f"{index}. [{item.get('draft_id', '-')}] "
                    f"{item.get('recommended_title', '未命名草稿')}",
                    f"   目标公司/岗位：{_draft_target_text(item)}",
                    f"   人才画像：{item.get('talent_persona', '-')}",
                    f"   吸引角度：{item.get('attraction_angle', '-')}",
                    f"   为什么现在：{item.get('why_now', '-')}",
                    *([f"   猎聘 JSON：{_liepin_json_text(item)}"] if include_liepin_json else []),
                ]
            )
        lines.extend(
            [
                "",
                "当前消息仅供人工审核；每条猎聘 JSON 已与目标公司和岗位假设关联并持久化。",
                "如需批准，请明确要求 Codex/OpenClaw 查看当日草稿并执行审批流程。",
            ]
        )
    else:
        lines.append("今日没有人才蓄水草稿；不会读取或沿用历史草稿。")
    return "\n".join(lines)

FEISHU_TEXT_MAX_BYTES = 28_000


def _split_utf8(text: str, max_bytes: int = FEISHU_TEXT_MAX_BYTES) -> list[str]:
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for character in text:
        encoded_size = len(character.encode("utf-8"))
        if current and size + encoded_size > max_bytes:
            parts.append("".join(current))
            current = []
            size = 0
        current.append(character)
        size += encoded_size
    if current:
        parts.append("".join(current))
    return parts


def build_summary_parts(**kwargs: Any) -> list[str]:
    """Keep a normal run in one message; split safely when full JSON is large."""

    full = build_summary(**kwargs)
    if len(full.encode("utf-8")) <= FEISHU_TEXT_MAX_BYTES:
        return [full]
    base_kwargs = dict(kwargs)
    base_kwargs["include_liepin_json"] = False
    parts = _split_utf8(build_summary(**base_kwargs))
    drafts = kwargs.get("talent_drafts") or ()
    for index, item in enumerate(drafts, start=1):
        detail = (
            f"【猎聘 JSON {index}/{len(drafts)}】\n"
            f"[{item.get('draft_id', '-')}] {item.get('recommended_title', '-')}\n"
            f"目标公司/岗位：{_draft_target_text(item)}\n"
            f"{_liepin_json_text(item)}"
        )
        if len(detail.encode("utf-8")) > FEISHU_TEXT_MAX_BYTES:
            raise ValueError("single Liepin JSON exceeds Feishu text limit")
        parts.append(detail)
    return parts

def load_talent_drafts(
    database: str | Path,
    *,
    run_date: str,
    direction: str,
    source_run_id: str = "",
) -> list[dict[str, Any]]:
    path = Path(database)
    if not path.exists():
        return []
    from .talent_pool_store import TalentPoolStore

    TalentPoolStore(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT d.draft_id, d.payload_hash, d.status, d.draft_json
            FROM talent_pool_drafts d
            JOIN talent_pool_current_snapshot_drafts c
              ON c.run_date=d.run_date AND c.direction=d.direction
             AND c.draft_id=d.draft_id
            WHERE d.run_date=? AND d.direction=?
              AND (?='' OR d.source_run_id=?)
            ORDER BY c.ordinal
            """,
            (run_date, direction, source_run_id, source_run_id),
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
    parser.add_argument(
        "--talent-output-dir",
        default="reports-daily/talent-pool",
    )
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
        source_run_id = str(
            ((report or {}).get("manifest") or {}).get("run_id") or ""
        )
        from .talent_pool_store import TalentPoolStore

        talent_store = TalentPoolStore(args.talent_state_db)
        if args.talent_draft_exit_code not in {0, 72}:
            talent_bundle = None
        elif report and source_run_id:
            talent_bundle = talent_store.current_bundle(
                args.run_date,
                args.direction,
                source_run_id=source_run_id,
            )
        else:
            talent_bundle = None
        talent_drafts = [
            dict(item)
            for item in (talent_bundle or {}).get("drafts") or ()
            if isinstance(item, Mapping)
        ]
        talent_generation_error = str(
            (talent_bundle or {}).get("generation_error") or ""
        ).strip()
        talent_generation_model = str(
            (talent_bundle or {}).get("generation_model") or ""
        ).strip()
        talent_snapshot_id = str(
            (talent_bundle or {}).get("_snapshot_id") or ""
        ).strip()
        if args.talent_draft_exit_code != 0 and report:
            exit_detail = f"退出码 {args.talent_draft_exit_code}"
            talent_generation_error = "; ".join(
                item for item in (exit_detail, talent_generation_error) if item
            )
        company_demands = list(
            (talent_bundle or {}).get("company_demand_analysis") or []
        )
        key = notification_key(
            run_date=args.run_date,
            direction=args.direction,
            task_exit_code=args.task_exit_code,
            report=report,
            talent_drafts=talent_drafts,
            talent_generation_error=talent_generation_error,
            talent_generation_model=talent_generation_model,
            talent_snapshot_id=talent_snapshot_id,
        )
        parts = build_summary_parts(
            run_date=args.run_date,
            direction=args.direction,
            task_exit_code=args.task_exit_code,
            report_path=report_path,
            report=report,
            talent_drafts=talent_drafts,
            talent_generation_error=talent_generation_error,
            talent_generation_model=talent_generation_model,
            company_demands=company_demands,
        )
        state = NotificationState(args.state_db)
        client = client_class(app_id, app_secret)
        sent_count = 0
        for index, text in enumerate(parts, start=1):
            part_manifest = f"{key}:{index}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
            part_key = hashlib.sha256(part_manifest.encode("utf-8")).hexdigest()
            if state.was_sent(part_key) and not args.force:
                continue
            message_id = client.send_text(
                recipient,
                text,
                idempotency_key=part_key,
            )
            state.record(part_key, message_id)
            sent_count += 1
        if sent_count:
            print(f"Feishu daily summary sent ({sent_count} message(s)).")
        else:
            print("Feishu daily summary already sent; skipping duplicate.")
        return 0
    except Exception as error:
        print(
            f"Feishu daily summary failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
