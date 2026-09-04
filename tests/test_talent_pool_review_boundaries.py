import json
import sqlite3
import subprocess

import pytest
from dataclasses import replace

from ht_lead_radar.feishu_notify import load_talent_drafts
from ht_lead_radar.liepin_bridge import (
    ExternalLiepinPublisher,
    FakePublisher,
    publish_approved_serially,
)
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
from ht_lead_radar.talent_pool import canonical_payload_hash
from test_talent_pool import sample_report


def _store(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    bundle = generate_draft_bundle(sample_report())
    store.save_bundle(bundle.to_dict())
    return store, bundle


def test_same_day_new_source_run_hides_and_expires_old_drafts(tmp_path):
    store, first = _store(tmp_path)
    empty_report = sample_report(leads=0)
    empty_report["manifest"]["run_id"] = "run-20260726-rerun"
    second = generate_draft_bundle(empty_report)
    store.save_bundle(second.to_dict())

    assert store.batch(second.run_date, second.direction) == []
    assert load_talent_drafts(
        store.database, run_date=second.run_date, direction=second.direction
    ) == []
    with store._connect() as connection:
        statuses = {
            row[0]
            for row in connection.execute(
                "SELECT status FROM talent_pool_drafts"
            ).fetchall()
        }
    assert statuses == {"expired"}


def test_only_current_command_selection_is_published(tmp_path):
    store, bundle = _store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 2,4",
        actor="older-command",
    )
    current = store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1,3,5",
        actor="current-command",
    )
    publisher = FakePublisher()
    result = publish_approved_serially(
        store,
        run_date=bundle.run_date,
        direction=bundle.direction,
        publisher=publisher,
        draft_ids=current["draft_ids"],
    )
    assert [item["draft_id"] for item in result] == current["draft_ids"]
    rows = store.batch(bundle.run_date, bundle.direction)
    assert rows[1]["status"] == "approved"
    assert rows[3]["status"] == "approved"


def test_arbitrary_direction_is_not_copied_to_public_ad():
    report = sample_report(leads=1)
    report["manifest"]["direction"] = "独角兽一号秘密项目"
    bundle = generate_draft_bundle(report)
    public = json.dumps(
        [draft.public_payload for draft in bundle.drafts], ensure_ascii=False
    )
    assert "独角兽一号" not in public
    assert "秘密项目" not in public
    assert "硬科技" in public


def test_batch_publish_lease_blocks_concurrency_and_crash_residue(tmp_path):
    store, bundle = _store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1",
        actor="user",
    )
    lease = store.acquire_publish_lease(bundle.run_date, bundle.direction)
    with pytest.raises(RuntimeError, match="queue is active"):
        store.acquire_publish_lease(bundle.run_date, bundle.direction)
    store.begin_publish(bundle.drafts[0].draft_id, lease_token=lease)
    store.release_publish_lease(bundle.run_date, bundle.direction, lease)
    with pytest.raises(RuntimeError, match="unresolved publish attempt"):
        store.acquire_publish_lease(bundle.run_date, bundle.direction)


