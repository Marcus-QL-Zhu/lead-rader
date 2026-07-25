from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol


class SearchProvider(Protocol):
    def search(self, query: str, limit: int = 10): ...


def _id(kind: str, value: str) -> str:
    normalized = ''.join(value.lower().split())
    return hashlib.sha256(f'{kind}:{normalized}'.encode('utf-8')).hexdigest()[:24]


@dataclass(frozen=True)
class PublicPerson:
    person_id: str
    name: str
    title: str
    organization: str
    category: str
    evidence_url: str
    evidence_text: str
    confidence: float
    inferred: bool = True


@dataclass(frozen=True)
class InvestmentInstitution:
    institution_id: str
    name: str
    role: str
    sectors: tuple[str, ...]
    evidence_url: str
    confidence: float


@dataclass
class DeepResearchReport:
    company: str
    direction: str
    investors: list[PublicPerson] = field(default_factory=list)
    institutions: list[InvestmentInstitution] = field(default_factory=list)
    hiring_managers: list[PublicPerson] = field(default_factory=list)
    hr_people: list[PublicPerson] = field(default_factory=list)
    founders: list[PublicPerson] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    generated_at: str = ''
    cached: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class RelationshipStore:
    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS public_people (
                    person_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    organization TEXT NOT NULL,
                    category TEXT NOT NULL,
                    evidence_url TEXT NOT NULL,
                    evidence_text TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    inferred INTEGER NOT NULL,
                    last_verified_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investment_institutions (
                    institution_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    sectors_json TEXT NOT NULL,
                    evidence_url TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    last_verified_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS public_relationships (
                    relationship_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    evidence_url TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    inferred INTEGER NOT NULL,
                    valid_from TEXT NOT NULL DEFAULT '',
                    valid_to TEXT NOT NULL DEFAULT '',
                    last_verified_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deep_research_cache (
                    company_name TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    PRIMARY KEY (company_name, direction)
                );
            ''')

    def save(self, report: DeepResearchReport) -> None:
        now = report.generated_at or datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.database) as connection:
            institution_names = {item.name for item in report.institutions}
            for person in report.investors + report.hiring_managers + report.hr_people + report.founders:
                connection.execute('''
                    INSERT INTO public_people VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        title=excluded.title, organization=excluded.organization,
                        category=excluded.category, evidence_url=excluded.evidence_url,
                        evidence_text=excluded.evidence_text, confidence=excluded.confidence,
                        inferred=excluded.inferred, last_verified_at=excluded.last_verified_at
                ''', (
                    person.person_id, person.name, person.title, person.organization,
                    person.category, person.evidence_url, person.evidence_text,
                    person.confidence, int(person.inferred), now,
                ))
                relation_type = {
                    'external_investor': 'COMMENTED_ON',
                    'hiring_manager': 'POTENTIAL_HIRING_MANAGER_AT',
                    'hr': 'HR_AT',
                    'founder': 'FOUNDED',
                }.get(person.category, 'RELATED_TO')
                rel_id = _id('relationship', f'{person.person_id}|{relation_type}|{report.company}')
                connection.execute('''
                    INSERT INTO public_relationshipS
                        (relationship_id, subject_id, relation_type, object_id, company_name,
                         evidence_url, confidence, inferred, last_verified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(relationship_id) DO UPDATE SET
                        evidence_url=excluded.evidence_url, confidence=excluded.confidence,
                        inferred=excluded.inferred, last_verified_at=excluded.last_verified_at
                ''', (
                    rel_id, person.person_id, relation_type, _id('company', report.company),
                    report.company, person.evidence_url, person.confidence, int(person.inferred), now,
                ))
                if (
                    person.category == 'external_investor'
                    and person.organization in institution_names
                ):
                    institution_id = _id('institution', person.organization)
                    employment_id = _id(
                        'relationship',
                        f'{person.person_id}|WORKS_AT|{institution_id}',
                    )
                    connection.execute('''
                        INSERT INTO public_relationshipS
                            (relationship_id, subject_id, relation_type, object_id,
                             company_name, evidence_url, confidence, inferred,
                             last_verified_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(relationship_id) DO UPDATE SET
                            evidence_url=excluded.evidence_url,
                            confidence=excluded.confidence,
                            inferred=excluded.inferred,
                            last_verified_at=excluded.last_verified_at
                    ''', (
                        employment_id, person.person_id, 'WORKS_AT', institution_id,
                        report.company, person.evidence_url, person.confidence,
                        int(person.inferred), now,
                    ))
            for institution in report.institutions:
                connection.execute('''
                    INSERT INTO investment_institutions VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(institution_id) DO UPDATE SET
                        role=excluded.role, sectors_json=excluded.sectors_json,
                        evidence_url=excluded.evidence_url, confidence=excluded.confidence,
                        last_verified_at=excluded.last_verified_at
                ''', (
                    institution.institution_id, institution.name, institution.role,
                    json.dumps(institution.sectors, ensure_ascii=False),
                    institution.evidence_url, institution.confidence, now,
                ))
                rel_id = _id('relationship', f'{institution.institution_id}|INVESTED_IN|{report.company}')
                connection.execute('''
                    INSERT INTO public_relationshipS
                        (relationship_id, subject_id, relation_type, object_id, company_name,
                         evidence_url, confidence, inferred, last_verified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(relationship_id) DO UPDATE SET
                        evidence_url=excluded.evidence_url, confidence=excluded.confidence,
                        last_verified_at=excluded.last_verified_at
                ''', (
                    rel_id, institution.institution_id,
                    'LED_INVESTMENT_IN' if institution.role == 'lead' else 'INVESTED_IN',
                    _id('company', report.company), report.company,
                    institution.evidence_url, institution.confidence, 0, now,
                ))
            connection.execute('''
                INSERT INTO deep_research_cache VALUES (?, ?, ?, ?)
                ON CONFLICT(company_name, direction) DO UPDATE SET
                    payload_json=excluded.payload_json, generated_at=excluded.generated_at
            ''', (report.company, report.direction, json.dumps(report.to_dict(), ensure_ascii=False), now))

    def cached(self, company: str, direction: str, max_age_days: int = 90) -> DeepResearchReport | None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                'SELECT payload_json, generated_at FROM deep_research_cache WHERE company_name=? AND direction=?',
                (company, direction),
            ).fetchone()
        if not row:
            return None
        try:
            generated = datetime.fromisoformat(row[1].replace('Z', '+00:00'))
        except ValueError:
            return None
        if generated < cutoff:
            return None
        payload = json.loads(row[0])
        payload['investors'] = [PublicPerson(**item) for item in payload.get('investors', [])]
        payload['institutions'] = [
            InvestmentInstitution(**{**item, 'sectors': tuple(item.get('sectors', []))})
            for item in payload.get('institutions', [])
        ]
        for key in ('hiring_managers', 'hr_people', 'founders'):
            payload[key] = [PublicPerson(**item) for item in payload.get(key, [])]
        payload['cached'] = True
        return DeepResearchReport(**payload)

    def graph(self, company: str = '') -> dict:
        query = '''
            SELECT r.subject_id, r.relation_type, r.object_id, r.company_name,
                   r.evidence_url, r.confidence, r.inferred
            FROM public_relationships r
        '''
        params: tuple = ()
        if company:
            query += ' WHERE r.company_name=?'
            params = (company,)
        with sqlite3.connect(self.database) as connection:
            edge_rows = list(connection.execute(query, params))
            people = {
                row[0]: {
                    'id': row[0], 'type': 'person', 'name': row[1],
                    'title': row[2], 'organization': row[3],
                    'category': row[4],
                }
                for row in connection.execute(
                    'SELECT person_id, name, title, organization, category '
                    'FROM public_people'
                )
            }
            institutions = {
                row[0]: {
                    'id': row[0], 'type': 'investment_institution',
                    'name': row[1], 'role': row[2],
                    'sectors': json.loads(row[3]),
                }
                for row in connection.execute(
                    'SELECT institution_id, name, role, sectors_json '
                    'FROM investment_institutions'
                )
            }
        edges = [
            {
                'subject_id': row[0], 'relation': row[1], 'object_id': row[2],
                'company': row[3], 'evidence_url': row[4],
                'confidence': row[5], 'inferred': bool(row[6]),
            }
            for row in edge_rows
        ]
        referenced = {
            identifier
            for edge in edges
            for identifier in (edge['subject_id'], edge['object_id'])
        }
        nodes = [
            node for identifier, node in {**people, **institutions}.items()
            if identifier in referenced
        ]
        company_names = sorted({edge['company'] for edge in edges if edge['company']})
        nodes.extend({
            'id': _id('company', name), 'type': 'company', 'name': name,
        } for name in company_names)
        return {'nodes': nodes, 'edges': edges}


INSTITUTION_PATTERN = re.compile(
    r'(红杉中国|高瓴创投|高瓴资本|深创投|IDG资本|源码资本|顺为资本|真格基金|'
    r'[\u4e00-\u9fffA-Za-z0-9·]{2,24}?(?:资本|创投|基金|投资|产投|Ventures|Capital))', re.I,
)
PERSON_WITH_TITLE_PATTERN = re.compile(
    r'([\u4e00-\u9fff]{2,4})[^。；;]{0,24}(管理合伙人|主管合伙人|投资合伙人|合伙人|'
    r'董事总经理|执行董事|投资负责人|投资总监|MD|创始人|联合创始人|CEO|CTO|HRD|人力资源总监|招聘负责人|HRBP)',
    re.I,
)
TITLE_WITH_PERSON_PATTERN = re.compile(
    r'(管理合伙人|主管合伙人|投资合伙人|合伙人|董事总经理|执行董事|投资负责人|投资总监|'
    r'MD|联合创始人|创始人|CEO|CTO|HRD|人力资源总监|招聘负责人|HRBP)'
    r'[^。；;，,]{0,12}?([\u4e00-\u9fff]{2,4})(?:表示|认为|指出|介绍|负责|曾任|称|[，,。；;]|$)',
    re.I,
)


class DeepResearchEngine:
    def __init__(self, provider: SearchProvider, store: RelationshipStore):
        self.provider = provider
        self.store = store

    def research(self, company: str, direction: str, *, refresh: bool = False) -> DeepResearchReport:
        if not refresh and (cached := self.store.cached(company, direction)):
            return cached
        queries = [
            f'{company} 融资 领投 投资人 合伙人 评论',
            f'{company} 投资机构 投资逻辑 {direction}',
            f'{company} 创始人 联合创始人 工作经历',
            f'{company} {direction} 业务负责人 研发负责人 总监',
            f'{company} HRD HRBP 人力资源总监 招聘负责人',
        ]
        report = DeepResearchReport(
            company=company, direction=direction, queries=queries,
            generated_at=datetime.now(timezone.utc).isoformat(),
            caveats=[
                '公开评论只支持“可能主导投资”的推断，不能当作确定事实。',
                '所有姓名、职位和任职关系在使用前应打开来源复核。',
            ],
        )
        seen_people: set[tuple[str, str]] = set()
        seen_institutions: set[str] = set()
        for index, query in enumerate(queries):
            for result in self.provider.search(query, limit=8):
                text = f'{result.title} {result.snippet}'
                for institution in INSTITUTION_PATTERN.findall(text):
                    if institution in seen_institutions:
                        continue
                    seen_institutions.add(institution)
                    role = 'lead' if '领投' in text and institution in text else 'participant_or_related'
                    report.institutions.append(InvestmentInstitution(
                        institution_id=_id('institution', institution), name=institution,
                        role=role, sectors=(direction,), evidence_url=result.url,
                        confidence=0.85 if role == 'lead' else 0.6,
                    ))
                people_with_titles = list(PERSON_WITH_TITLE_PATTERN.findall(text))
                people_with_titles.extend((name, title) for title, name in TITLE_WITH_PERSON_PATTERN.findall(text))
                for name, title in people_with_titles:
                    key = (name, title.lower())
                    if key in seen_people:
                        continue
                    seen_people.add(key)
                    organization_match = INSTITUTION_PATTERN.search(text)
                    organization = organization_match.group(1) if organization_match else company
                    if index <= 1 and title.lower() in {
                        '合伙人', '管理合伙人', '主管合伙人', '投资合伙人', '董事总经理', '执行董事', '投资负责人', '投资总监', 'md'}:
                        category = 'external_investor'
                    elif index == 2 or '创始' in title or title.upper() in {'CEO', 'CTO'}:
                        category = 'founder'
                    elif index == 4 or title.upper() in {'HRD', 'HRBP'} or '人力' in title or '招聘' in title:
                        category = 'hr'
                    else:
                        category = 'hiring_manager'
                    person = PublicPerson(
                        person_id=_id('person', f'{name}|{organization}'), name=name, title=title,
                        organization=organization, category=category, evidence_url=result.url,
                        evidence_text=text[:500], confidence=0.65, inferred=True,
                    )
                    {
                        'external_investor': report.investors,
                        'founder': report.founders,
                        'hr': report.hr_people,
                        'hiring_manager': report.hiring_managers,
                    }[category].append(person)
        lead_institutions = [
            institution
            for institution in report.institutions
            if institution.role == 'lead'
        ][:3]
        for institution in lead_institutions:
            query = (
                f'{institution.name} {direction} '
                '合伙人 董事总经理 MD 投资负责人'
            )
            report.queries.append(query)
            for result in self.provider.search(query, limit=8):
                text = f'{result.title} {result.snippet}'
                people_with_titles = list(PERSON_WITH_TITLE_PATTERN.findall(text))
                people_with_titles.extend(
                    (name, title)
                    for title, name in TITLE_WITH_PERSON_PATTERN.findall(text)
                )
                for name, title in people_with_titles:
                    if title.lower() not in {
                        '合伙人', '管理合伙人', '主管合伙人', '投资合伙人',
                        '董事总经理', '执行董事', '投资负责人', '投资总监', 'md',
                    }:
                        continue
                    key = (name, title.lower())
                    if key in seen_people:
                        continue
                    seen_people.add(key)
                    report.investors.append(PublicPerson(
                        person_id=_id('person', f'{name}|{institution.name}'),
                        name=name,
                        title=title,
                        organization=institution.name,
                        category='external_investor',
                        evidence_url=result.url,
                        evidence_text=text[:500],
                        confidence=0.7,
                        inferred=True,
                    ))
        if report.institutions and not lead_institutions:
            report.caveats.append(
                '已找到投资机构，但公开资料未能确认领投机构；未把参投机构误标为领投。'
            )
        self.store.save(report)
        return report
