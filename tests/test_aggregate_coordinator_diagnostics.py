import json

from ht_lead_radar.aggregate_adapters.coordinator import _bounded_dead_letter_error
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.storage import AggregateStateStore


def test_semantic_dead_letter_uses_audit_diagnostics_when_error_is_empty():
    value = _bounded_dead_letter_error(
        "",
        semantic_audit={
            "validation_issues": ["claim subject missing", "date unsupported"],
            "rejection_reason_counts": {"unmapped_subject": 2},
        },
    )

    assert "validation_issues=claim subject missing; date unsupported" in value
    assert 'rejection_reason_counts={"unmapped_subject": 2}' in value


def test_dead_letter_diagnostic_is_bounded_nonempty_and_redacts_secret_fragments():
    value = _bounded_dead_letter_error("token=private-value " + "x" * 3000)

    assert value
    assert len(value) == 2000
    assert "private-value" not in value
    assert "token=[redacted]" in value


def test_sqlite_and_semantic_trace_never_persist_raw_model_or_nested_secrets(tmp_path):
    state = tmp_path / "state.sqlite3"
    secret_values = ("raw-model-secret", "json-secret", "query-secret", "password-secret")
    audit = {
        "source_id": "source",
        "source_article_id": "article",
        "prompt_version": "v1",
        "model_identity": "minimax/test",
        "status": "partial",
        "error": "password=password-secret url=https://x.test/?access_token=query-secret",
        "validation_issues": ['{"access_token":"json-secret"}'],
        "first_response": "raw-model-secret",
        "repair_response": "raw-model-secret",
        "nested": {"password": "password-secret"},
    }
    with AggregateStateStore(state) as store:
        store.store_semantic_audit(audit)
        store.record_dead_letter(
            source_id="source",
            source_article_id="article",
            canonical_url=(
                "https://user:pass@x.test/a?access_token=query-secret&page=2#private"
            ),
            stage="semantic_validation",
            error=(
                "Authorization: Bearer raw-model-secret password=password-secret "
                "call +44 20 7946 0958 or 010-87654321"
            ),
        )
        semantic = store.connection.execute(
            "SELECT * FROM aggregate_semantic_attempts"
        ).fetchone()
        dead_letter = store.connection.execute(
            "SELECT * FROM aggregate_dead_letters"
        ).fetchone()

    persisted = json.dumps(
        {"semantic": dict(semantic), "dead_letter": dict(dead_letter)},
        ensure_ascii=False,
    )
    assert all(secret not in persisted for secret in secret_values)
    assert semantic["first_response"] == ""
    assert semantic["repair_response"] == ""
    assert dead_letter["canonical_url"] == "https://x.test/a?page=2"
    assert "7946 0958" not in dead_letter["error"]
    assert "87654321" not in dead_letter["error"]

    acceptance = tmp_path / "acceptance"
    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "trace.sqlite3",
        acceptance_dir=acceptance,
    )
    coordinator._write_semantic(
        "source",
        "article",
        {
            "canonical_url": "https://x.test/?access_token=query-secret",
            "clean_body": "contact marcus@example.com or 13800138000 id 11010519491231002X",
        },
        [{"metadata": {"password": "password-secret", "contact": "marcus@example.com"}}],
        audit,
    )
    trace = next(acceptance.rglob("semantic-article.json")).read_text(encoding="utf-8")
    assert all(secret not in trace for secret in secret_values)
    assert "first_response" not in trace
    assert "marcus@example.com" not in trace
    assert "13800138000" not in trace
    assert "11010519491231002X" not in trace


def test_legacy_semantic_raw_responses_are_purged_by_one_time_migration(tmp_path):
    state = tmp_path / "legacy.sqlite3"
    with AggregateStateStore(state) as store:
        store.connection.execute(
            """
            INSERT INTO aggregate_semantic_attempts (
                source_id, source_article_id, prompt_version, attempted_at,
                status, validation_error, first_response, repair_response, audit_json
            ) VALUES ('s', 'a', 'v', 'now', 'partial', ?, ?, ?, ?)
            """,
            (
                "email=marcus@example.com phone=13800138000 id=11010519491231002X",
                "legacy-secret",
                "legacy-secret",
                json.dumps(
                    {
                        "source_id": "s",
                        "source_article_id": "a",
                        "prompt_version": "v",
                        "status": "partial",
                        "first_response": "legacy-secret",
                    }
                ),
            ),
        )
        store.connection.execute(
            "UPDATE aggregate_metadata SET value='1' WHERE key='audit_sanitizer_version'"
        )
        store.connection.execute(
            "INSERT INTO aggregate_company_aliases(source_id, source_article_id, "
            "alias_key, alias, canonical_key, canonical_company, evidence_quote, "
            "recorded_at) VALUES ('s', 'a', 'alias', '甲公司', 'canonical', "
            "'甲公司', ?, 'now')",
            ("contact 010 / 87654321 token=alias-secret",),
        )
        store.connection.execute(
            """
            INSERT INTO aggregate_runs (
                adapter_id, source_id, started_at, finished_at, status, run_json
            ) VALUES ('a', 's', 'now', 'now', 'error', ?)
            """,
            (
                json.dumps(
                    {
                        "error": "marcus@example.com 13800138000 11010519491231002X"
                    }
                ),
            ),
        )
        store.connection.commit()

    with AggregateStateStore(state) as store:
        row = store.connection.execute(
            "SELECT validation_error, first_response, repair_response, audit_json "
            "FROM aggregate_semantic_attempts"
        ).fetchone()
    assert "legacy-secret" not in " ".join(str(value) for value in row)
    rendered = " ".join(str(value) for value in row)
    assert "marcus@example.com" not in rendered
    assert "13800138000" not in rendered
    assert "11010519491231002X" not in rendered
    with AggregateStateStore(state) as store:
        quote = store.connection.execute(
            "SELECT evidence_quote FROM aggregate_company_aliases"
        ).fetchone()[0]
    assert "87654321" not in quote
    assert "alias-secret" not in quote
    with AggregateStateStore(state) as store:
        run_json = store.connection.execute(
            "SELECT run_json FROM aggregate_runs"
        ).fetchone()[0]
    assert "marcus@example.com" not in run_json
    assert "13800138000" not in run_json
    assert "11010519491231002X" not in run_json