def test_external_bridge_passes_json_file_path_and_detects_partial_pipeline(
    tmp_path, monkeypatch
):
    publish_script = tmp_path / "publish_job.py"
    orchestrate_script = tmp_path / "orchestrate.py"
    runtime = tmp_path / "job_postings.json"
    publish_script.write_text("# fixture", encoding="utf-8")
    orchestrate_script.write_text("# fixture", encoding="utf-8")
    runtime.write_text("[]", encoding="utf-8")
    payload = generate_draft_bundle(sample_report()).drafts[0].public_payload
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        criteria = json.loads(__import__("pathlib").Path(args[2]).read_text("utf-8"))
        if args[1] == str(publish_script):
            assert args[3] == "--no-pipeline"
            assert criteria == payload
            runtime.write_text(
                json.dumps(
                    [{"ejob_id": "12345", "preview_link": "https://liepin/12345"}]
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, "published", "")
        assert criteria["ejob_id"] == "12345"
        return subprocess.CompletedProcess(args, 0, "❌ Step 2 失败", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    bridge = ExternalLiepinPublisher(
        python_bin="python",
        publish_script=publish_script,
        posting_runtime_file=runtime,
        orchestrate_script=orchestrate_script,
        execution_enabled=True,
    )
    result = bridge.publish(payload, full_criteria=payload)

    assert len(calls) == 2
    assert result.success
    assert result.job_id == "12345"
    assert result.blocking
    assert result.error_code == "manual_required"


def test_same_source_run_rerun_uses_exact_current_snapshot_and_keeps_history(tmp_path):
    store, first = _store(tmp_path)
    second = replace(first, drafts=first.drafts[:1], generation_model="minimax/MiniMax-M3")

    store.save_bundle(second.to_dict())

    current_rows = store.batch(second.run_date, second.direction)
    current_drafts = load_talent_drafts(
        store.database,
        run_date=second.run_date,
        direction=second.direction,
        source_run_id=second.source_run_id,
    )
    assert [row["draft_id"] for row in current_rows] == [second.drafts[0].draft_id]
    assert [row["draft_id"] for row in current_drafts] == [second.drafts[0].draft_id]

    current_links = store.find_opportunities(current_only=True)
    history_links = store.find_opportunities(current_only=False)
    assert current_links
    assert {item["draft_id"] for item in current_links} == {
        second.drafts[0].draft_id
    }
    assert len(history_links) > len(current_links)
    assert all(item["company"] for item in history_links)
    assert all(item["company_role"] for item in history_links)
    assert all("position_name" in item["liepin_payload"] for item in history_links)
    with sqlite3.connect(store.database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM talent_pool_bundle_snapshots"
        ).fetchone()[0] == 2


def test_opportunity_history_can_be_retrieved_for_later_float_analysis(tmp_path):
    store, bundle = _store(tmp_path)

    matches = store.find_opportunities(
        terms=[bundle.drafts[0].recommended_title],
        direction=bundle.direction,
    )

    assert matches
    assert matches[0]["recommended_title"] == bundle.drafts[0].recommended_title
    assert matches[0]["evidence_urls"]
    assert matches[0]["liepin_payload"]["position_name"]


def test_draft_that_reappears_after_snapshot_expiry_is_approvable_again(tmp_path):
    store, first = _store(tmp_path)
    without_first = replace(first, drafts=first.drafts[1:])
    store.save_bundle(without_first.to_dict())
    store.save_bundle(first.to_dict())

    rows = store.batch(first.run_date, first.direction)

    assert rows[0]["draft_id"] == first.drafts[0].draft_id
    assert rows[0]["status"] == "pending_approval"


def test_committed_bundle_is_exact_and_mapping_change_invalidates_approval(tmp_path):
    store, bundle = _store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1",
        actor="user",
    )
    changed = bundle.to_dict()
    changed["drafts"][0]["source_leads"][0]["role_hypotheses"] = ["新的具体总监岗位"]

    store.save_bundle(changed)

    committed = store.current_bundle(
        bundle.run_date,
        bundle.direction,
        source_run_id=bundle.source_run_id,
    )
    assert committed is not None
    assert committed["_snapshot_id"]
    assert committed["drafts"][0]["source_leads"][0]["role_hypotheses"] == [
        "新的具体总监岗位"
    ]
    assert store.batch(bundle.run_date, bundle.direction)[0]["status"] == "pending_approval"


def test_publish_attempt_cannot_finish_another_draft(tmp_path):
    store, bundle = _store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1",
        actor="user",
    )
    lease = store.acquire_publish_lease(bundle.run_date, bundle.direction)
    _, attempt_key = store.begin_publish(
        bundle.drafts[0].draft_id,
        lease_token=lease,
    )

    with pytest.raises(ValueError, match="does not belong"):
        store.finish_publish(
            draft_id=bundle.drafts[1].draft_id,
            attempt_key=attempt_key,
            outcome="published",
        )

    assert store.batch(bundle.run_date, bundle.direction)[0]["status"] == "publishing"
    store.finish_publish(
        draft_id=bundle.drafts[0].draft_id,
        attempt_key=attempt_key,
        outcome="published",
        job_id="job-1",
    )
    assert store.batch(bundle.run_date, bundle.direction)[0]["status"] == "published"

def test_existing_database_is_backfilled_into_snapshot_and_opportunity_tables(tmp_path):
    store, bundle = _store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1",
        actor="user",
    )
    with store._connect() as connection:
        connection.execute("DELETE FROM talent_pool_current_snapshots")
        connection.execute("DELETE FROM talent_pool_current_snapshot_drafts")
        connection.execute("DELETE FROM talent_pool_opportunity_links")
        connection.execute("DELETE FROM talent_pool_bundle_snapshots")

    migrated = TalentPoolStore(store.database)

    rows = migrated.batch(bundle.run_date, bundle.direction)
    assert len(rows) == len(bundle.drafts)
    assert rows[0]["status"] == "approved"
    assert migrated.current_bundle(bundle.run_date, bundle.direction) is not None
    assert migrated.find_opportunities(current_only=True)


def test_snapshot_normalizes_payload_hash_before_commit(tmp_path):
    store, bundle = _store(tmp_path)
    changed = bundle.to_dict()
    changed["drafts"][0]["payload_hash"] = "stale"

    store.save_bundle(changed)

    committed = store.current_bundle(bundle.run_date, bundle.direction)
    rows = store.batch(bundle.run_date, bundle.direction)
    assert committed is not None
    assert committed["drafts"][0]["payload_hash"] == rows[0]["payload_hash"]
    assert committed["drafts"][0]["payload_hash"] != "stale"


