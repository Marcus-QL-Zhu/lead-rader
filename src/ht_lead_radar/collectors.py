from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from .models import Evidence, OutreachRoute
from .signals import SIGNALS, infer_signal
from .taxonomy import classify_seniority, profile_for


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published_at: str = ''


# Kept as a compatibility view for callers that inspect the old constant.
EVENT_PATTERNS: tuple[tuple[str, str, str], ...] = tuple(
    (signal.name, signal.phase, signal.pattern.pattern)
    for signal in SIGNALS
)


COMPANY_PATTERNS = (
    re.compile(r'(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()]{2,24}?)(?:完成|获|宣布|计划|启动|签署|拿下|发布|开启)'),
    re.compile(r'(?:企业|公司)[“「]?(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()]{2,24})[”」]?'),
    re.compile(r"(?P<name>[A-Z][A-Za-z0-9 .&'’-]{1,50}?)(?:\s+raises?\s+|\s+raised\s+)", re.I),
)


def _clean_html(value: str) -> str:
    without_tags = re.sub(r'<[^>]+>', ' ', value or '')
    return re.sub(r'\s+', ' ', html.unescape(without_tags)).strip()


def infer_event(text: str) -> tuple[str, str]:
    return infer_signal(text)


def extract_company(title: str) -> str | None:
    cleaned = re.sub(r'[-_|｜].*$', '', title).strip()
    for pattern in COMPANY_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            name = match.group('name').strip('：:，, “”「」')
            if 2 <= len(name) <= 24:
                return name
    return None


KNOWN_COMPANY_ALIASES = {
    'linkerbot': '灵心巧手',
    'oymotion': '傲意科技',
    'dexrobot': '灵巧智能',
}

GENERIC_ENTITY_NAMES = {
    '人形机器人', '机器人行业', '机器人研究', '智能机器人', '具身智能',
    '人工智能', '智能制造', '机器人科技', '机器人公司',
}

INVALID_ENTITY_FRAGMENTS = (
    '哪些', '多少', '不超过', '市场份额', '位列', '提供', '支撑', '行业',
    '盘点', '排名', '国内品牌', '全球品牌', '的智能化',
)


def is_plausible_company(name: str, known_seeds: tuple[str, ...] = ()) -> bool:
    if name in known_seeds:
        return True
    if not 2 <= len(name) <= 16:
        return False
    if name in GENERIC_ENTITY_NAMES or any(fragment in name for fragment in INVALID_ENTITY_FRAGMENTS):
        return False
    return bool(re.search(r'(?:机器人|科技|智能|巧手)$', name, flags=re.I))


def company_mentioned(company: str, text: str) -> bool:
    lowered = text.lower()
    if company.lower() in lowered:
        return True
    aliases = [alias for alias, canonical in KNOWN_COMPANY_ALIASES.items() if canonical == company]
    return any(alias in lowered for alias in aliases)


def company_local_context(company: str, title: str, snippet: str, window: int = 180) -> str:
    '''Return text near the target company so other companies cannot leak events.'''
    names = [company]
    names.extend(alias for alias, canonical in KNOWN_COMPANY_ALIASES.items() if canonical == company)
    contexts: list[str] = []
    if any(name.lower() in title.lower() for name in names):
        contexts.append(title)
    sentences = re.split(r'(?<=[。！？；!?;])\s*|(?:\.{2,}|…{1,})|[\r\n]+', snippet)
    for sentence in sentences:
        if any(name.lower() in sentence.lower() for name in names):
            contexts.append(sentence[:window * 2])
    return ' '.join(dict.fromkeys(contexts))


def result_relevant_to_company(company: str, title: str, snippet: str) -> str:
    local_context = company_local_context(company, title, snippet)
    if not local_context:
        return ''
    if company_mentioned(company, title):
        return f'{title} {snippet}'
    if not company_mentioned(company, title):
        titled_companies = extract_company_candidates(title)
        if any(candidate != company for candidate in titled_companies):
            return ''
    return local_context


