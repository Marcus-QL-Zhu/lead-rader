#!/usr/bin/env python3
"""Materialize heterogeneous archived adapter snapshots for V27 acceptance."""

from __future__ import annotations

import argparse
from dataclasses import fields
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from ht_lead_radar.aggregate_adapters.models import SourceArticleIndex


_INDEX_FIELDS = {field.name for field in fields(SourceArticleIndex)}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _resolve(value: Any, dotted_path: str) -> Any:
    if not dotted_path:
        return value
    current = value
    for token in dotted_path.split("."):
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"path component is missing: {dotted_path}")
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            try:
                current = current[int(token)]
            except IndexError as exc:
                raise ValueError(f"list index is missing: {dotted_path}") from exc
        else:
            raise ValueError(f"cannot descend into value: {dotted_path}")
    return current


def _as_rows(value: Any, *, path: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and not path:
        return [value]
    raise ValueError(f"expected a list at {path or '<root>'}")


def _event_rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("events"), list):
            return [dict(row) for row in value["events"] if isinstance(row, dict)]
        return [dict(value)]
    raise ValueError("events must be a list or object")


def _group_events(value: Any) -> dict[str, list[dict[str, Any]]]:
    """Group both flat event lists and *_by_article wrapper lists."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    if value is None:
        return grouped
    if isinstance(value, dict):
        if "source_article_id" in value and "events" in value:
            key = str(value["source_article_id"])
            grouped[key] = _event_rows(value.get("events"))
            return grouped
        for key, rows in value.items():
            grouped[str(key)] = _event_rows(rows)
        return grouped
    if not isinstance(value, list):
        raise ValueError("global events must be a list or object")
    for row in value:
        if isinstance(row, list):
            for nested in row:
                if not isinstance(nested, dict):
                    continue
                key = str(nested.get("source_article_id") or "")
                if key:
                    grouped.setdefault(key, []).append(dict(nested))
            continue
        if not isinstance(row, dict):
            continue
        if "events" in row and "source_article_id" in row:
            key = str(row["source_article_id"])
            grouped.setdefault(key, []).extend(_event_rows(row.get("events")))
            continue
        key = str(row.get("source_article_id") or "")
        if key:
            grouped.setdefault(key, []).append(dict(row))
    return grouped


def _index_map(value: Any) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    rows = value if isinstance(value, list) else list(value.values()) if isinstance(value, dict) else []
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        candidate = raw.get("index") if isinstance(raw.get("index"), dict) else raw
        article_id = candidate.get("source_article_id")
        if article_id is not None:
            result[str(article_id)] = dict(candidate)
    return result


def _article_payload(raw: Any, indexes: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(raw, dict):
        raise ValueError("article row must be an object")
    item_events = raw.get("rule_events")
    if item_events is None:
        item_events = raw.get("events")
    article = raw.get("article") if isinstance(raw.get("article"), dict) else raw
    article = dict(article)
    if not isinstance(article.get("index"), dict):
        article_id = str(article.get("source_article_id") or "")
        base_index = indexes.get(article_id)
        if not base_index:
            raise ValueError(f"article has no SourceArticleIndex: {article_id}")
        article["index"] = dict(base_index)
    article["index"] = {
        key: value for key, value in dict(article["index"]).items() if key in _INDEX_FIELDS
    }
    return article, _event_rows(item_events)


def materialize(
    payload: Any,
    *,
    source_id: str,
    articles_path: str,
    events_path: str,
    indexes_path: str,
    output_root: Path,
    input_path: Path,
    events_payload: Any | None = None,
    events_input_path: Path | None = None,
    force: bool = False,
    skip_other_sources: bool = False,
) -> dict[str, Any]:
    article_rows = _as_rows(_resolve(payload, articles_path), path=articles_path)
    indexes = _index_map(_resolve(payload, indexes_path)) if indexes_path else {}
    events_source = payload if events_payload is None else events_payload
    global_events = _group_events(_resolve(events_source, events_path)) if events_path else {}
    source_dir = output_root / source_id
    source_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    skipped_source_count = 0
    for raw in article_rows:
        article, item_events = _article_payload(raw, indexes)
        index = article["index"]
        actual_source = str(index.get("source_id") or "")
        article_id = str(index.get("source_article_id") or "")
        if actual_source != source_id:
            if skip_other_sources:
                skipped_source_count += 1
                continue
            raise ValueError(f"source mismatch for {article_id}: {actual_source!r}")
        if not article_id:
            raise ValueError("article has an empty source_article_id")
        content_hash = str(article.get("content_hash") or "")
        if article_id in seen and seen[article_id] != content_hash:
            raise ValueError(f"duplicate article content mismatch: {article_id}")
        seen[article_id] = content_hash
        events = item_events or global_events.get(article_id, [])
        output_path = source_dir / f"semantic-{article_id}.json"
        if output_path.exists() and not force:
            existing = _read_json(output_path)
            existing_hash = str((existing.get("article") or {}).get("content_hash") or "")
            if existing_hash != content_hash:
                raise ValueError(f"output content mismatch: {output_path}")
            continue
        wrapper = {
            "schema_version": 1,
            "purpose": "materialized-dedicated-adapter-semantic-v27-input",
            "article": article,
            "events": events,
            "minimax_audit": {
                "model_identity": "rules-only",
                "materialized_from": str(input_path),
            },
        }
        output_path.write_text(
            json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append({"source_article_id": article_id, "path": str(output_path), "event_count": len(events)})
    return {
        "source_id": source_id,
        "article_count": len(seen),
        "skipped_source_count": skipped_source_count,
        "written_count": len(written),
        "event_count": sum(int(row["event_count"]) for row in written),
        "input": str(input_path),
        "input_sha256": sha256(input_path.read_bytes()).hexdigest(),
        "events_input": str(events_input_path) if events_input_path else None,
        "events_input_sha256": (
            sha256(events_input_path.read_bytes()).hexdigest()
            if events_input_path
            else None
        ),
        "output_root": str(output_root),
        "articles": written,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--articles-path", default="")
    parser.add_argument("--events-path", default="")
    parser.add_argument("--events-input", type=Path)
    parser.add_argument("--indexes-path", default="")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--skip-other-sources",
        action="store_true",
        help="skip mixed-input rows belonging to another source_id",
    )
    args = parser.parse_args()
    result = materialize(
        _read_json(args.input),
        source_id=args.source_id,
        articles_path=args.articles_path,
        events_path=args.events_path,
        indexes_path=args.indexes_path,
        output_root=args.output_root,
        input_path=args.input,
        events_payload=_read_json(args.events_input) if args.events_input else None,
        events_input_path=args.events_input,
        force=args.force,
        skip_other_sources=args.skip_other_sources,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
