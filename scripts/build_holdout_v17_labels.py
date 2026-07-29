#!/usr/bin/env python3
"""Freeze the post-prediction V17 Director+ label search."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "evaluation" / "holdout-v17"
WINDOW_START = "2026-05-01"
WINDOW_END = "2026-08-01"
EXECUTED_AT = datetime(
    2026, 7, 29, 2, 15, tzinfo=timezone(timedelta(hours=8))
).isoformat()


JOBS = [
    {
        "company": "北方华创",
        "title": "仓储总监(J16075)",
        "exact_title": "仓储总监(J16075)",
        "description": (
            "北方华创公开职位。职位标题明确为仓储总监，属于供应链/仓储职能。"
            "智联职位页内嵌结构化数据给出发布时间2026-07-28 01:42:31。"
        ),
        "responsibilities_summary": (
            "负责仓储职能；公开职位标题直接标明总监层级。"
        ),
        "published_at": "2026-07-28",
        "observed_at": "2026-07-29",
        "source_url": (
            "https://www.zhaopin.com/jobdetail/"
            "CC135196350J40920116812.htm"
        ),
        "source_name": "智联招聘（北方华创认证公司职位页）",
    },
    {
        "company": "佛吉亚（中国）",
        "title": "财务总监",
        "exact_title": "财务总监",
        "description": (
            "佛吉亚智永科技（重庆）有限公司公开职位，职位标题为财务总监。"
            "智联职位页内嵌结构化数据给出发布时间2026-07-22 09:26:03。"
            "该实体为佛吉亚相关合资经营实体；此标签用于公司层真实高阶招聘审计，"
            "不预设其与预测职能匹配。"
        ),
        "responsibilities_summary": "负责财务职能并承担总监层级责任。",
        "published_at": "2026-07-22",
        "observed_at": "2026-07-29",
        "source_url": (
            "https://www.zhaopin.com/jobdetail/"
            "CCL1502393540J40862298309.htm"
        ),
        "source_name": "智联招聘（佛吉亚智永科技认证职位页）",
    },
    {
        "company": "罗氏制药（中国）",
        "title": "Head of Manufacturing",
        "exact_title": "Head of Manufacturing",
        "description": (
            "Roche Shanghai site leadership role. Accountable for safe, "
            "cost-effective and efficient manufacturing operations; leads, "
            "coaches and develops the manufacturing team, owns the production "
            "budget, GMP/SHE compliance, production strategy and delivery. "
            "Roche requisition identifier ROCHGLOBAL202605111730 and the "
            "official URL freeze the posting to May 2026; LinkedIn independently "
            "showed the same role in Shanghai within the validation window."
        ),
        "responsibilities_summary": (
            "Owns Shanghai manufacturing operations, people leadership, "
            "production budget, compliance and delivery."
        ),
        "published_at": "2026-05-01",
        "published_at_precision": "month",
        "observed_at": "2026-07-29",
        "source_url": (
            "https://careers.roche.com/global/en/job/"
            "ROCHGLOBAL202605111730EXTERNALENGLOBAL/Head-of-Manufacturing"
        ),
        "source_name": (
            "Roche Careers official requisition; LinkedIn mirror "
            "4359975761 used for full responsibilities"
        ),
    },
]


COMPANY_SUMMARIES = {
    "东京电子（中国）": (
        "TEL官方全球职业入口及中国入口可访问；未检出验证窗口内带可核日期的"
        "中国区Director+/功能Head职位。"
    ),
    "佛吉亚（中国）": (
        "佛吉亚中国官网招聘入口可访问；公开网页复核到佛吉亚智永科技（重庆）"
        "财务总监，结构化发布时间为2026-07-22。"
    ),
    "博格华纳（中国）": (
        "博格华纳官方中国职位搜索逐条给出日期；窗口内检出的中国职位主要为"
        "工程师和经理，未发现Director+/功能Head。"
    ),
    "天津大冢饮料有限公司": (
        "未发现独立官方职位系统中的窗口内Director+职位；认证公司职位页主要为"
        "专员、主管等非目标层级。"
    ),
    "罗氏制药（中国）": (
        "罗氏官方职位编号ROCHGLOBAL202605111730及LinkedIn同职位镜像共同确认"
        "上海Head of Manufacturing；职位属于站点领导团队并拥有团队、预算及运营责任。"
    ),
    "西门子医疗（中国）": (
        "西门子医疗中国官方招聘入口可访问；未检出验证窗口内带可核日期的"
        "Director+/功能Head职位。"
    ),
    "上汽集团": (
        "检查集团/相关官方招聘入口及公开网页，未发现窗口内可核验的目标层级职位。"
    ),
    "北方华创": (
        "北方华创官方加入我们页面及认证招聘公司页均可核；公开职位仓储总监"
        "内嵌发布时间为2026-07-28。"
    ),
    "宁德时代": (
        "宁德时代官方社招入口可访问；公开结果未检出窗口内带可核日期的"
        "Director+/功能Head职位。"
    ),
    "欣旺达": (
        "欣旺达官方人才页面及认证招聘页已检查；发现的产品线副总经理职位"
        "发布时间为2025-09-28，早于验证窗口，不计入。"
    ),
    "汇川技术": (
        "检查官方人才入口及公开招聘结果，未发现窗口内可核验的目标层级职位。"
    ),
    "立讯精密": (
        "立讯精密官方社会招聘入口可访问；未检出窗口内带可核日期的"
        "Director+/功能Head职位。"
    ),
    "后摩智能": (
        "后摩官网只提供简历投递邮箱，公开搜索未发现窗口内可核验的"
        "Director+/功能Head职位。"
    ),
    "小鹏汇天": (
        "检查小鹏官方社招入口及小鹏汇天公开职位页；窗口内结果主要为"
        "经理、专家和工程师，按验收协议不计入。"
    ),
    "斯克斯机器人科技有限公司": (
        "斯克斯官网未提供带日期的高阶职位；公开招聘结果主要为工程师岗位，"
        "未发现窗口内目标层级职位。"
    ),
    "星动纪元": (
        "星动纪元官方加入我们页面可访问；公开结果以校招/工程岗位为主，"
        "未发现窗口内可核验的Director+/功能Head职位。"
    ),
    "星际荣耀": (
        "检查星际荣耀及全资海南发射子公司公开职位。采购部长、发射技术中心"
        "主任等标题存在，但当前冻结的Director+层级规则不自动等同这些头衔，"
        "且未取得满足协议的精确职位发布时间，因此不计入。"
    ),
    "普渡机器人": (
        "检查普渡官网及官方招聘入口；窗口内公开结果主要是校招/工程岗位，"
        "未发现可核验的Director+/功能Head职位。"
    ),
}


def main() -> None:
    manifest = json.loads(
        (TARGET / "manifest.json").read_text(encoding="utf-8")
    )
    matched = {item["company"] for item in JOBS}
    audits = []
    for company in manifest["companies"]:
        summary = COMPANY_SUMMARIES[company]
        audits.append(
            {
                "company": company,
                "searched_at": EXECUTED_AT,
                "window_start": WINDOW_START,
                "window_end_exclusive": WINDOW_END,
                "result": "matched" if company in matched else "no_eligible_job",
                "searches": [
                    {
                        "channel": "official_careers",
                        "query": (
                            f"{company} 官方 careers 社会招聘 "
                            "Director 总监 Head VP 2026"
                        ),
                        "executed_at": EXECUTED_AT,
                        "outcome_summary": summary,
                    },
                    {
                        "channel": "public_web_search",
                        "query": (
                            f"{company} (总监 OR Director OR Head OR VP) "
                            "招聘 2026-05 2026-06 2026-07"
                        ),
                        "executed_at": EXECUTED_AT,
                        "outcome_summary": summary,
                    },
                ],
            }
        )
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "jobs.json").write_text(
        json.dumps(
            {
                "search_protocol_version": "uniform-director-plus-v1",
                "jobs": JOBS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (TARGET / "label-audit.json").write_text(
        json.dumps(
            {
                "search_protocol_version": "uniform-director-plus-v1",
                "audits": audits,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
