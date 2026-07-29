#!/usr/bin/env python3
"""Deterministically expand the frozen v2 company universe to 120 companies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


NEW_COMPANIES = {
    "startup_private": [
        ("帕西尼感知科技", "embodied_ai"),
        ("跨维智能", "embodied_ai"),
        ("星海图", "embodied_ai"),
        ("本末科技", "robotics"),
        ("鹿明机器人", "embodied_ai"),
        ("星尘智能", "embodied_ai"),
        ("原力灵机", "embodied_ai"),
        ("自变量机器人", "embodied_ai"),
        ("星动纪元", "embodied_ai"),
        ("智平方", "embodied_ai"),
        ("梅卡曼德机器人", "industrial_robotics"),
        ("海柔创新", "warehouse_robotics"),
        ("仙工智能", "industrial_robotics"),
        ("极智嘉", "warehouse_robotics"),
        ("普渡机器人", "service_robotics"),
        ("傅利叶智能", "embodied_ai"),
        ("小鹏汇天", "low_altitude_mobility"),
        ("星际荣耀", "commercial_space"),
        ("微纳星空", "commercial_space"),
        ("中科宇航", "commercial_space"),
    ],
    "listed": [
        ("北方华创", "semiconductor_equipment"),
        ("中微公司", "semiconductor_equipment"),
        ("通富微电", "semiconductor"),
        ("兆易创新", "semiconductor"),
        ("澜起科技", "semiconductor"),
        ("韦尔股份", "semiconductor"),
        ("沪硅产业", "semiconductor_materials"),
        ("盛美上海", "semiconductor_equipment"),
        ("拓荆科技", "semiconductor_equipment"),
        ("华海清科", "semiconductor_equipment"),
        ("京东方", "display"),
        ("立讯精密", "electronics_manufacturing"),
        ("舜宇光学科技", "optics"),
        ("大族激光", "industrial_equipment"),
        ("新时达", "industrial_robotics"),
        ("机器人（新松）", "industrial_robotics"),
        ("上汽集团", "new_energy_vehicle"),
        ("广汽集团", "new_energy_vehicle"),
        ("赛力斯", "new_energy_vehicle"),
        ("欣旺达", "battery"),
    ],
    "foreign": [
        ("泛林集团（中国）", "semiconductor_equipment"),
        ("科磊（中国）", "semiconductor_equipment"),
        ("东京电子（中国）", "semiconductor_equipment"),
        ("伊顿（中国）", "industrial_technology"),
        ("江森自控（中国）", "industrial_technology"),
        ("派克汉尼汾（中国）", "industrial_technology"),
        ("费斯托（中国）", "industrial_automation"),
        ("SMC（中国）", "industrial_automation"),
        ("瓦里安（中国）", "medical_technology"),
        ("赛默飞世尔（中国）", "life_science_tools"),
        ("西门子医疗（中国）", "medical_technology"),
        ("美敦力（中国）", "medical_technology"),
        ("罗氏诊断（中国）", "diagnostics"),
        ("史赛克（中国）", "medical_technology"),
        ("佛吉亚（中国）", "automotive_technology"),
        ("李尔（中国）", "automotive_technology"),
        ("博格华纳（中国）", "automotive_technology"),
        ("法雷奥（中国）", "automotive_technology"),
        ("奥托立夫（中国）", "automotive_technology"),
        ("亚德诺半导体（中国）", "semiconductor"),
    ],
}


def _score(seed: str, company_type: str, company: str) -> str:
    return hashlib.sha256(
        f"{seed}|{company_type}|{company}".encode("utf-8")
    ).hexdigest()


def expand_pool(base: dict[str, Any], *, seed: str) -> dict[str, Any]:
    existing = {row["company"] for row in base["companies"]}
    additions: list[dict[str, str]] = []
    for company_type, rows in NEW_COMPANIES.items():
        ordered = sorted(rows, key=lambda row: _score(seed, company_type, row[0]))
        for index, (company, sector) in enumerate(ordered):
            if company in existing:
                raise ValueError(f"duplicate company across v2/v3: {company}")
            split = "train" if index < 12 else "calibration" if index < 14 else "test"
            additions.append(
                {
                    "company": company,
                    "company_type": company_type,
                    "sector": sector,
                    "split": split,
                    "pool_generation": "v3_expansion",
                }
            )
    return {
        "schema_version": 3,
        "seed": seed,
        "selection_policy": (
            "The 60-company expansion list and deterministic within-type split "
            "were frozen before collecting recruiting outcomes."
        ),
        "base_pool_count": len(base["companies"]),
        "added_count": len(additions),
        "companies": [*base["companies"], *additions],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", default="historical-training-v3-2026-07-28")
    args = parser.parse_args()
    result = expand_pool(
        json.loads(args.base.read_text(encoding="utf-8")),
        seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts = {
        split: sum(row["split"] == split for row in result["companies"])
        for split in ("train", "calibration", "test")
    }
    print(json.dumps({"companies": len(result["companies"]), **counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
