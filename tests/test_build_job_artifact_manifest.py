from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.build_job_artifact_manifest import build_manifest


def _span(text: str, value: str) -> dict[str, object]:
    start = text.index(value)
    return {"text": value, "char_start": start, "char_end": start + len(value)}


def test_manifest_replays_hashes_spans_and_preserves_pending_review(tmp_path: Path) -> None:
    artifact_id = "job_example"
    directory = tmp_path / "jobs" / artifact_id
    directory.mkdir(parents=True)
    text = "ID 123 Director 甲公司 1 周前 Owns the China business."
    raw = directory / "raw.json"
    normalized = directory / "normalized.txt"
    raw.write_text('{"ok":true}', encoding="utf-8")
    normalized.write_text(text, encoding="utf-8")
    row = {
        "artifact_id": artifact_id,
        "source_platform": "public-test",
        "source_job_id": "123",
        "final_url": "https://example.test/jobs/123",
        "raw_artifact_path": str(raw.relative_to(tmp_path)).replace("\\", "/"),
        "raw_artifact_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "normalized_text_path": str(normalized.relative_to(tmp_path)).replace(
            "\\", "/"
        ),
        "normalized_text_sha256": hashlib.sha256(normalized.read_bytes()).hexdigest(),
        "source_job_id_span": _span(text, "123"),
        "title_span": _span(text, "Director"),
        "employer_span": _span(text, "甲公司"),
        "publication_span": _span(text, "1 周前"),
        "scope_spans": [_span(text, "Owns the China business.")],
        "evaluation_eligible": True,
        "review_status": "pending_human_review",
    }
    (directory / "job-row.json").write_text(
        json.dumps(row, ensure_ascii=False),
        encoding="utf-8",
    )

    manifest = build_manifest(artifact_dir=tmp_path / "jobs", root=tmp_path)

    assert manifest["counts"] == {
        "artifacts": 1,
        "replayable": 1,
        "evaluation_eligible": 1,
        "approved": 0,
        "pending_human_review": 1,
    }
    assert manifest["jobs"][0]["artifact_id"] == artifact_id
