#!/usr/bin/env python3
"""Build a deterministic manifest from replayable public job artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_DIR = ROOT / "evaluation" / "training-v3" / "job-artifacts"
DEFAULT_OUTPUT = (
    ROOT / "evaluation" / "training-v3" / "job-artifacts-manifest.json"
)
DEFAULT_COMPANY_POOL = ROOT / "evaluation" / "training-v3" / "company-pool-v2.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root: Path, value: Any) -> Path:
    path = (root / str(value or "")).resolve()
    root = root.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"artifact path escapes root: {value}")
    return path


def _exact_span(span: Any, text: str) -> bool:
    if not isinstance(span, Mapping):
        return False
    try:
        start = int(span["char_start"])
        end = int(span["char_end"])
    except (KeyError, TypeError, ValueError):
        return False
    value = str(span.get("text") or "")
    return 0 <= start < end <= len(text) and text[start:end] == value


def validate_job_row(row: Mapping[str, Any], *, root: Path) -> list[str]:
    """Return deterministic replay errors without changing review decisions."""

    errors: list[str] = []
    for path_field, hash_field in (
        ("raw_artifact_path", "raw_artifact_sha256"),
        ("normalized_text_path", "normalized_text_sha256"),
    ):
        path = _inside(root, row.get(path_field))
        if not path.is_file():
            errors.append(f"missing:{path_field}")
            continue
        if _sha256(path) != str(row.get(hash_field) or "").lower():
            errors.append(f"hash_mismatch:{path_field}")
    normalized_path = _inside(root, row.get("normalized_text_path"))
    if normalized_path.is_file():
        text = normalized_path.read_text(encoding="utf-8")
        for field in (
            "source_job_id_span",
            "title_span",
            "employer_span",
            "publication_span",
        ):
            if not _exact_span(row.get(field), text):
                errors.append(f"invalid_span:{field}")
        scopes = row.get("scope_spans")
        if not isinstance(scopes, list) or not scopes:
            errors.append("missing_scope_spans")
        elif any(not _exact_span(span, text) for span in scopes):
            errors.append("invalid_span:scope_spans")
    return errors


def build_manifest(
    *,
    artifact_dir: Path,
    root: Path,
    company_identities: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    identities: dict[tuple[str, str], str] = {}
    urls: dict[str, str] = {}
    for path in sorted(artifact_dir.glob("*/job-row.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(row, dict):
            raise ValueError(f"job row must be an object: {path}")
        artifact_id = str(row.get("artifact_id") or "")
        if artifact_id != path.parent.name:
            raise ValueError(f"artifact directory mismatch: {path}")
        errors = validate_job_row(row, root=root)
        if errors:
            raise ValueError(f"unreplayable artifact {artifact_id}: {errors}")
        identity = (
            str(row.get("source_platform") or ""),
            str(row.get("source_job_id") or ""),
        )
        prior = identities.setdefault(identity, artifact_id)
        if prior != artifact_id:
            raise ValueError(f"duplicate platform/job identity: {identity}")
        final_url = str(row.get("final_url") or "")
        prior_url = urls.setdefault(final_url, artifact_id)
        if prior_url != artifact_id:
            raise ValueError(f"duplicate final URL: {final_url}")
        identity = (company_identities or {}).get(str(row.get("company") or ""))
        if identity:
            row = {
                **row,
                "canonical_company_id": identity["canonical_company_id"],
                "corporate_family_id": identity["corporate_family_id"],
                "identity_source": "company-pool-v2",
            }
        rows.append(row)
    digest = hashlib.sha256(
        json.dumps(
            rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "purpose": "replayable-director-plus-job-outcomes",
        "review_policy": "pending rows never become evaluation labels",
        "counts": {
            "artifacts": len(rows),
            "replayable": len(rows),
            "evaluation_eligible": sum(
                row.get("evaluation_eligible") is True for row in rows
            ),
            "approved": sum(row.get("review_status") == "approved" for row in rows),
            "pending_human_review": sum(
                row.get("review_status") == "pending_human_review" for row in rows
            ),
        },
        "jobs": rows,
        "manifest_sha256": digest,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--artifact-root", type=Path, default=ROOT)
    parser.add_argument("--company-pool", type=Path, default=DEFAULT_COMPANY_POOL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    pool = json.loads(args.company_pool.read_text(encoding="utf-8"))
    identities = {
        str(row["company"]): {
            "canonical_company_id": str(row["canonical_company_id"]),
            "corporate_family_id": str(row["corporate_family_id"]),
        }
        for row in pool.get("companies") or []
    }
    manifest = build_manifest(
        artifact_dir=args.artifact_dir,
        root=args.artifact_root,
        company_identities=identities,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
