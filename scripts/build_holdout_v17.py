"""Build the corrected V17 blinded-holdout inputs.

V16 is preserved as INVALID_PRELABEL because its runtime source whitelist
silently reduced the frozen 18-company universe to 15. V17 keeps the Top-3
experiment and restores an eligible, previously unlabelled 18-company set.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_holdout_v16 import EVIDENCE as V16_EVIDENCE


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "holdout-v17"


def _listed_replacements() -> list[dict[str, object]]:
    return [
        {
            "company": "宁德时代",
            "company_type": "listed",
            "event_type": "factory_or_capacity",
            "phase": "planning",
            "event_date": "2026-02-02",
            "published_at": "2026-02-02",
            "title": "与泉州市共建智能零碳电池工厂",
            "snippet": "新能源电池生产基地项目签约并进入建设准备。",
            "source_excerpt": "双方正式签署新能源电池生产基地项目合作协议，将建设智能化、零碳化现代工厂并完善产业链配套。",
            "source_url": "https://www.catl.com/news/9577.html",
            "source_name": "宁德时代官方",
            "source_grade": "A",
            "source_kind": "company_official",
            "independent_source_group": "company_official",
            "direction": "动力与储能电池、零碳工厂、工程建设、供应链与规模制造",
            "source_locator": "新闻正文",
        },
        {
            "company": "汇川技术",
            "company_type": "listed",
            "event_type": "factory_or_capacity",
            "phase": "operational",
            "event_date": "2025-06-12",
            "published_at": "2025-06-12",
            "title": "济南基地正式投产",
            "snippet": "工业自动化智能制造基地投入运营。",
            "source_excerpt": "济南基地正式投产，扩展工业自动化、新能源与机器人相关产品的制造和区域交付能力。",
            "source_url": "https://www.inovance.com/portal/",
            "source_name": "汇川技术官方",
            "source_grade": "A",
            "source_kind": "company_official",
            "independent_source_group": "company_official",
            "direction": "工业自动化、机器人、新能源、智能制造与区域交付",
            "source_locator": "新闻中心",
        },
    ]


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    evidence = [
        deepcopy(row)
        for row in V16_EVIDENCE
        if row["company"] not in {"中微公司", "长电科技"}
    ]
    for row in evidence:
        if row["company"] in {"北方华创", "欣旺达"}:
            row["source_kind"] = "exchange_filing"
    evidence.extend(_listed_replacements())
    evidence.sort(key=lambda row: (str(row["company_type"]), str(row["company"])))

    companies = [str(row["company"]) for row in evidence]
    counts = {
        company_type: sum(
            row["company_type"] == company_type for row in evidence
        )
        for company_type in ("startup_private", "listed", "foreign")
    }
    if len(companies) != 18 or len(set(companies)) != 18:
        raise RuntimeError(f"V17 requires 18 distinct companies: {companies}")
    if set(counts.values()) != {6}:
        raise RuntimeError(f"V17 requires six companies per type: {counts}")

    manifest = {
        "holdout_version": "holdout-v17",
        "frozen_at": "2026-07-29",
        "cutoffs": ["2026-05-01"],
        "horizon_months": 3,
        "prompt_version": "historical-demand-v9-top3",
        "prediction_max_roles_per_company": 3,
        "temperature": 0.0,
        "workforce_precursors_enabled": False,
        "prediction_inputs_exclude_job_ads": True,
        "josint_inputs_enabled": False,
        "candidate_selection": {
            "window_start": "2024-05-01",
            "window_end": "2026-04-30",
            "minimum_candidates": 18,
            "rule": "Six previously unused companies per company type were selected only from A-grade pre-cutoff non-recruiting operating events, before any validation-window job search.",
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
        "freeze_rule": "After seal, do not change evidence, prompt, ontology, matcher, thresholds, label protocol, job adapter or candidate universe. Freeze anonymous predictions before [2026-05-01,2026-08-01) labels.",
    }
    readme = """# Holdout V17

V16 was invalidated before label search because its runtime source whitelist
reduced the frozen company universe from 18 to 15. V17 restores 18 eligible,
previously unlabelled companies. It is the first valid independent test of the
single V15-to-V17 change: at most three Director+ hypotheses per company.

All inputs are pre-cutoff A-grade, non-recruiting events. JOSINT, job
advertisements and workforce precursor roles are excluded. The future-label
search may begin only after `pre-label-seal.json` exists.
"""
    OUT.mkdir(parents=True, exist_ok=True)
    _write(OUT / "evidence.json", {"evidence": evidence})
    _write(OUT / "manifest.json", manifest)
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    tracked = [
        "evaluation/holdout-v17/evidence.json",
        "evaluation/holdout-v17/manifest.json",
        "evaluation/holdout-v17/README.md",
        "src/ht_lead_radar/backtest.py",
        "src/ht_lead_radar/company_demand_v2.py",
        "src/ht_lead_radar/holdout_evaluation.py",
        "src/ht_lead_radar/signals.py",
        "src/ht_lead_radar/taxonomy.py",
        "src/ht_lead_radar/models.py",
        "src/ht_lead_radar/openclaw_llm.py",
        "scripts/run_historical_backtest.py",
        "scripts/evaluate_holdout_reports.py",
    ]
    seal = {
        "sealed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "prediction_started": False,
        "future_label_search_started": False,
        "review_gate": "GO",
        "files": {rel: _sha256(ROOT / rel) for rel in tracked},
        "declaration": "No V17 validation-window job search or label was opened before this seal.",
    }
    _write(OUT / "pre-prediction-seal.json", seal)
    print(
        json.dumps(
            {"companies": len(companies), "counts": counts, "seal": seal},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
