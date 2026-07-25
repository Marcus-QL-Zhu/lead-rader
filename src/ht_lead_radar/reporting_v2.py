"""Complete report envelope for Market Scan, Float and deep research."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .float_matching import FloatMatch
from .models import CompanyLead
from .reporting import render_markdown


def render_complete_markdown(
    direction: str,
    leads: list[CompanyLead],
    as_of: str,
    mode: str,
    *,
    late_opportunities: list[dict] | None = None,
    request_plan: Mapping[str, Any] | None = None,
    float_matches: Iterable[FloatMatch | Mapping[str, Any]] = (),
    deep_research: Mapping[str, Mapping[str, Any]] | None = None,
    source_summary: Mapping[str, Any] | None = None,
    integration_status: Mapping[str, Any] | None = None,
) -> str:
    industry_map = (request_plan or {}).get("industry_map")
    body = render_markdown(
        direction,
        leads,
        as_of,
        mode,
        late_opportunities=late_opportunities,
        request_summary=dict(request_plan or {}),
        industry_map=industry_map if isinstance(industry_map, dict) else None,
    ).rstrip()
    sections = [body]

    float_items = [
        item.to_dict() if isinstance(item, FloatMatch) else dict(item)
        for item in float_matches
    ]
    if float_items:
        lines = [
            "# Candidate Float 分析",
            "",
            "Float 分数只用于本次候选人与公司机会的相对排序；候选人画像不写入后端、飞书或投资图谱。",
            "",
            "| 排名 | 公司 | Float分 | 公司需求 | 候选人匹配 | 时机 | 公开关系可研究性 |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
        for item in float_items:
            components = {
                component["key"]: component
                for component in item.get("score_components", ())
            }
            lines.append(
                f'| {item.get("rank", "")} | {item.get("company", "")} | '
                f'{float(item.get("float_score", 0)):.1f} | '
                f'{_component_points(components, "company_need")} | '
                f'{_component_points(components, "candidate_match")} | '
                f'{_component_points(components, "timing")} | '
                f'{_component_points(components, "public_relationship_researchability")} |'
            )
        for item in float_items:
            lines.extend([
                "",
                f'## Float {item.get("rank", "")}. {item.get("company", "")}',
                "",
                f'- Float 分：{float(item.get("float_score", 0)):.1f}；'
                f'Market Scan 分：{float(item.get("market_scan_score", 0)):.1f}',
                "- 匹配原因：" + _join(item.get("match_reasons")),
                "- 候选人卖点：" + _join(item.get("candidate_selling_points")),
                "- 风险/冲突待核：" + _join(item.get("risks_or_conflicts_to_verify")),
                "- 候选人缺失信息：" + _join(item.get("missing_information")),
                "- 会改变排名的新证据：" + _join(
                    item.get("evidence_that_would_change_ranking")
                ),
                "- 深度研究：已要求；不生成触达话术、不发送。",
            ])
        sections.append("\n".join(lines))

    if deep_research:
        lines = [
            "# 深度研究：外部投资人和企业内部决策者",
            "",
            "人物与关系只来自公开职业信息；标记为 inferred 的记录是证据支持的推断，不是确定事实。",
        ]
        for company, report in deep_research.items():
            lines.extend(["", f"## {company}", ""])
            _append_institutions(lines, report.get("institutions") or ())
            _append_people(lines, "疑似主导/相关投资人", report.get("investors") or ())
            _append_people(lines, "业务 Hiring Manager", report.get("hiring_managers") or ())
            _append_people(lines, "HR / TA / HRBP", report.get("hr_people") or ())
            _append_people(lines, "创始团队", report.get("founders") or ())
            caveats = report.get("caveats") or ()
            lines.append("- 研究限制：" + _join(caveats))
        sections.append("\n".join(lines))

    if source_summary or integration_status:
        lines = ["# 运行与集成状态", ""]
        if source_summary:
            lines.extend([
                "## 信源",
                "",
                "```json",
                json.dumps(source_summary, ensure_ascii=False, indent=2),
                "```",
            ])
        if integration_status:
            lines.extend([
                "",
                "## 外部集成",
                "",
                "```json",
                json.dumps(integration_status, ensure_ascii=False, indent=2),
                "```",
            ])
        sections.append("\n".join(lines))

    return "\n\n".join(sections).rstrip() + "\n"


def write_complete_outputs(
    output_dir: str | Path,
    stem: str,
    markdown: str,
    *,
    leads: Iterable[CompanyLead],
    manifest: Mapping[str, Any],
    late_opportunities: Iterable[Mapping[str, Any]] = (),
    float_matches: Iterable[FloatMatch | Mapping[str, Any]] = (),
    deep_research: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    markdown_path = target / f"{stem}.md"
    json_path = target / f"{stem}.json"
    markdown_path.write_text(markdown, encoding="utf-8")
    float_payload = [
        item.to_dict() if isinstance(item, FloatMatch) else dict(item)
        for item in float_matches
    ]
    envelope = {
        "schema_version": 2,
        "manifest": dict(manifest),
        "leads": [lead.to_dict() for lead in leads],
        "late_opportunities": [dict(item) for item in late_opportunities],
        "float_matches": float_payload,
        "deep_research": dict(deep_research or {}),
    }
    json_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return markdown_path, json_path


def _component_points(components: Mapping[str, Mapping[str, Any]], key: str) -> str:
    item = components.get(key) or {}
    return f'{float(item.get("points", 0)):.1f}/{float(item.get("max_points", 0)):.0f}'


def _join(values: Iterable[Any] | None) -> str:
    items = [str(item) for item in (values or ()) if str(item).strip()]
    return "；".join(items) if items else "未找到可复核信息"


def _append_institutions(lines: list[str], institutions: Iterable[Mapping[str, Any]]) -> None:
    items = list(institutions)
    lines.append("- 投资机构：")
    if not items:
        lines.append("  - 未找到具体机构；不猜测。")
        return
    for item in items:
        role = "领投" if item.get("role") == "lead" else "参投或相关"
        lines.append(
            f'  - {item.get("name", "未知机构")}（{role}，'
            f'置信度 {float(item.get("confidence", 0)):.2f}）：'
            f'[{item.get("evidence_url", "来源")}]({item.get("evidence_url", "")})'
        )


def _append_people(
    lines: list[str],
    label: str,
    people: Iterable[Mapping[str, Any]],
) -> None:
    items = list(people)
    lines.append(f"- {label}：")
    if not items:
        lines.append("  - 未找到具体姓名；保留角色缺口，不猜人名。")
        return
    for item in items:
        inference = "推断" if item.get("inferred") else "公开事实"
        url = item.get("evidence_url", "")
        lines.append(
            f'  - {item.get("name", "未知")}｜{item.get("title", "职位未知")}｜'
            f'{item.get("organization", "机构未知")}｜{inference}｜'
            f'置信度 {float(item.get("confidence", 0)):.2f}：[{url}]({url})'
        )
        evidence_text = " ".join(
            str(item.get("evidence_text", "")).split()
        )
        if evidence_text:
            lines.append(f"    - 公开履历/语境：{evidence_text[:300]}")


__all__ = ["render_complete_markdown", "write_complete_outputs"]
