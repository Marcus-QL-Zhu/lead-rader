from __future__ import annotations

import json
import sqlite3

from ht_lead_radar.domain import (
    CanonicalEntity,
    EntityJudgement,
    EventLifecycle,
    EventLinkType,
    EvidenceStance,
    SourceDocument,
    Statement,
    normalize_url,
    sha256_text,
)
from ht_lead_radar.fact_store import FactStore, SCHEMA_VERSION
from ht_lead_radar.models import Evidence


def _document(
    url: str,
    *,
    source: str = "媒体",
    grade: str = "B",
    content: str = "曦诺未来完成A轮融资",
    observed: str = "2026-07-25T01:00:00Z",
    source_key: str = "",
) -> SourceDocument:
    return SourceDocument.create(
        source_name=source,
        source_url=url,
        title=content,
        content=content,
        source_grade=grade,
        published_at="2026-07-20",
        observed_at=observed,
        independent_source_key=source_key,
    )


def _claim(
    store: FactStore,
    company_id: str,
    document: SourceDocument,
    *,
    slots=None,
    event_type: str = "financing",
    event_date: str = "2026-07-20",
    stance: EvidenceStance = EvidenceStance.SUPPORTS,
):
    document, _ = store.upsert_document(document)
    statement = Statement.create(
        document_id=document.id,
        predicate=event_type,
        subject_entity_id=company_id,
        occurred_at=event_date,
        quote=document.content,
        slots=slots or {"round": "A"},
    )
    statement, _ = store.upsert_statement(statement)
    event, created = store.cluster_event(
        company_entity_id=company_id,
        event_type=event_type,
        occurred_at=event_date,
        slots=slots or {"round": "A"},
        observed_at=document.observed_at,
    )
    store.link_event_evidence(
        event.id, document.id, statement_id=statement.id, stance=stance
    )
    return store.get_event(event.id), document, statement, created


def test_url_normalization_exact_hash_and_immutable_document_versions(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    url = "HTTPS://Example.COM:443//news/../news/a?utm_source=x&b=2&a=1#part"
    assert normalize_url(url) == "https://example.com/news/a?a=1&b=2"

    original = _document(url, content="same exact bytes")
    stored, created = store.upsert_document(original)
    assert created
    assert stored.normalized_url == "https://example.com/news/a?a=1&b=2"
    assert stored.url_hash == sha256_text(stored.normalized_url)
    assert stored.content_hash == sha256_text("same exact bytes")

    repeated, created = store.upsert_document(original)
    assert not created
    assert repeated.id == stored.id

    changed, created = store.upsert_document(
        _document(url, content="same exact bytes ")
    )
    assert created
    assert changed.id != stored.id
    assert len(store.find_documents(url=url)) == 2

    mirror, created = store.upsert_document(
        _document("https://mirror.example/story", content="same exact bytes")
    )
    assert created
    assert mirror.exact_duplicate_of_id == stored.id
    assert len(store.find_documents(content_hash=stored.content_hash)) == 2


def test_fact_store_never_persists_url_userinfo_tokens_or_secret_metadata(tmp_path):
    database = tmp_path / "safe-facts.sqlite"
    store = FactStore(database)
    document = SourceDocument.create(
        source_name="测试",
        source_url=(
            "https://user:pass@example.com/a?access_token=url-secret&page=2#private"
        ),
        title="融资",
        content="融资事实",
        metadata={"headers": {"Authorization": "Bearer metadata-secret"}},
    )

    stored, _ = store.upsert_document(document)

    assert stored.source_url == "https://example.com/a?page=2"
    assert stored.normalized_url == "https://example.com/a?page=2"
    assert stored.metadata["headers"] == "[redacted]"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT source_url, normalized_url, metadata_json FROM source_documents"
        ).fetchone()
    assert "url-secret" not in " ".join(row)
    assert "metadata-secret" not in " ".join(row)


