from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from .collectors import (
    BingRSSCollector,
    MetasoCollector,
    SearXNGCollector,
    company_mentioned,
    collect_josint,
    infer_routes_from_text,
    load_demo_fixture,
    load_env_file,
    load_replay,
)
from .fixed_sources import FixedSourceCollector
from .models import Evidence
from .pipeline import build_late_opportunities, build_leads
from .reporting import render_markdown, write_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='ht-lead-radar',
        description='从公开上游信号识别总监级以上硬科技招聘机会。',
    )
    parser.add_argument('command', choices=['run'])
    parser.add_argument('--direction', required=True, help='企业/技术方向，例如：灵巧手')
    parser.add_argument('--demo', action='store_true', help='使用带真实来源的确定性验收夹具')
    parser.add_argument('--replay-json', help='重放此前实时 JSON，并应用当前过滤和评分规则')
    parser.add_argument('--provider', choices=['auto', 'fixed', 'metaso', 'searxng', 'bing'], default='auto')
    parser.add_argument('--fixed-sources', default='config/fixed-sources.json')
    parser.add_argument('--source-state-db', default='data/fixed-sources.sqlite')
    parser.add_argument('--metaso-verify-limit', type=int, default=3)
    parser.add_argument('--metaso-daily-point-budget', type=int, default=30)
    parser.add_argument('--env-file', help='可选 .env；用于复用现有 METASO_API_KEY，不会输出密钥')
    parser.add_argument('--josint-db', help='可选：现有 JOSINT jobs.sqlite 路径，仅用于职位晚期验证')
    parser.add_argument('--output-dir', default='reports', help='报告输出目录')
    parser.add_argument('--minimum-score', type=float, default=0.0)
    parser.add_argument('--top', type=int, default=20, help='主队列最多返回公司数，默认 20')
    parser.add_argument('--limit-per-query', type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.demo and args.replay_json:
            raise ValueError('--demo 与 --replay-json 不能同时使用')
        if args.replay_json:
            evidence, metadata = load_replay(args.replay_json, args.direction)
            as_of = date.today()
            mode = 'replay-audited-live-evidence'
        elif args.demo:
            evidence, metadata = load_demo_fixture(args.direction)
            as_of = date.fromisoformat(metadata['as_of'])
            mode = 'demo-real-evidence'
        else:
            env = load_env_file(args.env_file)
            as_of = date.today()
            candidates: list[tuple[str, object]] = []
            if args.provider in {'auto', 'fixed'}:
                candidates.append(('fixed-sources', FixedSourceCollector(
                    registry_path=args.fixed_sources,
                    state_db=args.source_state_db,
                )))
            if args.provider == 'metaso' and env.get('METASO_API_KEY'):
                candidates.append(('metaso', MetasoCollector(
                    api_key=env['METASO_API_KEY'],
                    base_url=env.get('METASO_BASE_URL', 'https://metaso.cn'),
                )))
            if args.provider in {'auto', 'searxng'}:
                candidates.append(('searxng', SearXNGCollector(
                    base_url=env.get('SEARXNG_URL', 'http://localhost:8080'),
                )))
            if args.provider in {'auto', 'bing'}:
                candidates.append(('bing-rss', BingRSSCollector()))
            if not candidates:
                raise ValueError(f'{args.provider} provider configuration is unavailable')
            evidence = []
            failures: list[str] = []
            collector = candidates[-1][1]
            provider_mode = candidates[-1][0]
            for candidate_mode, candidate in candidates:
                try:
                    candidate_evidence = candidate.collect(
                        args.direction, year=as_of.year, limit_per_query=args.limit_per_query,
                    )
                except Exception as exc:
                    failures.append(f'{candidate_mode}: {exc}')
                    if args.provider != 'auto':
                        raise
                    continue
                collector = candidate
                provider_mode = candidate_mode
                evidence = candidate_evidence
                if evidence or args.provider != 'auto':
                    break
            if not evidence and failures and args.provider == 'auto':
                print('搜索源降级：' + ' | '.join(failures), file=sys.stderr)
            metadata = {'routes': {}, 'ad_checks': {}}
            preliminary = build_leads(
                args.direction, evidence, metadata, as_of=as_of,
                minimum_score=args.minimum_score, limit=args.top,
            )
            for lead in preliminary[:args.top]:
                if getattr(collector, 'supports_search', True):
                    metadata['routes'][lead.company] = [
                        route.__dict__ for route in collector.collect_routes(lead.company, args.direction)
                    ]
                    roles_query = ' '.join(lead.target_roles)
                    role_query = f'{lead.company} {roles_query} 招聘'
                    job_results = collector.search(role_query, limit=args.limit_per_query)
                    matches = [
                        result for result in job_results
                        if company_mentioned(lead.company, f'{result.title} {result.snippet}')
                        and any(role in f'{result.title} {result.snippet}' for role in lead.target_roles)
                    ]
                    metadata['ad_checks'][lead.company] = {
                        'checked_at': as_of.isoformat(),
                        'queries': [role_query],
                        'matching_results': len(matches),
                    }
                    for result in matches:
                        evidence.append(Evidence(
                            company=lead.company,
                            event_type='job_ad',
                            phase='recruit',
                            event_date=result.published_at,
                            title=result.title,
                            snippet=result.snippet,
                            source_url=result.url,
                            source_name=collector.provider_name,
                            source_grade='C',
                            direction=args.direction,
                        ))
                else:
                    route_map = {}
                    for item in lead.evidence:
                        for route in infer_routes_from_text(
                            f'{item.title} {item.snippet}', item.source_url, company=lead.company,
                        ):
                            route_map[(route.kind, route.target)] = route.__dict__
                    metadata['routes'][lead.company] = list(route_map.values())
            verification_limit = min(
                max(args.metaso_verify_limit, 0),
                max(args.metaso_daily_point_budget, 0) // 6,
            )
            if env.get('METASO_API_KEY') and verification_limit and provider_mode != 'metaso':
                verifier = MetasoCollector(
                    api_key=env['METASO_API_KEY'],
                    base_url=env.get('METASO_BASE_URL', 'https://metaso.cn'),
                )
                metadata['verification'] = {}
                for lead in preliminary[:verification_limit]:
                    query = f'{lead.company} 融资 扩产 量产 订单 战略合作 {as_of.year}'
                    try:
                        results = verifier.search(query, limit=5)
                        matched = [result for result in results if company_mentioned(
                            lead.company, f'{result.title} {result.snippet}',
                        )]
                        metadata['verification'][lead.company] = {
                            'provider': 'metaso', 'query_count': 1,
                            'matching_results': len(matched), 'status': 'ok',
                        }
                    except Exception as exc:
                        metadata['verification'][lead.company] = {
                            'provider': 'metaso', 'query_count': 1,
                            'matching_results': 0, 'status': f'error: {exc}',
                        }
            mode = f'live-public-search-{provider_mode}'

        if args.josint_db:
            evidence.extend(collect_josint(args.josint_db, args.direction))
            mode += '+josint'

        leads = build_leads(
            direction=args.direction,
            evidence=evidence,
            metadata=metadata,
            as_of=as_of,
            minimum_score=args.minimum_score,
            limit=args.top,
        )
        late_opportunities = build_late_opportunities(args.direction, evidence)
        slug = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff-]+', '-', args.direction).strip('-') or 'direction'
        stem = f'lead-radar-{slug}-{as_of.isoformat()}'
        markdown = render_markdown(
            args.direction, leads, as_of.isoformat(), mode,
            late_opportunities=late_opportunities,
        )
        md_path, json_path = write_outputs(Path(args.output_dir), stem, markdown, leads)
        print(f'强信号企业：{len(leads)} 家')
        for lead in leads:
            roles_text = '、'.join(lead.target_roles)
            print(f'- {lead.company}: {lead.score:.1f} | {roles_text}')
        print(f'Markdown: {md_path.resolve()}')
        print(f'JSON: {json_path.resolve()}')
        return 0 if leads else 2
    except Exception as exc:
        print(f'运行失败：{exc}', file=sys.stderr)
        return 1
