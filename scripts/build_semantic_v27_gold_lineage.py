#!/usr/bin/env python3
"""Build the independently reviewed Final-v2 development Gold lineage.

The frozen Final-v2 adjudication remains immutable.  This script records every
removed event and adds only independently identified atomic events using exact
source spans.  The result is an opened development asset, never a replacement
for the original blind Final-v2 benchmark.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from ht_lead_radar.semantic_gold import validate_gold_packet


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Zero-based indexes in the immutable parent annotation.
DROP_INDEXES: dict[str, set[int]] = {
    "vbdata-funding:1519087619": {1},
    "nbd-vcpe-weekly:4517408": {0, 1, 2, 5},
    "vbdata-funding:1519084694": {5, 6},
    "pedaily-investment-news:562976": {0},
    "pedaily-vcpe-events:566982": {1, 2, 3, 5},
    "nbd-vcpe-weekly:4455756": {1, 7},
    "36kr-financing-flash:3915175290901889": {1, 3, 4},
    "vbdata-funding:1519086865": {7},
    "nbd-vcpe-weekly:4482544": {0},
}
AMBIGUOUS_INDEXES: dict[str, set[int]] = {
    "pedaily-investment-news:563474": {1, 2},
    "nbd-vcpe-weekly:4455756": {2},
    "vbdata-funding:1519086865": {9, 10},
}
IMPORTANCE_UPDATES: dict[str, dict[int, str]] = {
    "vbdata-funding:1519086865": {1: "weak"},
    "nbd-vcpe-weekly:4482544": {4: "weak"},
}
SPAN_UPDATES: dict[str, dict[int, str]] = {
    "vbdata-funding:1519086865": {
        8: "已经获得多家药企的大规模生产订单",
    },
}


ADDITIONS: dict[str, list[dict[str, str]]] = {
    "vbdata-funding:1519087619": [
        {
            "canonical_company": "杭州迪英加科技有限公司",
            "event_type": "technical_milestone",
            "event_status": "target",
            "importance": "strong",
            "needle": (
                "迪英加将持续加大DeepPathAI大模型研发投入"
            ),
            "context_needle": "本次C轮融资完成，迪英加将持续加大DeepPathAI大模型研发投入",
            "review_reason": "公司 modal“将”明确支持独立的技术研发 target。",
        }
    ],
    "ccid-report-commentary:1120253": [
        {
            "canonical_company": "优必选",
            "event_type": "customer_validation",
            "event_status": "target",
            "importance": "strong",
            "needle": (
                "我们现在已经在已经在下半年和明年是我们重点去海外市场"
                "这样一个去推进这个具身智能落地的这样的一个关键的节点"
            ),
        }
    ],
    "vbdata-funding:1519084694": [
        {
            "canonical_company": "格式塔",
            "event_type": "technical_milestone",
            "event_status": "started",
            "importance": "strong",
            "needle": (
                "格式塔亦在探索基于这些多模态数据建立一个Foundation Model"
            ),
        }
    ],
    "36kr-financing-flash:3919125291412872": [
        {
            "canonical_company": "璨辰科技",
            "event_type": "technical_milestone",
            "event_status": "target",
            "importance": "strong",
            "needle": (
                "一是迭代全尺度虚拟器官仿真平台，升级多器官联动仿真能力"
            ),
            "context_needle": "本轮数千万天使系列融资将重点投向三大板块",
            "review_reason": "共同 target context 下的第一个独立平台升级动作。",
        },
        {
            "canonical_company": "璨辰科技",
            "event_type": "technical_milestone",
            "event_status": "target",
            "importance": "strong",
            "needle": "落地全模态虚拟大脑模型",
            "context_needle": "本轮数千万天使系列融资将重点投向三大板块",
            "review_reason": "共同 target context 下与平台迭代不同的模型交付动作。",
        },
    ],
    "pedaily-vcpe-events:566982": [
        {
            "canonical_company": "中 美瑞康核酸技术（南通）研究院有限公司",
            "event_type": "regulatory_or_clinical",
            "event_status": "completed",
            "importance": "strong",
            "needle": (
                "RAG-01是全球首个通过偶联递送技术在肿瘤领域"
                "完成临床概念验证的saRNA疗法"
            ),
        },
        {
            "canonical_company": "中 美瑞康核酸技术（南通）研究院有限公司",
            "event_type": "regulatory_or_clinical",
            "event_status": "completed",
            "importance": "strong",
            "needle": (
                "早期临床数据显示针对卡介苗失败的非肌层浸润性膀胱癌患者"
                "实现67%完全缓解率"
            ),
        },
        {
            "canonical_company": "中 美瑞康核酸技术（南通）研究院有限公司",
            "event_type": "regulatory_or_clinical",
            "event_status": "completed",
            "importance": "strong",
            "needle": "已获FDA快速通道资格",
        },
        {
            "canonical_company": "中 美瑞康核酸技术（南通）研究院有限公司",
            "event_type": "regulatory_or_clinical",
            "event_status": "completed",
            "importance": "strong",
            "needle": "I期临床数据显示出优异的SOD1蛋白抑制效果与安全性",
        },
        {
            "canonical_company": "中 美瑞康核酸技术（南通）研究院有限公司",
            "event_type": "regulatory_or_clinical",
            "event_status": "completed",
            "importance": "strong",
            "needle": "II期临床入组已全部完成",
        },
        {
            "canonical_company": "中 美瑞康核酸技术（南通）研究院有限公司",
            "event_type": "technical_milestone",
            "event_status": "target",
            "importance": "strong",
            "needle": (
                "持续迭代升级全球领先的saRNA/siRNA双模态小核酸技术平台，"
                "以及SCAD™、LiCO™两大自主研发的肝外递送系统"
            ),
            "context_needle": "本轮融资资金将主要用于加速公司核心管线的临床研发进程",
            "review_reason": "融资后路线图中的明确双模态平台与递送系统升级 target。",
        },
    ],
    "nbd-vcpe-weekly:4455756": [
        {
            "canonical_company": "智平方",
            "event_type": "customer_validation",
            "event_status": "completed",
            "importance": "strong",
            "needle": (
                "目前公司的产品已经在汽车、半导体、生物制造、公共服务、"
                "新零售等领域实现落地应用"
            ),
        }
    ],
    "vbdata-funding:1519086865": [
        {
            "canonical_company": "品善生物科技（上海）有限公司",
            "event_type": "customer_validation",
            "event_status": "completed",
            "importance": "weak",
            "needle": "获得了国内头部客户的认可",
        },
        {
            "canonical_company": "品善生物科技（上海）有限公司",
            "event_type": "customer_validation",
            "event_status": "completed",
            "importance": "strong",
            "needle": "实现了国内最大规模的商业化生产案例",
        },
        {
            "canonical_company": "Repligen（瑞普利金）",
            "event_type": "partnership",
            "event_status": "completed",
            "importance": "strong",
            "needle": (
                "品善生物近期与全球知名的生命科学公司Repligen（瑞普利金）"
                "围绕中空纤维过滤产品达成了战略合作"
            ),
        },
    ],
}


def _exact_span(body: str, needle: str) -> dict[str, Any]:
    start = body.find(needle)
    if start < 0:
        raise ValueError(f"evidence not found: {needle}")
    return {
        "text": needle,
        "char_start": start,
        "char_end": start + len(needle),
    }


def _event_from_addition(
    body: str,
    raw: dict[str, str],
    *,
    review_decision_id: str,
) -> dict[str, Any]:
    needle = raw["needle"]
    event = {
        "canonical_company": raw["canonical_company"],
        "event_type": raw["event_type"],
        "event_status": raw["event_status"],
        "importance": raw["importance"],
        "claim_ids": [],
        "candidate_gap": True,
        "evidence_span": _exact_span(body, needle),
        "lineage_provenance": {
            "review_decision_id": review_decision_id,
            "guide_sections": ["§1", "§2", "§3", "§4"],
            "reason": raw.get(
                "review_reason",
                "independent review identified a missing atomic current event",
            ),
        },
    }
    context_needle = raw.get("context_needle")
    if context_needle:
        event["status_context_span"] = _exact_span(body, context_needle)
    return event


def build_lineage(parent: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(parent)
    output["dataset_version"] = "semantic-v27-final-v2-lineage-v1"
    output["annotation_role"] = "independently_reviewed_development_lineage"
    output["annotator_id"] = "final-v2-independent-lineage-review"
    parent_comparison = output.pop("comparison", None)
    output["lineage"] = {
        "parent_dataset_version": parent.get("dataset_version"),
        "parent_file": "evaluation/semantic-v27/final-v2-gold/adjudication.json",
        "review_reports": [
            ".acceptance/semantic-v25/v27-final-v2-gold-review-a.md",
            ".acceptance/semantic-v25/v27-final-v2-gold-review-b.md",
        ],
        "benchmark_status": "opened_development_only",
        "parent_prediction_must_not_be_rerun": True,
        "parent_adjudication": {
            "annotator_id": parent.get("annotator_id"),
            "comparison": parent_comparison,
        },
        "change_contract": {
            "drop_count": 19,
            "ambiguous_count": 5,
            "addition_count": 15,
            "importance_update_count": 2,
            "span_update_count": 1,
        },
    }

    for case_number, case in enumerate(output.get("cases") or [], start=1):
        key = str(case["key"])
        annotation = case["annotation"]
        original_events = list(annotation.get("gold_events") or [])
        drop = DROP_INDEXES.get(key, set())
        ambiguous = AMBIGUOUS_INDEXES.get(key, set())
        updates = IMPORTANCE_UPDATES.get(key, {})
        span_updates = SPAN_UPDATES.get(key, {})
        exclusions: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        removed_claims: dict[str, str] = {}
        for index, event in enumerate(original_events):
            if index in drop or index in ambiguous:
                decision = "drop" if index in drop else "ambiguous"
                exclusions.append(
                    {
                        "parent_event_index": index,
                        "decision": decision,
                        "event": event,
                        "review_basis": (
                            "independent_gold_review_a"
                            if case_number <= 10
                            else "independent_gold_review_b"
                        ),
                        "review_decision_id": (
                            f"{'A' if case_number <= 10 else 'B'}-"
                            f"{case_number:02d}-parent-{index}"
                        ),
                        "guide_sections": ["§1", "§2", "§3", "§4"],
                        "reason": (
                            "independent review excluded this parent event from hard "
                            "Gold because its current operating-company subject, "
                            "atomicity, or time status was not uniquely supported"
                            if decision == "drop"
                            else "independent review could not uniquely distinguish "
                            "a current event from undated background"
                        ),
                    }
                )
                for claim_id in event.get("claim_ids") or []:
                    removed_claims[str(claim_id)] = decision
                continue
            copied = deepcopy(event)
            if index in updates:
                copied["importance"] = updates[index]
                changes.append(
                    {
                        "review_decision_id": f"importance-{key}-{index}",
                        "parent_event_index": index,
                        "field": "importance",
                        "before": event.get("importance"),
                        "after": updates[index],
                        "reason": "independent review recalibrated event strength",
                    }
                )
            if index in span_updates:
                copied["evidence_span"] = _exact_span(
                    str(case.get("clean_body") or ""), span_updates[index]
                )
                changes.append(
                    {
                        "review_decision_id": f"span-{key}-{index}",
                        "parent_event_index": index,
                        "field": "evidence_span",
                        "before": event.get("evidence_span"),
                        "after": copied["evidence_span"],
                        "reason": "independent review required the minimal atomic order span",
                    }
                )
            copied["lineage_parent_event_index"] = index
            kept.append(copied)

        kept_claims = {
            str(claim_id)
            for event in kept
            for claim_id in event.get("claim_ids") or []
        }
        for disposition in annotation.get("candidate_dispositions") or []:
            claim_id = str(disposition.get("claim_id") or "")
            if claim_id in removed_claims and claim_id not in kept_claims:
                decision = removed_claims[claim_id]
                disposition["disposition"] = (
                    "ambiguous" if decision == "ambiguous" else "rejected"
                )
                disposition["reason_code"] = (
                    "independent_review_ambiguous"
                    if decision == "ambiguous"
                    else "independent_review_not_current_operating_event"
                )

        additions = [
            _event_from_addition(
                str(case.get("clean_body") or ""),
                raw,
                review_decision_id=f"addition-{case_number:02d}-{addition_index}",
            )
            for addition_index, raw in enumerate(
                ADDITIONS.get(key, []), start=1
            )
        ]
        annotation["gold_events"] = kept + additions
        annotation["review_exclusions"] = exclusions
        annotation["lineage_added_event_count"] = len(additions)
        annotation["lineage_changes"] = changes
        case["adjudication_audit"] = {
            "resolution": "independent_lineage_reviewed",
            "parent_event_count": len(original_events),
            "kept_parent_event_count": len(kept),
            "dropped_parent_event_count": len(drop),
            "ambiguous_parent_event_count": len(ambiguous),
            "added_atomic_event_count": len(additions),
        }

    validation = validate_gold_packet(output)
    if not validation["valid"]:
        raise ValueError(
            "lineage Gold failed validation: "
            + json.dumps(validation, ensure_ascii=False)
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--parent",
        type=Path,
        default=(
            PROJECT_ROOT
            / "evaluation/semantic-v27/final-v2-gold/adjudication.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            PROJECT_ROOT
            / "evaluation/semantic-v27/final-v2-gold-lineage-v1"
            / "adjudication.json"
        ),
    )
    args = parser.parse_args()
    parent = json.loads(args.parent.read_text(encoding="utf-8"))
    result = build_lineage(parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "case_count": len(result["cases"]),
                "gold_event_count": sum(
                    len(case["annotation"]["gold_events"])
                    for case in result["cases"]
                ),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
