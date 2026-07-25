from __future__ import annotations

import json
from pathlib import Path

from .models import CompanyLead


def render_markdown(
    direction: str,
    leads: list[CompanyLead],
    as_of: str,
    mode: str,
    *,
    late_opportunities: list[dict] | None = None,
    request_summary: dict | None = None,
    industry_map: dict | None = None,
) -> str:
    lines = [
        f'# {direction}：总监级以上提前招聘信号 Top 20',
        '',
        f'- 生成日期：{as_of}',
        f'- 运行模式：{mode}',
        f'- 主队列企业：{len(leads)} 家（按分数从高到低，不为凑数虚构公司）',
        f'- 强信号企业：{len(leads)} 家',
        '- 硬门槛：必须形成总监级以上岗位假设，并至少有一条招聘广告之前的上游信号。',
        '- 说明：分数用于排序并交给人类判断，不是业务结果评价系统。',
        '',
    ]
    if request_summary:
        lines.extend(['## 请求解释', '', '```json', json.dumps(request_summary, ensure_ascii=False, indent=2), '```', ''])
    if industry_map:
        lines.extend(['## 行业地图', ''])
        for key, label in (
            ('core', '核心层'), ('direct_upstream', '直接上游'),
            ('direct_downstream', '直接下游'), ('adjacent', '相邻层（Watchlist）'),
        ):
            values = industry_map.get(key) or []
            lines.append(f'- **{label}：** {"、".join(values) if values else "未识别"}')
        lines.append('')

    if not leads:
        lines.extend(['没有公司同时通过两个硬门槛。系统不会用只有招聘广告或无法形成总监级假设的公司凑数。', ''])
    else:
        lines.extend([
            '| 排名 | 企业 | 分数 | 置信度 | 时机 | 预测总监级岗位 |',
            '|---:|---|---:|---|---|---|',
        ])
        for rank, lead in enumerate(leads, 1):
            roles_text = '、'.join(lead.target_roles)
            lines.append(
                f'| {rank} | {lead.company} | {lead.score:.1f} | {lead.confidence_grade} | '
                f'{lead.timing_stage} | {roles_text} |'
            )

    for rank, lead in enumerate(leads, 1):
        roles_text = '、'.join(lead.target_roles)
        lines.extend([
            '', f'## {rank}. {lead.company}', '',
            f'**结论：{lead.score:.1f} 分 / {lead.confidence_grade}；{lead.timing_stage}。**', '',
            f'**为什么可能招总监以上：** {lead.hiring_thesis}', '',
            f'**建议岗位：** {roles_text}', '',
            f'**行业层级：** {lead.industry_layer}；**地域：** {lead.mainland_relevance}', '',
            '### 得分来源', '',
        ])
        for component in lead.score_components:
            sign = '+' if component.points >= 0 else ''
            lines.append(f'- **{component.label}：{sign}{component.points:.1f}** — {component.reason}')
            if component.evidence_urls:
                lines.append('  - 依据：' + '、'.join(f'[来源{i + 1}]({url})' for i, url in enumerate(component.evidence_urls)))
        lines.extend(['', '### 证据', ''])
        for item in lead.evidence:
            event_date = item.event_date or '日期未知'
            event_ref = f'；Event {item.event_id}' if item.event_id else ''
            lines.append(
                f'- **{event_date}｜{item.event_type}｜{item.source_grade}级{event_ref}**：'
                f'[{item.title}]({item.source_url})'
            )
            lines.append(f'  - {item.snippet}')

        lines.extend(['', '### 每日基础研究', ''])
        research = lead.basic_research or {}
        institutions = research.get('external_investors_or_institutions') or []
        people = research.get('public_people_in_evidence') or []
        internal_roles = research.get('internal_roles_to_research') or []
        lines.append(f'- 外部投资机构/投资人线索：{"、".join(institutions) if institutions else "固定证据中尚未出现，深研时补充"}')
        lines.append(f'- 公开人物线索：{"、".join(people) if people else "固定证据中尚未出现"}')
        lines.append(f'- 企业内部应研究角色：{"、".join(internal_roles)}')

        lines.extend(['', '### 公开关系线索', ''])
        if lead.outreach_routes:
            for route in lead.outreach_routes:
                lines.append(
                    f'- **{route.kind}｜{route.target}｜{route.grade}级**：{route.note}'
                )
                lines.append(f'  - 来源：[{route.evidence_url}]({route.evidence_url})')
        else:
            lines.append('- 基础研究未发现可复核的公开关系线索；主动深研或 Float 时可继续研究投资人、Hiring Manager、HR 和创始团队。')
        if lead.risk_notes:
            lines.extend(['', '### 风险与复核', ''])
            lines.extend(f'- {note}' for note in lead.risk_notes)

    late = late_opportunities or []
    lines.extend(['', '## 晚期机会附录', ''])
    if late:
        lines.append('以下企业只有公开招聘广告，没有招聘前上游信号，因此不参与主 Top 20：')
        lines.append('')
        for item in late:
            links = '、'.join(f'[广告{i + 1}]({url})' for i, url in enumerate(item.get('ads', [])))
            lines.append(f'- **{item["company"]}**：{item["reason"]} {links}')
    else:
        lines.append('- 本次没有需要单列的招聘广告-only公司。')

    lines.extend([
        '', '## 使用边界', '',
        '- 所有公司判断、岗位和人物关系均应区分事实与推断，并保留公开来源。',
        '- 投资评论只能支持“疑似主导投资人”推断，不能自动变成事实关系。',
        '- 系统不生成触达话术、不发送消息、不抓取私人联系方式。',
        '- 飞书是行动队列投影，后端事实库才是唯一事实源。',
    ])
    return '\n'.join(lines) + '\n'


def write_outputs(output_dir: Path, stem: str, markdown: str, leads: list[CompanyLead]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f'{stem}.md'
    json_path = output_dir / f'{stem}.json'
    markdown_path.write_text(markdown, encoding='utf-8')
    json_path.write_text(
        json.dumps([lead.to_dict() for lead in leads], ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return markdown_path, json_path