def test_fact_store_v3_migration_recleans_legacy_urls_metadata_and_pii(tmp_path):
    database = tmp_path / "legacy-facts.sqlite"
    store = FactStore(database)
    store.upsert_document(
        SourceDocument.create(
            source_name="测试",
            source_url="https://example.test/a?page=2",
            title="融资",
            content="融资事实",
        )
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_documents SET source_url=?, normalized_url=?, "
            "metadata_json=?",
            (
                "https://user:pass@example.test/a?X-Amz-Credential=cred-secret"
                "&X-Amz-Signature=signature-secret&page=2#private-fragment",
                "https://user:pass@example.test/a?token=url-secret&page=2",
                json.dumps(
                    {
                        "contact": "0086 / 21 / 61234567",
                        "Config.FEISHU_APP_SECRET": "metadata-secret",
                    }
                ),
            ),
        )
        connection.execute(
            "UPDATE fact_store_metadata SET value='2' "
            "WHERE key='persistence_sanitizer_version'"
        )

    FactStore(database)
    with sqlite3.connect(database) as connection:
        dump = "\n".join(connection.iterdump())

    for unsafe in (
        "user:pass",
        "cred-secret",
        "signature-secret",
        "url-secret",
        "private-fragment",
        "61234567",
        "metadata-secret",
    ):
        assert unsafe not in dump


