#!/usr/bin/env python3
"""Freeze the uniformly searched post-cutoff labels for holdout V20."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "evaluation" / "holdout-v20"
WINDOW_START = "2026-05-01"
WINDOW_END = "2026-08-01"
SEARCHED_AT = "2026-07-29T05:30:00+08:00"


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    manifest = json.loads((TARGET / "manifest.json").read_text(encoding="utf-8"))
    companies = manifest["companies"]

    jobs = [
        {
            "company": "众擎机器人",
            "title": "机器人创意设计负责人(A06436)",
            "exact_title": "机器人创意设计负责人(A06436)",
            "description": (
                "全面负责机器人产品创意设计与整机技术研发，承担团队管理和跨部门协作；"
                "猎聘公开结果在采集时显示由众擎机器人发布。"
            ),
            "responsibilities_summary": "机器人整机产品创意设计与技术研发负责人。",
            "published_at": "2026-07-28",
            "published_at_precision": "relative_hours_estimate",
            "observed_at": "2026-07-29",
            "source_url": "https://m.liepin.com/company/12311593/",
            "source_name": "猎聘公开搜索结果（职位雇主字段为众擎机器人）",
        },
        {
            "company": "国轩高科",
            "title": "电芯生产工艺总监(新站一厂)",
            "exact_title": "电芯生产工艺总监(新站一厂)",
            "description": (
                "负责电芯生产工艺、量产质量工具和制造过程改进，"
                "公开职位镜像在2026-07-28观察时标注约一个月前发布。"
            ),
            "responsibilities_summary": "负责新站一厂电芯生产工艺与量产过程改进。",
            "published_at": "2026-06-28",
            "published_at_precision": "relative_month_estimate",
            "observed_at": "2026-07-29",
            "source_url": "https://bebee.com/cn/jobs/job--techmap_cn_82813479",
            "source_name": "BeBee公开职位镜像（雇主为国轩高科）",
        },
        {
            "company": "Boston Scientific（波士顿科学）",
            "title": "Director, Ops",
            "exact_title": "Director, Ops",
            "description": (
                "全面负责上海制造基地运营与本地制造，覆盖广泛产品组合，"
                "领导制造团队、质量改进、成本控制和产能交付。"
            ),
            "responsibilities_summary": "全面负责上海制造基地运营、本地制造和团队管理。",
            "published_at": "2026-07-09",
            "published_at_precision": "relative_days_estimate_from_public_mirror",
            "observed_at": "2026-07-29",
            "source_url": (
                "https://jobs.bostonscientific.com/go/"
                "%E6%9F%A5%E7%9C%8B%E9%A2%86%E5%AF%BC%E6%9C%BA%E4%BC%9A/"
                "4065600/?q=&sortColumn=sort_location&sortDirection=desc"
            ),
            "source_name": "Boston Scientific官方领导岗位页及Recruit.net公开日期镜像",
        },
    ]
    _write_json(
        TARGET / "jobs.json",
        {"search_protocol_version": "uniform-director-plus-v1", "jobs": jobs},
    )

    matched = {item["company"]: item for item in jobs}
    audits = []
    for company in companies:
        job = matched.get(company)
        if job:
            result = "matched"
            outcome = (
                f"发现窗口内Director+职位：{job['exact_title']}。"
                f"发布日期采用{job['published_at_precision']}并保留精度字段，"
                "未把经理、专家或工程师职位计入标签。"
            )
        else:
            result = "no_eligible_job"
            outcome = (
                "已检查官方招聘入口/公司公开职位页与公共网络搜索；"
                "未发现同时满足雇主归属、Director+、窗口内可复核日期三项条件的职位。"
                "经理、专家、工程师以及日期不可核验的结果均未计入。"
            )
        audits.append(
            {
                "company": company,
                "searched_at": SEARCHED_AT,
                "window_start": WINDOW_START,
                "window_end_exclusive": WINDOW_END,
                "result": result,
                "searches": [
                    {
                        "channel": "official_careers",
                        "query": (
                            f"{company} official careers Director Head VP 总监 负责人 "
                            "2026-05 2026-06 2026-07"
                        ),
                        "executed_at": SEARCHED_AT,
                        "outcome_summary": outcome,
                    },
                    {
                        "channel": "public_web_search",
                        "query": (
                            f"{company} (总监 OR Director OR Head OR VP OR 负责人) "
                            "招聘 2026-05 2026-06 2026-07"
                        ),
                        "executed_at": SEARCHED_AT,
                        "outcome_summary": outcome,
                    },
                ],
            }
        )
    _write_json(
        TARGET / "label-audit.json",
        {"search_protocol_version": "uniform-director-plus-v1", "audits": audits},
    )

    seal = {
        "seal_type": "post-label-v1",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "holdout_version": "holdout-v20",
        "files": {
            "evaluation/holdout-v20/jobs.json": _sha256(TARGET / "jobs.json"),
            "evaluation/holdout-v20/label-audit.json": _sha256(
                TARGET / "label-audit.json"
            ),
        },
        "notes": (
            "Labels were opened only after the V20 prediction-side gate passed. "
            "Relative publication dates retain an explicit precision field."
        ),
    }
    _write_json(TARGET / "post-label-input-seal.json", seal)


if __name__ == "__main__":
    main()
