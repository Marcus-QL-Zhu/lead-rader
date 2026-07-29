from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


NEWS_GROUPS = {
    "leadership_organization": (
        "任命 履新 换帅 高管 事业部 区域负责人 组织架构",
        "appointed president executive business unit organization",
    ),
    "capital_structure": (
        "融资 并购 合资 上市 资金用途",
        "funding acquisition joint venture IPO proceeds",
    ),
    "operations_capacity": (
        "落户 总部 工厂 产线 扩产 投产 环评 施工 设备",
        "headquarters site factory capacity production permit",
    ),
    "customer_revenue": (
        "订单 定点 中标 客户验证 供应商准入 渠道 交付",
        "order award customer validation supplier delivery channel",
    ),
    "technology_product": (
        "产品发布 样机 流片 临床 注册证 认证 专利 技术突破",
        "product launch prototype tapeout approval patent milestone",
    ),
    "partnership_expansion": (
        "战略合作 联合研发 生态 出海 海外 市场进入",
        "partnership joint development ecosystem overseas market entry",
    ),
    "enterprise_system": (
        "ERP MES PLM CRM 数字化 招标 上线",
        "ERP MES PLM CRM digital transformation tender go-live",
    ),
}


def _task_id(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "task_" + sha256(encoded).hexdigest()[:20]


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--news-start", default="2026-01-01")
    parser.add_argument("--news-end-inclusive", default="2026-06-30")
    args = parser.parse_args()

    pool_bytes = args.pool.read_bytes()
    pool = json.loads(pool_bytes.decode("utf-8"))
    companies = pool["companies"]
    liepin_manifest = {
        "schema_version": 1,
        "pool_sha256": sha256(pool_bytes).hexdigest(),
        "source": "Liepin public company pages, guest read-only access",
        "rules": {
            "selected_before_job_collection": True,
            "current_snapshot_is_auxiliary": True,
            "exact_published_at_required_for_strict_benchmark": True,
            "test_labels_must_remain_sealed_during_model_development": True,
        },
        "companies": [
            {
                "company": item["company"],
                "company_type": item["company_type"],
                "sector": item["sector"],
                "split": item["split"],
                "company_page_url": None,
                "discovery_status": "pending",
            }
            for item in companies
        ],
    }
    news_tasks: list[dict[str, Any]] = []
    for item in companies:
        company = item["company"]
        body = {
            "kind": "precursor_news_timeline",
            "company": company,
            "company_type": item["company_type"],
            "sector": item["sector"],
            "split": item["split"],
            "window_start": args.news_start,
            "window_end_inclusive": args.news_end_inclusive,
            "query_groups": [
                {
                    "group": group,
                    "queries": [
                        (
                            f'"{company}" {terms} after:{args.news_start} '
                            f"before:2026-07-01"
                        )
                        for terms in terms_list
                    ],
                }
                for group, terms_list in NEWS_GROUPS.items()
            ],
            "required_artifacts": [
                "source URL",
                "published_at or observed_at",
                "raw page or snapshot",
                "content_sha256",
                "event type and phase",
            ],
            "prediction_boundary": (
                "Job advertisements are labels only and must not enter news evidence."
            ),
            "status": "pending",
        }
        news_tasks.append({"task_id": _task_id(body), **body})
    news_plan = {
        "schema_version": 1,
        "pool_sha256": sha256(pool_bytes).hexdigest(),
        "counts": {
            "companies": len(companies),
            "tasks": len(news_tasks),
            "queries": sum(
                len(group["queries"])
                for task in news_tasks
                for group in task["query_groups"]
            ),
        },
        "tasks": news_tasks,
    }
    _write(args.output_dir / "liepin-company-manifest.json", liepin_manifest)
    _write(args.output_dir / "snapshot-news-tasks.json", news_plan)
    print(json.dumps(news_plan["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
