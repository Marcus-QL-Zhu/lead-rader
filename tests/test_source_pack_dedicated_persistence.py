from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
    DedicatedCollectionResult,
)
from ht_lead_radar.aggregate_adapters.models import AdapterRun
from ht_lead_radar.models import Evidence
from ht_lead_radar.source_pack_collector import SourcePackCollector
from ht_lead_radar.source_packs import (
    SourceDefinition,
    SourcePack,
    SourcePackRegistry,
)


def _source() -> SourceDefinition:
    return SourceDefinition(
        id="36kr-financing-flash",
        name="36氪—融资快报",
        owner="36氪",
        source_type="financing_media",
        grade="B",
        url="https://pitchhub.36kr.com/financing-flash",
        adapter="html_list",
        signal_types=("funding",),
        industry_tags=("generic", "semiconductor"),
        enabled=True,
        verified_on="2026-07-30",
        status="verified",
        verification_note="test",
    )


def _registry(source: SourceDefinition) -> SourcePackRegistry:
    return SourcePackRegistry(
        version=1,
        verified_on="2026-07-30",
        policy={},
        sources=(source,),
        packs=(
            SourcePack(
                id="generic-cn",
                name="通用",
                aliases=("通用",),
                industry_tags=("generic",),
                source_ids=(source.id,),
            ),
            SourcePack(
                id="semiconductor-cn",
                name="半导体",
                aliases=("半导体", "芯片"),
                industry_tags=("semiconductor",),
                source_ids=(),
            ),
        ),
    )


def _evidence(
    *,
    event_id: str,
    company: str = "测试芯片",
    title: str = "测试芯片完成A轮融资",
    industry_tags: tuple[str, ...] = ("semiconductor",),
) -> Evidence:
    return Evidence(
        company=company,
        event_type="funding",
        phase="build_organize",
        event_date="2026-07-30",
        title=title,
        snippet=title,
        source_url="https://36kr.com/newsflashes/9001",
        source_name="36氪",
        source_grade="B",
        direction="半导体",
        event_id=event_id,
        industry_tags=industry_tags,
    )


def _run(status: str, evidence_count: int) -> AdapterRun:
    now = datetime.now(timezone.utc).isoformat()
    return AdapterRun(
        adapter_id="kr36",
        source_id="36kr-financing-flash",
        started_at=now,
        finished_at=now,
        status=status,
        listing_count=evidence_count,
        incremental_count=evidence_count,
        detail_success_count=evidence_count,
        detail_failure_count=int(status == "partial"),
        rule_event_count=evidence_count,
        minimax_event_count=0,
        evidence_count=evidence_count,
        adaptive_used_count=0,
    )


def test_distinct_same_type_events_from_one_article_are_preserved(tmp_path):
    source = _source()
    with SourcePackCollector(
        registry=_registry(source),
        state_db=tmp_path / "state.sqlite3",
        dedicated_llm_runner=False,
    ) as collector:
        collector._store_evidence(source, "半导体", _evidence(event_id="round-a"))
        collector._store_evidence(source, "半导体", _evidence(event_id="round-b"))

        items = collector.load_recent("半导体", year=2026)

    assert {item.event_id for item in items} == {"round-a", "round-b"}


def test_partial_dedicated_run_merges_and_topic_filter_rejects_noise(
    tmp_path,
    monkeypatch,
):
    source = _source()
    results = iter(
        (
            DedicatedCollectionResult(
                (_evidence(event_id="old"),),
                _run("ok", 1),
            ),
            DedicatedCollectionResult(
                (
                    _evidence(event_id="new"),
                    _evidence(
                        event_id="noise",
                        company="消费品牌",
                        title="消费品牌完成A轮融资",
                        industry_tags=("consumer",),
                    ),
                ),
                _run("partial", 2),
            ),
        )
    )
    monkeypatch.setattr(
        DedicatedAggregateCoordinator,
        "collect_source",
        lambda *_args, **_kwargs: next(results),
    )

    with SourcePackCollector(
        registry=_registry(source),
        state_db=tmp_path / "state.sqlite3",
        dedicated_llm_runner=False,
    ) as collector:
        collector.collect("半导体", year=2026)
        collector.collect("半导体", year=2026)
        items = collector.load_recent("半导体", year=2026)

    assert {item.event_id for item in items} == {"old", "new"}
    assert {item.company for item in items} == {"测试芯片"}


def _create_legacy_evidence_table(connection):
    connection.execute(
        """
        CREATE TABLE source_pack_evidence (
            source_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            topic TEXT NOT NULL,
            company TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_date TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            PRIMARY KEY (source_id, source_url, topic, company, event_type)
        )
        """
    )


def _insert_legacy_evidence(connection, evidence_json):
    connection.execute(
        """
        INSERT INTO source_pack_evidence (
            source_id, source_url, topic, company, event_type, event_date,
            first_seen_at, last_seen_at, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "36kr-financing-flash",
            "https://36kr.com/newsflashes/9001",
            "semiconductor",
            "Legacy Chip",
            "funding",
            "2026-07-30",
            "2026-07-30T00:00:00+00:00",
            "2026-07-30T00:00:00+00:00",
            evidence_json,
        ),
    )
    connection.commit()


def test_bad_legacy_json_leaves_original_table_untouched(tmp_path):
    db = tmp_path / "state.sqlite3"
    with sqlite3.connect(db) as connection:
        _create_legacy_evidence_table(connection)
        _insert_legacy_evidence(connection, "{broken-json")

    with pytest.raises(json.JSONDecodeError):
        SourcePackCollector(
            registry=_registry(_source()),
            state_db=db,
            dedicated_llm_runner=False,
        )

    with sqlite3.connect(db) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        count = connection.execute(
            "SELECT COUNT(*) FROM source_pack_evidence"
        ).fetchone()[0]
    assert "source_pack_evidence_legacy" not in tables
    assert count == 1


def test_interrupted_legacy_table_is_recovered_on_next_start(tmp_path):
    db = tmp_path / "state.sqlite3"
    payload = asdict(
        _evidence(
            event_id="",
            company="Legacy Chip",
            title="Legacy Chip completed Series A funding",
            industry_tags=("semiconductor",),
        )
    )
    payload["direction"] = "semiconductor"
    with sqlite3.connect(db) as connection:
        _create_legacy_evidence_table(connection)
        _insert_legacy_evidence(
            connection,
            json.dumps(payload, ensure_ascii=False),
        )
        connection.execute(
            "ALTER TABLE source_pack_evidence RENAME TO source_pack_evidence_legacy"
        )
        connection.execute(
            """
            CREATE TABLE source_pack_evidence (
                source_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                topic TEXT NOT NULL,
                company TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_date TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                PRIMARY KEY (
                    source_id, source_url, topic, company, event_type, event_id
                )
            )
            """
        )
        connection.commit()

    with SourcePackCollector(
        registry=_registry(_source()),
        state_db=db,
        dedicated_llm_runner=False,
    ) as collector:
        items = collector.load_recent("semiconductor", year=2026)

    with sqlite3.connect(db) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "source_pack_evidence_legacy" not in tables
    assert len(items) == 1
    assert items[0].event_id
