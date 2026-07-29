#!/usr/bin/env python3
"""Build blind V20 from pre-cutoff verified operating evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "evaluation" / "holdout-v20"
CUTOFF = "2026-05-01"

STARTUPS = ["傲意科技", "因时机器人", "众擎机器人", "星环聚能", "银河通用机器人", "星尘智能"]
LISTED = ["华虹半导体", "寒武纪", "赛力斯", "国轩高科", "吉利汽车", "京东方"]
FOREIGN = [
    "Boehringer Ingelheim（勃林格殷格翰中国）",
    "Eli Lilly（礼来中国）",
    "AbbVie（艾伯维）",
    "Bristol Myers Squibb（百时美施贵宝中国）",
    "Boston Scientific（波士顿科学）",
    "Daiichi Sankyo（第一三共）",
]
COMPANIES = STARTUPS + LISTED + FOREIGN


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strict_event(company: str, item: dict, company_type: str) -> dict:
    date = str(item.get("event_date_candidate") or "")[:10]
    return {
        "company": company,
        "company_type": company_type,
        "event_type": item.get("event_type") or "other",
        "phase": item.get("phase") or "build_organize",
        "event_date": date,
        "published_at": date,
        "title": item.get("title") or "",
        "snippet": str(item.get("search_excerpt") or "")[:1600],
        "source_excerpt": str(item.get("search_excerpt") or "")[:1600],
        "source_url": item.get("final_url") or item.get("source_url") or "",
        "source_name": "replayable captured public source",
        "source_grade": item.get("source_grade") or "B",
        "source_kind": "company_official" if company == "傲意科技" else "mainstream_media",
        "independent_source_group": (item.get("final_url") or item.get("source_url") or "").split("/")[2],
        "direction": company,
        "source_locator": item.get("storage_path") or "captured artifact",
        "content_sha256": "",
    }


def _manual_listed() -> list[dict]:
    return [
        {
            "company": "国轩高科", "company_type": "listed", "event_type": "technical_milestone",
            "phase": "build_organize", "event_date": "2026-01-10", "published_at": "2026-01-12",
            "title": "国轩高科发布2026全球市场、质量、制造、技术和供应链战略",
            "snippet": "公司披露2025年动力与储能出货创新高，并在2026年推进固液混合产品量产、储售电体系、全球市场以及制造、质量和供应链全面升级。",
            "source_excerpt": "2026战略覆盖全球市场、质量、制造、技术和供应链，固液混合产品将量产落地并加速储电和售电体系搭建。",
            "source_url": "https://www.gotion.com.cn/newsInfo/752", "source_name": "国轩高科官网", "source_grade": "A",
            "source_kind": "company_official", "independent_source_group": "gotion-official", "direction": "动力电池、储能与全球化", "source_locator": "2026战略升级段落",
        },
        {
            "company": "吉利汽车", "company_type": "listed", "event_type": "technical_milestone",
            "phase": "build_organize", "event_date": "2026-01-05", "published_at": "2026-01-05",
            "title": "吉利在CES 2026发布全域AI 2.0与G-ASD智能驾驶系统",
            "snippet": "吉利从全域AI 1.0升级至2.0并正式发布G-ASD智能驾驶系统，推动高阶智能驾驶落地；2025年累计销量超过302万辆，同比增长39%。",
            "source_excerpt": "Geely advanced to Full-Domain AI 2.0 and launched G-ASD to accelerate high-level autonomous driving.",
            "source_url": "https://global.geely.com/en/news/2026/geely-ces-2026-full-domain-ai", "source_name": "Geely Global官网", "source_grade": "A",
            "source_kind": "company_official", "independent_source_group": "geely-official", "direction": "汽车AI、智能驾驶与规模化产品", "source_locator": "CES 2026发布段落",
        },
        {
            "company": "京东方", "company_type": "listed", "event_type": "research_or_ip",
            "phase": "build_organize", "event_date": "2026-01-21", "published_at": "2026-01-21",
            "title": "京东方举办2026首场技术策源地论坛",
            "snippet": "京东方围绕OLED、先进LED、光显示、光通信与钙钛矿等方向推进产学研协同；技术策源地计划已聘请40多位科技顾问并落地合作项目200余项。",
            "source_excerpt": "累计聘请40多位专家学者担任科技顾问，落地合作项目200余项，并推进OLED、LED、光通信与钙钛矿技术合作。",
            "source_url": "https://www.boe.com/company/dynamic-27251498af0b4df4b0ca1096d81750a0", "source_name": "京东方官网", "source_grade": "A",
            "source_kind": "company_official", "independent_source_group": "boe-official", "direction": "显示技术、产学研与创新平台", "source_locator": "技术策源地计划成果段落",
        },
    ]


def main() -> None:
    raw = json.loads((ROOT / "data" / "training-v3-all-news-verified.json").read_text(encoding="utf-8"))
    pool = json.loads((ROOT / "evaluation" / "training-v3" / "company-pool.json").read_text(encoding="utf-8"))["companies"]
    company_types = {item["company"]: item["company_type"] for item in pool}
    evidence: list[dict] = []
    strict_targets = set(STARTUPS + LISTED[:3]) - {"银河通用机器人"}
    for group in raw["companies"]:
        company = group["company"]
        if company not in strict_targets:
            continue
        candidates = [
            item for item in group.get("results", [])
            if item.get("verification_status") == "strict_evidence_ready"
            and str(item.get("event_date_candidate") or "")[:10] < CUTOFF
        ]
        if not candidates:
            raise RuntimeError(f"missing strict pre-cutoff evidence: {company}")
        evidence.extend(_strict_event(company, item, company_types[company]) for item in candidates)
    for item in evidence:
        if item["company"] == "华虹半导体":
            item.update({
                "event_type": "executive_change", "source_grade": "A",
                "source_kind": "exchange_filing",
                "source_url": "https://www.hkexnews.hk/listedco/listconews/sehk/2026/0213/2026021300990_c.pdf",
                "source_name": "香港交易所披露易",
                "independent_source_group": "hkex-filing",
            })
    evidence.append({
        "company": "因时机器人", "company_type": "startup_private", "event_type": "major_order",
        "phase": "build_organize", "event_date": "2025-12-30", "published_at": "2025-12-30",
        "title": "因时机器人全年灵巧手量产交付超过10000台",
        "snippet": "公司官方披露灵巧手已实现规模化交付，全年累计量产交付超过10000台，并持续推进多传动路线和多场景产品适配。",
        "source_excerpt": "因时机器人通过技术迭代与制造体系建设，全年累计量产交付超过10000台。",
        "source_url": "https://www.inspire-robots.com/news/company%20news/2025-12-30/296.html",
        "source_name": "因时机器人官网", "source_grade": "A", "source_kind": "company_official",
        "independent_source_group": "inspire-robots-official", "direction": "灵巧手量产与多场景交付", "source_locator": "万台交付与制造体系段落",
    })
    evidence.extend([
        {
            "company": "银河通用机器人", "company_type": "startup_private", "event_type": "major_order",
            "phase": "build_organize", "event_date": "2025-12-22", "published_at": "2025-12-23",
            "title": "银河通用获得超过1000台具身智能机器人部署订单",
            "snippet": "银河通用与百达精工签署战略合作，将在精密制造复杂场景及其生态体系内部署超过1000台具身智能机器人。",
            "source_excerpt": "双方将在百达精工及其生态体系内部署超过1,000台银河通用具身智能机器人。",
            "source_url": "https://www.prnasia.com/story/517052-1.shtml", "source_name": "美通社企业新闻稿", "source_grade": "B",
            "source_kind": "mainstream_media", "independent_source_group": "prnasia", "direction": "工业具身智能规模化部署", "source_locator": "千台订单与部署范围段落",
        },
        {
            "company": "银河通用机器人", "company_type": "startup_private", "event_type": "technical_milestone",
            "phase": "build_organize", "event_date": "2026-03-30", "published_at": "2026-03-30",
            "title": "银河通用机器人在海淀全家便利店实现24小时常态化服务",
            "snippet": "盖博特机器人正式落地海淀全家便利店并实现24小时常态化服务，标志零售场景从展示验证进入持续运营。",
            "source_excerpt": "机器人在中国大陆首家引入机器人常态化服务的连锁便利店实现24小时全天候值守。",
            "source_url": "https://open.beijing.gov.cn/html/haidian/gzdt/2026/3/1774834573943.html", "source_name": "开放北京", "source_grade": "A",
            "source_kind": "government_official", "independent_source_group": "beijing-government", "direction": "零售机器人常态化运营", "source_locator": "常态化服务与24小时值守段落",
        },
    ])
    evidence.extend(_manual_listed())
    v11 = json.loads((ROOT / "evaluation" / "holdout-v11" / "evidence.json").read_text(encoding="utf-8"))
    for item in v11.get("evidence", v11):
        if item["company"] in FOREIGN:
            event = dict(item)
            if event["company"] == "Eli Lilly（礼来中国）":
                event["event_type"] = "factory_or_capacity"
            elif event["company"] == "Bristol Myers Squibb（百时美施贵宝中国）":
                event["event_type"] = "regulatory_or_clinical"
            event.setdefault("source_kind", "company_official" if "官网" in str(event.get("source_name")) else "government_official")
            evidence.append(event)
    present = {item["company"] for item in evidence}
    if present != set(COMPANIES):
        raise RuntimeError(f"V19 evidence mismatch: missing={set(COMPANIES)-present}, extra={present-set(COMPANIES)}")
    manifest = {
        "holdout_version": "holdout-v20", "frozen_at": "2026-07-29", "cutoffs": [CUTOFF], "horizon_months": 3,
        "prompt_version": "historical-demand-v12-top3-evidence-quality", "prediction_max_roles_per_company": 3,
        "temperature": 0.0, "workforce_precursors_enabled": False, "prediction_inputs_exclude_job_ads": True, "josint_inputs_enabled": False,
        "candidate_selection": {"window_start": "2025-10-01", "window_end": "2026-04-30", "minimum_candidates": 18,
            "rule": "Six companies per type selected from replayable A/B pre-cutoff non-recruiting operating evidence before opening any V19 future-job labels."},
        "companies": COMPANIES,
        "acceptance": {"minimum_matches_per_cutoff": 3, "minimum_distinct_matched_jobs": 3, "minimum_distinct_matched_companies": 3,
            "minimum_candidate_prediction_coverage": 0.75, "minimum_distinct_predicted_titles": 30, "minimum_distinct_predicted_role_families": 12,
            "minimum_distinct_canonical_role_keys": 30, "required_matched_company_types": ["startup_private", "listed", "foreign"],
            "snapshot_audit_required": True, "uniform_label_search_required": True},
        "freeze_rule": "After seal, do not change evidence, prompt, ontology, matcher, thresholds, label protocol, job adapter or candidate universe. Freeze predictions before [2026-05-01,2026-08-01) labels.",
    }
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "evidence.json").write_text(json.dumps({"dataset_version":"2026-07-29-holdout-v20","evidence":evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    (TARGET / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (TARGET / "README.md").write_text("# Holdout V20\n\nIndependent top-3 replay after V19 evidence-quality calibration. Future recruiting labels stay closed until the prediction snapshot is sealed.\n", encoding="utf-8")
    sealed = [TARGET/"evidence.json", TARGET/"manifest.json", TARGET/"README.md", ROOT/"src/ht_lead_radar/backtest.py", ROOT/"src/ht_lead_radar/taxonomy.py", ROOT/"src/ht_lead_radar/company_demand_v2.py", ROOT/"scripts/run_historical_backtest.py", ROOT/"scripts/evaluate_holdout_reports.py"]
    seal = {"holdout_version":"holdout-v20", "sealed_at":"2026-07-29T03:00:00+08:00", "prediction_started":False, "future_label_search_started":False,
            "files":{p.relative_to(ROOT).as_posix():_hash(p) for p in sealed}, "declaration":"No V19 validation-window recruiting label was opened before this seal."}
    (TARGET/"pre-prediction-seal.json").write_text(json.dumps(seal,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__ == "__main__":
    main()