def grade_source(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(':')[0]
    official_domains = (
        'gov.cn', 'sse.com.cn', 'cninfo.com.cn', 'hkexnews.hk',
        'linkerbot.cn', 'inspire-robots.com', 'dmrobot.com', 'oymotion.com', 'dex-robot.com',
    )
    professional_media = (
        'stcn.com', 'cls.cn', 'pedaily.cn', 'thepaper.cn', '36kr.com',
        'cnfin.com', 'jjckb.cn', '21jingji.com', 'caixin.com', 'yicai.com',
        'sina.com.cn', 'qq.com',
    )
    if any(host == domain or host.endswith(f'.{domain}') for domain in official_domains):
        return 'A'
    if any(host == domain or host.endswith(f'.{domain}') for domain in professional_media):
        return 'B'
    return 'C'


def extract_company_candidates(text: str) -> list[str]:
    lowered = text.lower()
    candidates: list[str] = []
    for alias, canonical in KNOWN_COMPANY_ALIASES.items():
        if alias in lowered and canonical not in candidates:
            candidates.append(canonical)
    patterns = (
        r'[“「](.{2,20}?(?:机器人|科技|智能|巧手))[”」]',
        r'(?:公司|企业|厂商|团队)(?:为|是|：|:)?([\u4e00-\u9fffA-Za-z]{2,16}(?:机器人|科技|智能|巧手))',
        r'([\u4e00-\u9fffA-Za-z]{2,12}(?:机器人|科技|智能|巧手))(?=[A-Z\s，,。；;：:])',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = match.group(1).strip('“”「」：:，, ')
            candidate = re.sub(r'^(?:具身智能|人形机器人|国内|全球|企业)', '', candidate)
            if candidate and is_plausible_company(candidate) and candidate not in candidates:
                candidates.append(candidate)
    direct = extract_company(text)
    if direct and is_plausible_company(direct) and direct not in candidates:
        candidates.append(direct)
    return candidates


def _event_date_from_text(text: str, fallback: str) -> str:
    cleaned = re.sub(r'[*_]', '', text)
    patterns = (
        r'(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?',
        r'(20\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, cleaned):
            year, month, day = match.groups()
            try:
                parsed = datetime(int(year), int(month), int(day or 1))
                return parsed.date().isoformat()
            except ValueError:
                continue
    fallback_match = re.search(r'20\d{2}-\d{2}-\d{2}', fallback or '')
    if fallback_match:
        try:
            return datetime.fromisoformat(fallback_match.group(0)).date().isoformat()
        except ValueError:
            pass
    if fallback:
        return _event_date_from_text(fallback, '')
    return ''


class BingRSSCollector:
    endpoint = 'https://www.bing.com/search?format=rss&q='
    provider_name = 'Bing RSS'

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        url = self.endpoint + urllib.parse.quote_plus(query)
        request = urllib.request.Request(url, headers={'User-Agent': 'HT-Lead-Radar/0.1'})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read()
        root = ET.fromstring(payload)
        results: list[SearchResult] = []
        for item in root.findall('.//item')[:limit]:
            results.append(SearchResult(
                title=_clean_html(item.findtext('title') or ''),
                url=(item.findtext('link') or '').strip(),
                snippet=_clean_html(item.findtext('description') or ''),
                published_at=(item.findtext('pubDate') or '').strip(),
            ))
        return results

    def collect(self, direction: str, year: int = 2026, limit_per_query: int = 10) -> list[Evidence]:
        profile = profile_for(direction)
        discovery_queries = [
            f'{direction} 融资 扩产 量产 订单 {year}',
            f'{direction} 工厂 基地 项目 战略合作 {year}',
            f'{direction} 企业 盘点 龙头 厂商 {year}',
        ]
        candidates = list(profile.seed_companies)
        for query in discovery_queries:
            for result in self.search(query, limit=limit_per_query):
                combined = f'{result.title} {result.snippet}'
                for candidate in extract_company_candidates(combined):
                    if is_plausible_company(candidate, profile.seed_companies) and candidate not in candidates:
                        candidates.append(candidate)

        seen: set[tuple[str, str]] = set()
        evidence: list[Evidence] = []
        for company in candidates[:12]:
            queries = (
                f'{company} 融资 量产 工厂 订单 {year}',
                f'{company} 交付 产能 海外 战略合作 {year}',
            )
            for query in queries:
                for result in self.search(query, limit=limit_per_query):
                    key = (company, result.url)
                    if not result.url or key in seen:
                        continue
                    local_context = result_relevant_to_company(company, result.title, result.snippet)
                    if not local_context:
                        continue
                    event_type, phase = infer_event(local_context)
                    if event_type in {'other', 'job_ad'}:
                        continue
                    seen.add(key)
                    evidence.append(Evidence(
                        company=company,
                        event_type=event_type,
                        phase=phase,
                        event_date=_event_date_from_text(local_context, result.published_at),
                        title=result.title,
                        snippet=result.snippet[:400],
                        source_url=result.url,
                        source_name=self.provider_name,
                        source_grade=grade_source(result.url),
                        direction=direction,
                    ))
        return evidence

    def collect_routes(self, company: str, direction: str, limit_per_query: int = 8) -> list[OutreachRoute]:
        queries = (
            f'{company} {direction} 融资 投资方 领投 投资人',
            f'{company} 创始人 毕业 校友 师从',
            f'{company} 创始人 曾任 前同事 团队来自',
        )
        routes: list[OutreachRoute] = []
        seen: set[tuple[str, str]] = set()
        for query in queries:
            for result in self.search(query, limit=limit_per_query):
                local_context = result_relevant_to_company(company, result.title, result.snippet)
                if not local_context:
                    continue
                text = f'{result.title} {result.snippet}'
                for route in infer_routes_from_text(text, result.url, company=company):
                    key = (route.kind, route.target)
                    if key not in seen:
                        seen.add(key)
                        routes.append(route)
        return routes


class MetasoCollector(BingRSSCollector):
    provider_name = 'Metaso public web search'

    def __init__(
        self,
        api_key: str,
        base_url: str = 'https://metaso.cn',
        timeout: float = 60.0,
    ):
        super().__init__(timeout=timeout)
        if not api_key:
            raise ValueError('METASO_API_KEY is required')
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        payload = json.dumps({
            'q': query,
            'scope': 'webpage',
            'includeSummary': True,
            'conciseSnippet': True,
        }, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            f'{self.base_url}/api/v1/search',
            data=payload,
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'HT-Lead-Radar/0.1',
            },
            method='POST',
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
        if body.get('errCode'):
            error_code = body.get('errCode')
            error_message = body.get('errMsg', 'request failed')
            raise RuntimeError(f'Metaso error {error_code}: {error_message}')
        results: list[SearchResult] = []
        for page in body.get('webpages', [])[:limit]:
            results.append(SearchResult(
                title=_clean_html(str(page.get('title') or '')),
                url=str(page.get('link') or page.get('url') or '').strip(),
                snippet=_clean_html(str(page.get('snippet') or page.get('summary') or '')),
                published_at=str(page.get('date') or page.get('publishedAt') or ''),
            ))
        return results


class SearXNGCollector(BingRSSCollector):
    provider_name = 'SearXNG metasearch'

    def __init__(self, base_url: str = 'http://localhost:8080', timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.base_url = base_url.rstrip('/')

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        payload = urllib.parse.urlencode({
            'q': query,
            'format': 'json',
            'categories': 'general',
            'language': 'zh-CN',
        }).encode('utf-8')
        request = urllib.request.Request(
            f'{self.base_url}/search',
            data=payload,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'User-Agent': 'HT-Lead-Radar/0.1',
            },
            method='POST',
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
        return [SearchResult(
            title=_clean_html(str(item.get('title') or '')),
            url=str(item.get('url') or '').strip(),
            snippet=_clean_html(str(item.get('content') or '')),
            published_at=str(item.get('publishedDate') or item.get('pubdate') or ''),
        ) for item in body.get('results', [])[:limit]]


def load_env_file(path: str | Path | None) -> dict[str, str]:
    values = dict(os.environ)
    if not path:
        return values
    env_path = Path(path)
    if not env_path.exists():
        raise FileNotFoundError(env_path)
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip(chr(34)).strip(chr(39))
    return values


def load_demo_fixture(direction: str) -> tuple[list[Evidence], dict]:
    if direction.strip() != '灵巧手':
        raise ValueError('当前内置验收夹具仅覆盖“灵巧手”；其他方向请使用 --live')
    from .demo_data import DEMO_DEXTEROUS_HAND
    payload = DEMO_DEXTEROUS_HAND
    evidence = [Evidence(
        company=item['company'],
        event_type=item['event_type'],
        phase=item['phase'],
        event_date=item['event_date'],
        title=item['title'],
        snippet=item['snippet'],
        source_url=item['source_url'],
        source_name=item['source_name'],
        source_grade=item.get('source_grade', 'B'),
        direction=direction,
        people=tuple(item.get('people', [])),
        organizations=tuple(item.get('organizations', [])),
    ) for item in payload['evidence']]
    return evidence, payload


def load_replay(path: str | Path, direction: str) -> tuple[list[Evidence], dict]:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    evidence: list[Evidence] = []
    metadata: dict = {'routes': {}, 'ad_checks': {}}
    seen: set[tuple[str, str]] = set()
    for lead in payload:
        company = str(lead['company'])
        retained_ads = 0
        company_evidence_texts: list[tuple[str, str]] = []
        for item in lead.get('evidence', []):
            title = str(item.get('title') or '')
            snippet = str(item.get('snippet') or '')
            combined = f'{title} {snippet}'
            local_context = result_relevant_to_company(company, title, snippet)
            event_type = str(item.get('event_type') or 'other')
            if event_type == 'job_ad' and not company_mentioned(company, combined):
                continue
            inferred_type, inferred_phase = infer_event(local_context)
            if inferred_type not in {'other', 'job_ad'}:
                event_type = inferred_type
                phase = inferred_phase
            elif event_type != 'job_ad':
                continue
            else:
                phase = str(item.get('phase') or 'build_organize')
            if event_type == 'job_ad':
                retained_ads += 1
            source_url = str(item.get('source_url') or '')
            key = (company, source_url)
            if not source_url or key in seen:
                continue
            seen.add(key)
            event_date = _event_date_from_text(local_context, str(item.get('event_date') or ''))
            if event_date and event_date < f'{date.today().year - 1}-01-01':
                continue
            evidence.append(Evidence(
                company=company,
                event_type=event_type,
                phase=phase,
                event_date=event_date,
                title=title,
                snippet=snippet,
                source_url=source_url,
                source_name=str(item.get('source_name') or 'replayed live search'),
                source_grade=grade_source(source_url),
                direction=direction,
                people=tuple(item.get('people') or ()),
                organizations=tuple(item.get('organizations') or ()),
            ))
            company_evidence_texts.append((local_context, source_url))
        evidence_corpus = ' '.join(text for text, _ in company_evidence_texts)
        routes: list[dict] = []
        route_keys: set[tuple[str, str]] = set()
        for item in lead.get('outreach_routes', []):
            normalized = normalize_replayed_route(item)
            if normalized is None:
                continue
            if normalized['kind'] == 'investor' and not investor_supported(normalized['target'], evidence_corpus):
                continue
            key = (normalized['kind'], normalized['target'])
            if key not in route_keys:
                route_keys.add(key)
                routes.append(normalized)
        for text, source_url in company_evidence_texts:
            for route in infer_routes_from_text(text, source_url, company=company):
                key = (route.kind, route.target)
                if key not in route_keys:
                    route_keys.add(key)
                    routes.append(asdict(route))
        metadata['routes'][company] = routes
        metadata['ad_checks'][company] = {
            'checked_at': date.today().isoformat(),
            'queries': ['replayed from prior live run'],
            'matching_results': retained_ads,
        }
    return evidence, metadata


def normalize_replayed_route(route: dict) -> dict | None:
    normalized = dict(route)
    kind = str(normalized.get('kind') or '')
    target = re.sub(r'\s+', ' ', str(normalized.get('target') or '')).strip()
    if kind == 'investor':
        target = re.sub(r'(?:独家|共同)$', '', target).strip(' ，,')
        if not 2 <= len(target) <= 70:
            return None
        normalized['path'] = f'请 Michael Page 内部或共同 LP/被投企业联系人请求 {target} 引荐公司创始团队。'
    elif kind in {'alumni_or_academic', 'former_colleague'}:
        if '—' not in target:
            return None
        person, organization = target.split('—', 1)
        person = re.sub(r'^(?:是|为)', '', person)
        person = re.sub(r'(?:博士|教授)$', '', person).strip()
        known_people = ('倪华良', '许晋诚', '左家平', '师云雷')
        person = next((name for name in known_people if name in person), person)
        if (
            not re.fullmatch(r'[\u4e00-\u9fff·]{2,4}', person)
            or person.startswith(('前', '某', '在', '为', '是'))
            or person.endswith(('工', '系', '者'))
        ):
            return None
        organization = re.split(
            r'(?:担任|任职|参与|公开资料|自成立以来|主任工程师|技术负责人|_腾讯|……|\.\.\.)',
            organization,
            maxsplit=1,
        )[0].strip(' ，,。；;')
        invalid_organizations = ('人工智能领域世界顶级', '具身智能行业', '国内品牌第一')
        if not 2 <= len(organization) <= 40 or any(value in organization for value in invalid_organizations):
            return None
        target = f'{person}—{organization}'
        if kind == 'alumni_or_academic':
            normalized['path'] = f'优先查找 Michael Page、客户或候选人网络中的 {organization} 校友/师门关系，请求暖介绍 {person}。'
        else:
            normalized['path'] = f'在经许可的 Michael Page 关系网络中查找 {organization} 前同事，请求对 {person} 的暖介绍。'
    else:
        return None
    invalid = ('之一—', '被传', '兼董事', '毕业于—', '具身智能行业', '国内品牌第一', '基因的充分')
    if any(fragment in target for fragment in invalid):
        return None
    normalized['target'] = target
    return normalized


def route_is_plausible(route: dict) -> bool:
    return normalize_replayed_route(route) is not None


def investor_supported(target: str, evidence_text: str) -> bool:
    components = re.split(r'[、,，与及]|(?:联合|头部银行系|银行系机构)', target)
    return any(component.strip() in evidence_text for component in components if len(component.strip()) >= 2)


def collect_josint(db_path: str | Path, direction: str) -> list[Evidence]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    terms = profile_for(direction).aliases
    from .josint_adapter import read_canonical_evidence
    canonical_rows = read_canonical_evidence(path, terms=terms, direction=direction)
    if canonical_rows is not None:
        return [Evidence(**row) for row in canonical_rows]

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(jobs)').fetchall()}

        def selected(alias: str, candidates: tuple[str, ...]) -> str:
            found = next((name for name in candidates if name in columns), None)
            return f'{found} AS {alias}' if found else f'NULL AS {alias}'

        fields = [
            selected('source_name', ('source_name', 'source_slug')),
            selected('title', ('title', 'raw_title')),
            selected('canonical_url', ('canonical_url',)),
            selected('url', ('url',)),
            selected('jd_text', ('jd_text', 'detail_text', 'raw_json')),
            selected('company_description', ('company_description', 'list_excerpt')),
            selected('published_at', ('published_at', 'published')),
            selected('first_seen_at', ('first_seen_at', 'first_seen')),
        ]
        field_sql = ', '.join(fields)
        rows = connection.execute(f'SELECT {field_sql} FROM jobs').fetchall()
    finally:
        connection.close()
    output: list[Evidence] = []
    for row in rows:
        text = ' '.join(str(row[key] or '') for key in row.keys())
        if not any(term.lower() in text.lower() for term in terms):
            continue
        _, eligible, _ = classify_seniority(str(row['title'] or ''), str(row['jd_text'] or ''))
        if not eligible:
            continue
        company = _company_hint(str(row['company_description'] or ''))
        if not company:
            continue
        output.append(Evidence(
            company=company,
            event_type='job_ad',
            phase='marketed_competitive',
            event_date=str(row['published_at'] or row['first_seen_at'] or ''),
            title=str(row['title'] or ''),
            snippet=str(row['jd_text'] or '')[:500],
            source_url=str(row['canonical_url'] or row['url'] or ''),
            source_name=str(row['source_name'] or 'JOSINT'),
            source_grade='C',
            direction=direction,
        ))
    return output


def _company_hint(description: str) -> str | None:
    match = re.search(r'([\u4e00-\u9fffA-Za-z0-9·（）()]{2,30}(?:公司|集团|机器人|科技))', description)
    return match.group(1) if match else None


def infer_routes_from_text(text: str, source_url: str, company: str = '') -> list[OutreachRoute]:
    compact = re.sub(r'\s+', ' ', text)
    routes: list[OutreachRoute] = []
    investment = re.search(r'(?:由|获)([^，,。；]{2,70}?)(?:领投|联合投资|投资)', compact)
    equity_target = None
    if not investment and company:
        equity_target = re.search(
            rf'([^，,。；]{{2,40}}?)(?:等)?(?:入股|参股){re.escape(company)}',
            compact,
        )
    if investment:
        target = re.sub(r'本轮|近日|完成.*?轮融资', '', investment.group(1)).strip('，,：: ')
        target = re.sub(r'^得', '', target)
        target = re.sub(r'(?:\d+(?:\.\d+)?|数)?亿元$', '', target).strip()
        target = re.sub(r'(?:独家|共同)$', '', target).strip()
        invalid_investor_text = ('订单', '消息', '融资', '完成')
        if (
            2 <= len(target) <= 70
            and not any(value in target for value in invalid_investor_text)
            and not (company and company in target)
        ):
            routes.append(OutreachRoute(
                kind='investor',
                target=target,
                path=f'请 Michael Page 内部或共同 LP/被投企业联系人请求 {target} 引荐公司创始团队。',
                evidence_url=source_url,
                grade='C',
                note='搜索摘要中的投资关系候选，触达前需打开原文复核。',
            ))
    elif equity_target:
        target = equity_target.group(1).strip('，,：: ')
        target = re.sub(r'^(?:最新新闻|快讯|消息)', '', target).strip()
        if 2 <= len(target) <= 40 and company not in target:
            routes.append(OutreachRoute(
                kind='investor',
                target=target,
                path=f'请 Michael Page 内部或共同被投企业联系人请求 {target} 引荐公司创始团队。',
                evidence_url=source_url,
                grade='C',
                note='搜索摘要中的入股关系候选，触达前需打开原文复核。',
            ))
    founder = re.search(
        r'(?:创始人|联合创始人)(?:之一)?(?:兼(?:CEO|CTO|董事长|总经理|首席科学家))?(?:是|为)?[：:\s]*([\u4e00-\u9fff·]{2,4})(?=博士|教授|，|。|；|曾|毕业|就读|师从|\s|$)(?:博士|教授)?',
        compact,
        flags=re.I,
    )
    invalid_people = {'之一', '团队', '公司', '被传', '离职', '毕业于', '兼董事'}
    if founder and founder.group(1) not in invalid_people:
        person = founder.group(1)
        context = compact[founder.start():founder.start() + 180]
        school_match = re.search(r'(?:毕业于|就读于|师从)([^，。；]{2,30})', context)
        if school_match:
            school = school_match.group(1).strip()
            routes.append(OutreachRoute(
                kind='alumni_or_academic',
                target=f'{person}—{school}',
                path=f'优先查找 Michael Page、客户或候选人网络中的 {school} 校友/师门关系，请求暖介绍 {person}。',
                evidence_url=source_url,
                grade='C',
                note='公开教育或师门线索候选，不等于存在私人关系。',
            ))
        former_match = re.search(r'(?:曾任职于|曾就职于|曾在|来自)([^，。；]{2,30}?)(?:担任|任职|，|。|；|$)', context)
        if former_match:
            organization = former_match.group(1).strip()
            routes.append(OutreachRoute(
                kind='former_colleague',
                target=f'{person}—{organization}',
                path=f'在经许可的 Michael Page 关系网络中查找 {organization} 前同事，请求对 {person} 的暖介绍。',
                evidence_url=source_url,
                grade='C',
                note='公开履历线索候选，禁止推断或抓取私人联系方式。',
            ))
    return routes
