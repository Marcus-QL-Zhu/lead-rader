"""Run the bounded Metaso funding-source benchmark."""

from __future__ import annotations

import argparse
import json

from ht_lead_radar.funding_benchmark import run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="config/source-packs.json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--budget-db", default="data/search-budget.sqlite")
    parser.add_argument(
        "--output",
        default="reports/funding-coverage-benchmark-2026-07-25.json",
    )
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--configured-limit", type=int, default=90)
    parser.add_argument("--result-limit", type=int, default=10)
    args = parser.parse_args()
    payload = run_benchmark(
        registry_path=args.registry,
        env_file=args.env_file,
        budget_db=args.budget_db,
        output_path=args.output,
        seed=args.seed,
        sample_size=args.sample_size,
        configured_limit=args.configured_limit,
        result_limit=args.result_limit,
    )
    print(json.dumps({
        "output": args.output,
        "sample_size": payload["sample_size"],
        "candidate_pool_size": payload["candidate_pool_size"],
        "budget_after": payload["budget_after"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