def test_talent_store_sanitizes_diagnostics_and_urls_but_not_job_payload(tmp_path):
    database = tmp_path / "safe-talent.sqlite"
    store = TalentPoolStore(database)
    bundle = generate_draft_bundle(sample_report(leads=1)).to_dict()
    public_payload = json.loads(
        json.dumps(bundle["drafts"][0]["public_payload"], ensure_ascii=False)
    )
    public_hash = canonical_payload_hash(public_payload)
    bundle["drafts"][0]["source_leads"][0]["evidence_urls"] = [
        "https://user:pass@example.test/a?access_token=url-secret&page=2#fragment"
    ]
    bundle["drafts"][0]["raw_completion"] = {
        "Authorization": "Bearer completion-secret"
    }
    bundle["completion_status"] = {
        "analysis_status": "completed",
        "draft_generation_status": "complete",
        "notification_status": "pending",
        "source_health_status": "healthy",
        "diagnostic": "call +1 415 555 2671 token=diagnostic-secret",
    }

    store.save_bundle(bundle)
    current = store.current_bundle(bundle["run_date"], bundle["direction"])

    assert current is not None
    draft = current["drafts"][0]
    assert current["source_run_id"] == bundle["source_run_id"]
    assert draft["draft_id"] == bundle["drafts"][0]["draft_id"]
    context = store.latest_openclaw_context()
    assert context is not None
    assert context["snapshot_id"] != "[redacted-token]"
    assert draft["public_payload"] == public_payload
    assert draft["payload_hash"] == public_hash
    assert draft["raw_completion"] == "[redacted]"
    assert draft["source_leads"][0]["evidence_urls"] == [
        "https://example.test/a?page=2"
    ]
    with sqlite3.connect(database) as connection:
        dump = "\n".join(connection.iterdump())
        stored_link_payload = json.loads(
            connection.execute(
                "SELECT liepin_payload_json FROM talent_pool_opportunity_links LIMIT 1"
            ).fetchone()[0]
        )
    for secret in (
        "user:pass",
        "url-secret",
        "fragment",
        "completion-secret",
        "diagnostic-secret",
        "415 555 2671",
    ):
        assert secret not in dump
    assert stored_link_payload == public_payload
    assert canonical_payload_hash(stored_link_payload) == public_hash


def test_talent_store_migration_cleans_legacy_operational_fields_only(tmp_path):
    database = tmp_path / "legacy-safe-talent.sqlite"
    store, bundle = _store(tmp_path)
    database = store.database
    original_payload = bundle.drafts[0].public_payload
    original_hash = canonical_payload_hash(original_payload)
    with sqlite3.connect(database) as connection:
        draft_id, raw_draft = connection.execute(
            "SELECT draft_id, draft_json FROM talent_pool_drafts LIMIT 1"
        ).fetchone()
        draft = json.loads(raw_draft)
        draft["raw_completion"] = "Bearer legacy-completion-secret"
        draft["source_leads"][0]["evidence_urls"] = [
            "https://user:pass@legacy.test/a?access_token=legacy-url-secret&page=2"
        ]
        connection.execute(
            "UPDATE talent_pool_drafts SET draft_json=?, last_error_message=?, "
            "liepin_job_url=? WHERE draft_id=?",
            (
                json.dumps(draft, ensure_ascii=False),
                "call 010-87654321 token=legacy-error-secret",
                "https://user:pass@jobs.test/a?token=legacy-job-secret&page=3",
                draft_id,
            ),
        )
        snapshot_id, raw_bundle = connection.execute(
            "SELECT snapshot_id, bundle_json FROM talent_pool_bundle_snapshots LIMIT 1"
        ).fetchone()
        legacy_bundle = json.loads(raw_bundle)
        legacy_bundle["diagnostic"] = (
            "contact +44 20 7946 0958 token=legacy-bundle-secret"
        )
        connection.execute(
            "UPDATE talent_pool_bundle_snapshots SET bundle_json=? "
            "WHERE snapshot_id=?",
            (json.dumps(legacy_bundle, ensure_ascii=False), snapshot_id),
        )
        connection.execute(
            "UPDATE talent_pool_metadata SET value='0' "
            "WHERE key='persistence_sanitizer_version'"
        )

    migrated = TalentPoolStore(database)
    current = migrated.current_bundle(bundle.run_date, bundle.direction)
    assert current is not None
    migrated_draft = next(
        item for item in current["drafts"] if item["draft_id"] == draft_id
    )
    assert migrated_draft["public_payload"] == original_payload
    assert migrated_draft["payload_hash"] == original_hash
    with sqlite3.connect(database) as connection:
        dump = "\n".join(connection.iterdump())
    for secret in (
        "legacy-completion-secret",
        "legacy-url-secret",
        "legacy-error-secret",
        "legacy-job-secret",
        "legacy-bundle-secret",
        "87654321",
        "7946 0958",
    ):
        assert secret not in dump