def test_schema_migration_is_idempotent(tmp_path):
    database = tmp_path / "facts.sqlite"
    FactStore(database)
    FactStore(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == SCHEMA_VERSION


def test_entities_aliases_and_resolution_are_non_destructive_and_reversible(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    first, first_created = store.upsert_entity(
        CanonicalEntity.create("company", "曦诺未来")
    )
    repeated, repeated_created = store.get_or_create_entity("company", "曦诺未来")
    legal, _ = store.get_or_create_entity("company", "上海曦诺未来智能科技有限公司")
    assert first_created and not repeated_created
    assert first.id == repeated.id

    store.add_entity_alias(first.id, "Xinuo Future", alias_type="english")
    assert store.find_entities("Xinuo Future")[0].id == first.id

    positive = store.judge_entities(
        first.id,
        legal.id,
        EntityJudgement.POSITIVE,
        reason="官网工商主体",
        actor="analyst",
    )
    assert store.get_positive_entity_cluster(first.id) == {first.id, legal.id}
    negative = store.judge_entities(
        first.id,
        legal.id,
        EntityJudgement.NEGATIVE,
        reason="人工复核为不同主体",
        actor="analyst",
    )
    assert positive.id != negative.id
    assert store.get_positive_entity_cluster(first.id) == {first.id}
    history = store.entity_judgement_history(first.id, legal.id)
    assert [item.judgement for item in history] == ["POSITIVE", "NEGATIVE"]
    assert history[0].revoked_at is not None
    assert store.get_entity(first.id) is not None
    assert store.get_entity(legal.id) is not None


def test_statements_are_content_addressed_and_queryable(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    company, _ = store.get_or_create_entity("company", "曦诺未来")
    document, _ = store.upsert_document(_document("https://one.example/a"))
    statement = Statement.create(
        document_id=document.id,
        predicate="financing",
        subject_entity_id=company.id,
        occurred_at="2026-07-20",
        confidence=0.91,
        quote="完成A轮融资",
        slots={"round": "A", "investors": ["甲资本", "乙资本"]},
    )
    inserted, created = store.upsert_statement(statement)
    repeated, repeated_created = store.upsert_statement(statement)
    assert created and not repeated_created
    assert inserted.id == repeated.id
    assert store.get_statement(statement.id).slots["round"] == "A"
    assert store.list_statements(
        document_id=document.id, subject_entity_id=company.id
    )[0].id == statement.id


def test_event_clustering_canonical_evidence_and_independent_support(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    company, _ = store.get_or_create_entity("company", "曦诺未来")

    first_event, first_doc, _, created = _claim(
        store,
        company.id,
        _document(
            "https://media-one.example/a",
            source="媒体一",
            grade="B",
            source_key="media-group-one",
        ),
        slots={"round": "A"},
    )
    assert created
    assert first_event.lifecycle == "emerging"
    assert first_event.independent_source_count == 1

    # A second page owned by the same media group is corroborating evidence,
    # but it is not an independent source group.
    same_group_event, _, _, created = _claim(
        store,
        company.id,
        _document(
            "https://media-one.example/b",
            source="媒体一子站",
            grade="C",
            source_key="media-group-one",
            content="曦诺未来A轮融资详情",
        ),
        slots={"round": "A"},
        event_date="2026-07-22",
    )
    assert not created
    assert same_group_event.id == first_event.id
    assert same_group_event.independent_source_count == 1

    official_event, official_doc, _, created = _claim(
        store,
        company.id,
        _document(
            "https://xinuo.example/news/a",
            source="曦诺未来官网",
            grade="A",
            source_key="xinuo-official",
            content="曦诺未来宣布完成A轮融资",
        ),
        slots={"round": "A"},
        event_date="2026-07-21",
    )
    assert not created
    assert official_event.id == first_event.id
    assert official_event.independent_source_count == 2
    assert official_event.lifecycle == "corroborated"
    assert official_event.lifecycle_reason == "at least two independent source groups"
    assert official_event.canonical_document_id == official_doc.id
    assert store.get_canonical_document(first_event.id).id == official_doc.id
    assert [item.source_grade for item in store.get_event_evidence(first_event.id)][0] == "A"
    assert len(store.get_event_documents(first_event.id)) == 3
    assert len(store.get_event_statements(first_event.id)) == 3


def test_event_cluster_uses_company_type_time_and_identifying_slots(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    first_company, _ = store.get_or_create_entity("company", "甲公司")
    second_company, _ = store.get_or_create_entity("company", "乙公司")
    doc, _ = store.upsert_document(_document("https://source.example/1"))

    event_a, created_a = store.cluster_event(
        company_entity_id=first_company.id,
        event_type="financing",
        occurred_at="2026-07-01",
        slots={"round": "A", "amount": "1亿元"},
        observed_at=doc.observed_at,
    )
    event_a_again, created_again = store.cluster_event(
        company_entity_id=first_company.id,
        event_type="financing",
        occurred_at="2026-07-05",
        slots={"round": "A", "lead_investor": "甲资本"},
        observed_at=doc.observed_at,
    )
    event_b, created_b = store.cluster_event(
        company_entity_id=first_company.id,
        event_type="financing",
        occurred_at="2026-07-06",
        slots={"round": "B"},
        observed_at=doc.observed_at,
    )
    other_company_event, created_other = store.cluster_event(
        company_entity_id=second_company.id,
        event_type="financing",
        occurred_at="2026-07-05",
        slots={"round": "A"},
        observed_at=doc.observed_at,
    )
    capacity_event, created_capacity = store.cluster_event(
        company_entity_id=first_company.id,
        event_type="factory_or_capacity",
        occurred_at="2026-07-05",
        slots={"location": "上海"},
        observed_at=doc.observed_at,
    )

    assert created_a and not created_again
    assert event_a_again.id == event_a.id
    assert event_a_again.slots["lead_investor"] == "甲资本"
    assert created_b and event_b.id != event_a.id
    assert created_other and other_company_event.id != event_a.id
    assert created_capacity and capacity_event.id != event_a.id
    assert len(store.event_revisions(event_a.id)) == 2


def test_lifecycle_developing_stale_disputed_retracted_and_manual(
    tmp_path,
    monkeypatch,
):
    # Keep the pre-stale assertions independent of the wall clock. The stale
    # transition itself is exercised explicitly with ``as_of`` below.
    monkeypatch.setattr(
        "ht_lead_radar.fact_store.utcnow",
        lambda: "2026-07-08T00:00:00Z",
    )
    store = FactStore(tmp_path / "facts.sqlite", stale_after_days=30)
    company, _ = store.get_or_create_entity("company", "甲公司")
    event, _, _, _ = _claim(
        store,
        company.id,
        _document(
            "https://one.example/a",
            source_key="one",
            observed="2026-07-01T00:00:00Z",
        ),
        slots={"round": "A"},
        event_date="2026-01-01",
    )
    # Adding a new structured slot creates a revision; a second source then
    # changes corroborated to developing.
    event, second_doc, second_statement, _ = _claim(
        store,
        company.id,
        _document(
            "https://two.example/a",
            source_key="two",
            observed="2026-07-08T00:00:00Z",
            content="甲公司A轮融资由乙资本领投",
        ),
        slots={"round": "A", "lead_investor": "乙资本"},
        event_date="2026-01-02",
    )
    assert event.lifecycle == "developing"

    stale = store.refresh_event(event.id, as_of="2026-09-01T00:00:00Z")
    assert stale.lifecycle == "stale"

    contradiction_doc, _ = store.upsert_document(
        _document(
            "https://three.example/a",
            source_key="three",
            content="甲公司否认完成A轮融资",
            observed="2026-09-02T00:00:00Z",
        )
    )
    contradiction = Statement.create(
        document_id=contradiction_doc.id,
        predicate="financing",
        subject_entity_id=company.id,
        occurred_at="2026-01-02",
        quote="否认完成融资",
        slots={"round": "A"},
    )
    contradiction, _ = store.upsert_statement(contradiction)
    store.link_event_evidence(
        event.id,
        contradiction_doc.id,
        statement_id=contradiction.id,
        stance=EvidenceStance.CONTRADICTS,
    )
    assert store.get_event(event.id).lifecycle == "disputed"

    retraction_doc, _ = store.upsert_document(
        _document(
            "https://official.example/retraction",
            grade="A",
            source_key="official",
            content="更正：此前融资消息撤回",
            observed="2026-09-03T00:00:00Z",
        )
    )
    store.link_event_evidence(
        event.id, retraction_doc.id, stance=EvidenceStance.RETRACTS
    )
    assert store.get_event(event.id).lifecycle == "retracted"

    manual = store.set_event_lifecycle(
        event.id,
        EventLifecycle.DISPUTED,
        reason="人工认为撤回事实仍有争议",
        actor="analyst",
    )
    assert manual.lifecycle == "disputed"
    assert store.refresh_event(event.id, as_of="2027-01-01").lifecycle == "disputed"
    assert len(store.lifecycle_history(event.id)) >= 5


def test_event_merge_and_supersession_judgements_are_reversible(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    company, _ = store.get_or_create_entity("company", "甲公司")
    first, _ = store.cluster_event(
        company_entity_id=company.id,
        event_type="financing",
        occurred_at="2026-01-01",
        slots={"round": "A"},
    )
    second, _ = store.cluster_event(
        company_entity_id=company.id,
        event_type="financing",
        occurred_at="2026-04-01",
        slots={"round": "B"},
    )
    merge = store.judge_event_link(
        first.id,
        second.id,
        EventLinkType.MERGE,
        EntityJudgement.POSITIVE,
        reason="人工判断同一轮融资",
    )
    assert store.get_positive_event_cluster(first.id) == {first.id, second.id}
    split = store.judge_event_link(
        first.id,
        second.id,
        EventLinkType.MERGE,
        EntityJudgement.NEGATIVE,
        reason="发现轮次不同，撤销合并",
    )
    assert merge.id != split.id
    assert store.get_positive_event_cluster(first.id) == {first.id}
    merge_history = store.event_link_history(
        first.id, second.id, link_type=EventLinkType.MERGE
    )
    assert merge_history[0].revoked_at is not None

    split_decision = store.judge_event_link(
        first.id,
        second.id,
        EventLinkType.SPLIT,
        EntityJudgement.POSITIVE,
        reason="明确是A轮和B轮",
    )
    assert split_decision.link_type == "split"
    store.judge_event_link(
        first.id,
        second.id,
        EventLinkType.MERGE,
        EntityJudgement.POSITIVE,
        reason="新证据重新支持合并",
    )
    assert store.event_link_history(
        second.id, first.id, link_type=EventLinkType.SPLIT
    )[0].revoked_at is not None
    assert len(store.event_link_history(second.id, first.id)) >= 4
    store.judge_event_link(
        first.id,
        second.id,
        EventLinkType.MERGE,
        EntityJudgement.NEGATIVE,
        reason="最终保持分离",
    )

    store.judge_event_link(
        first.id,
        second.id,
        EventLinkType.SUPERSEDES,
        EntityJudgement.POSITIVE,
        reason="B轮取代旧融资事件",
    )
    assert store.get_event(first.id).lifecycle == "superseded"
    store.judge_event_link(
        first.id,
        second.id,
        EventLinkType.SUPERSEDES,
        EntityJudgement.NO_JUDGEMENT,
        reason="撤销该判断",
    )
    assert store.get_event(first.id).lifecycle == "emerging"


def test_legacy_evidence_ingest_is_idempotent_and_queryable(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite")
    evidence = Evidence(
        company="曦诺未来",
        event_type="financing",
        phase="strategy_capital",
        event_date="2026-07-20",
        title="曦诺未来完成A轮融资",
        snippet="本轮由甲资本领投。",
        source_url="https://example.com/a?utm_source=feed",
        source_name="投资界",
        source_grade="B",
        direction="灵巧手",
        people=("张三",),
        organizations=("甲资本",),
    )
    first = store.ingest_legacy_evidence(evidence)
    repeated = store.ingest_legacy_evidence(evidence)

    assert first.created_document
    assert first.created_entity
    assert first.created_statement
    assert first.created_event
    assert not repeated.created_document
    assert not repeated.created_entity
    assert not repeated.created_statement
    assert not repeated.created_event
    assert repeated.event.id == first.event.id
    assert len(store.list_events(company_name="曦诺未来")) == 1
    assert len(store.get_event_documents(first.event.id)) == 1
    assert len(store.get_event_statements(first.event.id)) == 1
    assert store.find_documents(url="https://example.com/a")[0].id == first.document.id
