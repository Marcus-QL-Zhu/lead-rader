#!/usr/bin/env python3
"""Build the blind V18 holdout from previously unlabelled company universes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "evaluation" / "holdout-v18"


def _load_evidence(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return list(value.get("evidence", value))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    v13 = _load_evidence(ROOT / "evaluation" / "holdout-v13" / "evidence.json")
    v16 = _load_evidence(ROOT / "evaluation" / "holdout-v16" / "evidence.json")
    v11 = _load_evidence(ROOT / "evaluation" / "holdout-v11" / "evidence.json")
    evidence = list(v13)
    for item in v16:
        if item["company"] != "中微公司":
            continue
        event = dict(item)
        event.update(
            {
                "source_url": (
                    "https://www.zc.gov.cn/zx/zcyw/content/post_10456403.html"
                ),
                "source_name": "广州市增城区人民政府",
                "source_kind": "government_official",
                "independent_source_group": "government_official",
            }
        )
        evidence.append(event)
    for item in v11:
        if item["company"] != "Takeda（武田中国）":
            continue
        event = dict(item)
        event.update(
            {
                "source_kind": "company_official",
                "independent_source_group": "company_official",
            }
        )
        evidence.append(event)
    evidence.append(
        {
            "company": "沃飞长空",
            "company_type": "startup_private",
            "event_type": "factory_or_capacity",
            "phase": "construction",
            "event_date": "2024-12-12",
            "published_at": "2024-12-13",
            "title": "沃飞长空全球总部基地在成都未来科技城开工",
            "snippet": (
                "一期建设总部办公、研发、生产制造和销售交付一体化基地，"
                "用于AE200 eVTOL批量化生产。"
            ),
            "source_excerpt": (
                "项目将打造集研发、生产、办公于一体的高标准厂房，"
                "投产后用于eVTOL航空器零部件成型生产制造及装配。"
            ),
            "source_url": (
                "https://cdsgxq.sczwfw.gov.cn/art/2024/12/13/"
                "art_24385_275911.html"
            ),
            "source_name": "成都高新区政务服务网",
            "source_grade": "A",
            "source_kind": "government_official",
            "independent_source_group": "government_official",
            "direction": "eVTOL、飞行汽车、总部基地、研发制造与销售交付",
            "source_locator": "项目开工及基地功能段落",
        }
    )
    companies = [
        "海柔创新 Hai Robotics",
        "梅卡曼德机器人 Mech-Mind Robotics",
        "嬴彻科技 Inceptio Technology",
        "常州飞鱼机器人科技",
        "旋光智能机器人",
        "沃飞长空",
        "文远知行 WeRide",
        "速腾聚创 RoboSense",
        "MiniMax Group",
        "新松机器人 SIASUN",
        "福日电子（以诺通讯）",
        "中微公司",
        "罗盖特 Roquette",
        "Gattefossé 嘉法狮",
        "安费诺 Amphenol",
        "威猛集团 Wittmann Group",
        "ABB",
        "Takeda（武田中国）",
    ]
    selected = [item for item in evidence if item["company"] in companies]
    if {item["company"] for item in selected} != set(companies):
        raise RuntimeError("V18 evidence is incomplete")
    manifest = {
        "holdout_version": "holdout-v18",
        "frozen_at": "2026-07-29",
        "cutoffs": ["2026-04-01"],
        "horizon_months": 3,
        "prompt_version": "historical-demand-v10-top3-calibrated-ontology",
        "prediction_max_roles_per_company": 3,
        "temperature": 0.0,
        "workforce_precursors_enabled": False,
        "prediction_inputs_exclude_job_ads": True,
        "josint_inputs_enabled": False,
        "candidate_selection": {
            "window_start": "2024-12-01",
            "window_end": "2026-03-31",
            "minimum_candidates": 18,
            "rule": (
                "Six unlabelled companies per type were selected only from "
                "A-grade pre-cutoff non-recruiting operating events. Fifteen "
                "were inherited from the sealed but never labelled V13 universe; "
                "one each came from the sealed but never labelled V11/V16 "
                "universes, and one fresh startup event was added before any "
                "V18 validation-window job search."
            ),
        },
        "companies": companies,
        "acceptance": {
            "minimum_matches_per_cutoff": 3,
            "minimum_distinct_matched_jobs": 3,
            "minimum_distinct_matched_companies": 3,
            "minimum_candidate_prediction_coverage": 0.75,
            "minimum_distinct_predicted_titles": 30,
            "minimum_distinct_predicted_role_families": 12,
            "minimum_distinct_canonical_role_keys": 30,
            "required_matched_company_types": [
                "startup_private",
                "listed",
                "foreign",
            ],
            "snapshot_audit_required": True,
            "uniform_label_search_required": True,
        },
        "freeze_rule": (
            "After seal, do not change evidence, prompt, ontology, matcher, "
            "thresholds, label protocol, job adapter or candidate universe. "
            "Freeze anonymous predictions before [2026-04-01,2026-07-01) labels."
        ),
    }
    readme = """# Holdout V18

V18 is the first independent validation after V17 ontology calibration.
It contains six startup/private, six listed and six foreign companies.
No V18 validation-window job label was searched before the prediction seal.
"""
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "evidence.json").write_text(
        json.dumps(
            {"dataset_version": "2026-07-29-holdout-v18", "evidence": selected},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (TARGET / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (TARGET / "README.md").write_text(readme, encoding="utf-8")
    sealed = [
        TARGET / "evidence.json",
        TARGET / "manifest.json",
        TARGET / "README.md",
        ROOT / "src" / "ht_lead_radar" / "backtest.py",
        ROOT / "src" / "ht_lead_radar" / "taxonomy.py",
        ROOT / "src" / "ht_lead_radar" / "company_demand_v2.py",
        ROOT / "scripts" / "run_historical_backtest.py",
        ROOT / "scripts" / "evaluate_holdout_reports.py",
    ]
    seal = {
        "holdout_version": "holdout-v18",
        "sealed_at": "2026-07-29T02:50:00+08:00",
        "prediction_started": False,
        "future_label_search_started": False,
        "files": {
            path.relative_to(ROOT).as_posix(): _hash(path) for path in sealed
        },
        "declaration": (
            "No V18 validation-window job search or label was opened before "
            "this seal."
        ),
    }
    (TARGET / "pre-prediction-seal.json").write_text(
        json.dumps(seal, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
