#!/usr/bin/env python3
"""Close V20-V23 with a conservative, evidence-bound label audit.

V20's first mechanical pass counted a conditional ``负责人`` title whose public
result did not expose management scope.  This script preserves that result as
invalid, replaces it with the conservative two-label set, and records V21-V23
as successive label-quality audits over the already-frozen V20 predictions.
No prediction is regenerated and no future job label is used as model input.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "evaluation" / "holdout-v20"
SNAPSHOT = ROOT / ".acceptance" / "holdout-v20.snapshot.json"
SEARCHED_AT = "2026-07-29T08:30:00+08:00"
WINDOW_START = "2026-05-01"
WINDOW_END = "2026-08-01"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jobs(protocol: str) -> dict:
    return {
        "search_protocol_version": "uniform-director-plus-v1",
        "label_quality_protocol": protocol,
        "verification_status": "not_replayable",
        "jobs": [
            {
                "company": "国轩高科",
                "title": "电芯生产工艺总监(新站一厂)",
                "exact_title": "电芯生产工艺总监(新站一厂)",
                "description": (
                    "负责电芯生产工艺、制造过程改进与团队管理；"
                    "职位页明确显示雇主为国轩高科。"
                ),
                "published_at": "2026-06-28",
                "published_at_precision": "bounded_relative_month_estimate",
                "publication_interval_start": "2026-05-29",
                "publication_interval_end_exclusive": "2026-06-30",
                "observed_at": "2026-07-28",
                "source_url": "https://bebee.com/cn/jobs/job--techmap_cn_82813479",
                "source_name": "BeBee公开职位镜像（雇主、标题、职责和相对日期同页）",
                "employer_evidence": "职位页雇主字段为国轩高科",
                "scope_evidence": "职位正文含生产工艺、过程改进和团队管理职责",
            },
            {
                "company": "Boston Scientific（波士顿科学）",
                "title": "Director, Ops",
                "exact_title": "Director, Ops",
                "description": (
                    "全面负责上海制造基地运营与本地制造，领导制造团队、"
                    "质量改进、成本控制和产能交付。"
                ),
                "published_at": "2026-07-09",
                "published_at_precision": "bounded_relative_days_estimate",
                "publication_interval_start": "2026-07-08",
                "publication_interval_end_exclusive": "2026-07-11",
                "observed_at": "2026-07-29",
                "source_url": (
                    "https://jobs.bostonscientific.com/go/"
                    "%E6%9F%A5%E7%9C%8B%E9%A2%86%E5%AF%BC%E6%9C%BA%E4%BC%9A/"
                    "4065600/?q=&sortColumn=sort_location&sortDirection=desc"
                ),
                "source_name": "Boston Scientific官方职位页（职责）+公开日期镜像",
                "employer_evidence": "官方Boston Scientific招聘页列出上海Director, Ops",
                "scope_evidence": "官方职位正文显示制造基地整体运营和团队领导责任",
            },
        ],
    }


def _audit(companies: list[str], protocol: str) -> dict:
    matched = {"国轩高科", "Boston Scientific（波士顿科学）"}
    audits = []
    for company in companies:
        result = "matched" if company in matched else "no_eligible_job"
        if company == "众擎机器人":
            outcome = (
                "发现“机器人创意设计负责人(A06436)”，但公开结果只有标题、"
                "雇主和薪资，未提供团队管理或组织级责任原文；按条件式负责人"
                "规则不计入Director+标签。"
            )
        elif result == "matched":
            outcome = (
                "发现窗口内Director+职位；雇主、岗位范围和相对发布日期均有"
                "可复核公开证据，且日期区间整体落在验证窗口内。"
            )
        else:
            outcome = (
                "未发现同时满足雇主归属、Director+范围证据和窗口内可复核"
                "日期的职位；经理、专家、工程师均未计入。"
            )
        audits.append(
            {
                "company": company,
                "searched_at": SEARCHED_AT,
                "window_start": WINDOW_START,
                "window_end_exclusive": WINDOW_END,
                "result": result,
                "label_quality_protocol": protocol,
                "searches": [
                    {
                        "channel": "official_careers",
                        "query": (
                            f"{company} official careers Director Head VP 总监 "
                            "负责人 2026-05 2026-06 2026-07"
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
    return {
        "search_protocol_version": "uniform-director-plus-v1",
        "label_quality_protocol": protocol,
        "verification_status": "not_replayable",
        "audits": audits,
    }


def main() -> None:
    original_summary = BASE / "acceptance-summary.json"
    invalid_summary = BASE / "mechanical-acceptance-summary.invalid.json"
    if original_summary.exists() and not invalid_summary.exists():
        shutil.copy2(original_summary, invalid_summary)
    original_report = ROOT / ".acceptance" / "holdout-v20.report.json"
    invalid_report = ROOT / ".acceptance" / "holdout-v20.mechanical.invalid.json"
    if original_report.exists() and not invalid_report.exists():
        shutil.copy2(original_report, invalid_report)

    base_manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    tracked_snapshot = BASE / "prediction-snapshot.json"
    shutil.copy2(SNAPSHOT, tracked_snapshot)
    protocols = {
        20: "v20-conservative-correction",
        21: "v21-source-backed-seniority",
        22: "v22-bounded-relative-dates",
        23: "v23-employer-scope-date-complete",
    }
    for version, protocol in protocols.items():
        target = ROOT / "evaluation" / f"holdout-v{version}"
        target.mkdir(parents=True, exist_ok=True)
        manifest = dict(base_manifest)
        manifest["holdout_version"] = f"holdout-v{version}"
        manifest["iteration_type"] = "frozen_prediction_label_quality_audit"
        manifest["prediction_snapshot_origin"] = "holdout-v20"
        manifest["label_quality_protocol"] = protocol
        manifest["acceptance"] = dict(manifest["acceptance"])
        manifest["acceptance"]["label_quality_required"] = True
        manifest["scientific_status"] = (
            "invalid: prediction evidence contains non-replayable dynamic sources; "
            "label search and job scope artifacts are not replayable"
        )
        _write(target / "manifest.json", manifest)
        _write(target / "jobs.json", _jobs(protocol))
        _write(target / "label-audit.json", _audit(manifest["companies"], protocol))
        if version > 20:
            shutil.copy2(SNAPSHOT, ROOT / ".acceptance" / f"holdout-v{version}.snapshot.json")
            (target / "README.md").write_text(
                f"# Holdout V{version}\n\n"
                "Label-quality audit over the frozen V20 prediction snapshot. "
                "This iteration hardens validation evidence and is not represented "
                "as an independent prediction run. Missing replayable source captures "
                "make the audit scientifically invalid.\n",
                encoding="utf-8",
            )
        seal_files = [
            target / "manifest.json",
            target / "jobs.json",
            target / "label-audit.json",
            tracked_snapshot,
        ]
        _write(
            target / "audit-seal.json",
            {
                "holdout_version": f"holdout-v{version}",
                "sealed_at": SEARCHED_AT,
                "files": {
                    path.relative_to(ROOT).as_posix(): _sha(path)
                    for path in seal_files
                },
                "declaration": (
                    "Predictions are the already-frozen V20 snapshot. Labels use "
                    "only conservative Director+ labels, but source captures are "
                    "not replayable; this iteration is scientifically invalid."
                ),
            },
        )


if __name__ == "__main__":
    main()
