from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import CompanyLead


def stable_company_id(company: str) -> str:
    normalized = ''.join(company.lower().split())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]


def lead_projection(lead: CompanyLead) -> dict:
    return {
        'Company ID': stable_company_id(lead.company),
        '公司': lead.company,
        '方向': lead.direction,
        '模式': lead.request_mode,
        '得分': lead.score,
        '置信度': lead.confidence_grade,
        '时机': lead.timing_stage,
        '预测岗位': '、'.join(lead.target_roles),
        '招聘逻辑': lead.hiring_thesis,
        '得分明细': json.dumps([
            {'项': item.label, '分': item.points, '原因': item.reason}
            for item in lead.score_components
        ], ensure_ascii=False),
        '证据链接': '\n'.join(item.source_url for item in lead.evidence),
        '基础研究': json.dumps(lead.basic_research, ensure_ascii=False),
        '处理状态': '待研究',
    }


@dataclass(frozen=True)
class ProjectionChange:
    operation: str
    company_id: str
    fields: dict
    previous_record_id: str = ''


class ProjectionState:
    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS feishu_projection (
                    company_id TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL DEFAULT '',
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feishu_cursors (
                    stream TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            ''')

    @staticmethod
    def _hash(fields: dict) -> str:
        payload = json.dumps(fields, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def changes(self, leads: Iterable[CompanyLead]) -> list[ProjectionChange]:
        current = {stable_company_id(lead.company): lead_projection(lead) for lead in leads}
        with sqlite3.connect(self.database) as connection:
            previous = {
                row[0]: {'record_id': row[1], 'hash': row[2], 'active': bool(row[3])}
                for row in connection.execute(
                    'SELECT company_id, record_id, payload_hash, active FROM feishu_projection'
                )
            }
        output: list[ProjectionChange] = []
        for company_id, fields in current.items():
            digest = self._hash(fields)
            old = previous.get(company_id)
            if old is None:
                output.append(ProjectionChange('create', company_id, fields))
            elif old['hash'] != digest or not old['active']:
                output.append(ProjectionChange('update', company_id, fields, old['record_id']))
        for company_id, old in previous.items():
            if old['active'] and company_id not in current:
                output.append(ProjectionChange(
                    'deactivate', company_id, {'处理状态': '本期撤选'}, old['record_id'],
                ))
        return output

    def commit(self, change: ProjectionChange, record_id: str = '') -> None:
        now = datetime.now(timezone.utc).isoformat()
        fields = change.fields
        digest = self._hash(fields)
        active = 0 if change.operation == 'deactivate' else 1
        with sqlite3.connect(self.database) as connection:
            connection.execute('''
                INSERT INTO feishu_projection
                    (company_id, record_id, payload_hash, payload_json, active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id) DO UPDATE SET
                    record_id=CASE WHEN excluded.record_id='' THEN feishu_projection.record_id ELSE excluded.record_id END,
                    payload_hash=excluded.payload_hash,
                    payload_json=excluded.payload_json,
                    active=excluded.active,
                    updated_at=excluded.updated_at
            ''', (
                change.company_id, record_id or change.previous_record_id, digest,
                json.dumps(fields, ensure_ascii=False, sort_keys=True), active, now,
            ))


class FeishuBitableClient:
    def __init__(self, app_id: str, app_secret: str, app_token: str, table_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self._token = ''

    def _json_request(self, url: str, payload: dict, token: str = '') -> dict:
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        request = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers=headers, method='POST',
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
        if result.get('code', 0) != 0:
            raise RuntimeError(f'Feishu API error {result.get("code")}: {result.get("msg")}')
        return result

    def token(self) -> str:
        if not self._token:
            result = self._json_request(
                'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                {'app_id': self.app_id, 'app_secret': self.app_secret},
            )
            self._token = result['tenant_access_token']
        return self._token

    def apply(self, change: ProjectionChange) -> str:
        base = (
            f'https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}'
            f'/tables/{self.table_id}/records'
        )
        if change.operation == 'create':
            result = self._json_request(base, {'fields': change.fields}, self.token())
            return result['data']['record']['record_id']
        if not change.previous_record_id:
            raise ValueError(f'{change.operation} requires a Feishu record_id')
        url = f'{base}/{change.previous_record_id}'
        request = urllib.request.Request(
            url, data=json.dumps({'fields': change.fields}, ensure_ascii=False).encode('utf-8'),
            headers={
                'Content-Type': 'application/json; charset=utf-8',
                'Authorization': f'Bearer {self.token()}',
            }, method='PUT',
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
        if result.get('code', 0) != 0:
            raise RuntimeError(f'Feishu API error {result.get("code")}: {result.get("msg")}')
        return change.previous_record_id


def sync_leads(
    leads: Iterable[CompanyLead],
    state: ProjectionState,
    client: FeishuBitableClient | None = None,
    dry_run_path: str | Path | None = None,
) -> list[ProjectionChange]:
    changes = state.changes(leads)
    if dry_run_path:
        target = Path(dry_run_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps([
            {
                'operation': item.operation,
                'company_id': item.company_id,
                'record_id': item.previous_record_id,
                'fields': item.fields,
            }
            for item in changes
        ], ensure_ascii=False, indent=2), encoding='utf-8')
    if client:
        for change in changes:
            record_id = client.apply(change)
            state.commit(change, record_id)
    return changes
