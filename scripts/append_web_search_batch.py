from __future__ import annotations

import argparse
import base64
from datetime import datetime
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--companies-base64", required=True)
    parser.add_argument("--result-base64", required=True)
    args = parser.parse_args()
    companies = json.loads(
        base64.b64decode(args.companies_base64).decode("utf-8")
    )
    result = base64.b64decode(args.result_base64).decode("utf-8")
    payload = (
        json.loads(args.output.read_text(encoding="utf-8"))
        if args.output.exists()
        else {"schema_version": 1, "batches": []}
    )
    payload["batches"] = [
        batch
        for batch in payload["batches"]
        if batch["batch_id"] != args.batch_id
    ]
    payload["batches"].append(
        {
            "batch_id": args.batch_id,
            "companies": companies,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "raw_result": result,
        }
    )
    payload["batches"].sort(key=lambda item: item["batch_id"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"batches={len(payload['batches'])} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
