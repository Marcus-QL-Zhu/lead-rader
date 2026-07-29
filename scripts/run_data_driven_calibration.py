"""Run a preregistered train/calibration-only role-ranker iteration."""

from __future__ import annotations

import argparse
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from ht_lead_radar.calibration import DEVELOPMENT_SPLITS, run_calibration
from ht_lead_radar.historical_training import HistoricalTrainingRow, validate_rows


ROW_FIELDS = {item.name for item in fields(HistoricalTrainingRow)}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_development_rows(path: Path) -> tuple[HistoricalTrainingRow, ...]:
    payload = _read_json(path)
    rows = []
    for item in payload["rows"]:
        # Read only the partition marker before skipping frozen test rows.
        # No test label, feature, evidence, or outcome enters this process.
        if item.get("split") not in DEVELOPMENT_SPLITS:
            continue
        values = {key: item[key] for key in ROW_FIELDS if key in item}
        values["label_weight"] = float(values["label_weight"])
        values["evidence_ids"] = tuple(values.get("evidence_ids") or ())
        values["matched_job_ids"] = tuple(values.get("matched_job_ids") or ())
        values["features"] = {
            str(key): float(value)
            for key, value in (values.get("features") or {}).items()
        }
        rows.append(HistoricalTrainingRow(**values))
    validate_rows(rows)
    return tuple(rows)


def run(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    manifest = _read_json(manifest_path)
    dataset_path = Path(manifest["inputs"]["dataset"]["path"])
    expected_dataset_hash = manifest["inputs"]["dataset"]["sha256"]
    actual_dataset_hash = _sha256(dataset_path)
    if actual_dataset_hash != expected_dataset_hash:
        raise ValueError("dataset hash differs from preregistered manifest")

    for frozen_input in manifest["frozen_references"]:
        path = Path(frozen_input["path"])
        if _sha256(path) != frozen_input["sha256"]:
            raise ValueError(f"frozen reference hash mismatch: {path}")

    grid = manifest["candidate_grid"]
    gate = manifest["acceptance_gate"]
    result = run_calibration(
        _load_development_rows(dataset_path),
        l2_values=tuple(float(value) for value in grid["l2_values"]),
        learned_weights=tuple(
            float(value) for value in grid["learned_weights"]
        ),
        feature_policies=tuple(grid["feature_policies"]),
        iterations=int(grid["iterations"]),
        learning_rate=float(grid["learning_rate"]),
        top_k=int(grid["top_k"]),
        maximum_slice_regression=float(
            gate["maximum_company_type_top1_regression"]
        ),
    )
    report = {
        "schema_version": 1,
        "iteration": manifest["iteration"],
        "manifest_sha256": _sha256(manifest_path),
        "dataset_sha256": actual_dataset_hash,
        **result,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output_path.resolve())
    print(json.dumps(report["decision"], ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--manifest", required=True)
    root.add_argument("--output", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return run(args)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"calibration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
