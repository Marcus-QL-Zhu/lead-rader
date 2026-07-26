import sqlite3

import pytest

from ht_lead_radar.liepin_bridge import (
    FakePublisher,
    PublishResult,
    publish_approved_serially,
)
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore, parse_approval_command
from test_talent_pool import sample_report


def _approved_store(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    bundle = generate_draft_bundle(sample_report())
    store.save_bundle(bundle.to_dict())
    selected = store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1",
        actor="review",
    )
    return store, bundle, selected["draft_ids"]


def test_lease_is_global_across_dates_and_directions(tmp_path):
    store, bundle, _ = _approved_store(tmp_path)
    lease = store.acquire_publish_lease(bundle.run_date, bundle.direction)
    with pytest.raises(RuntimeError, match="queue is active"):
        store.acquire_publish_lease("2099-01-01", "另一个方向")
    store.release_publish_lease(bundle.run_date, bundle.direction, lease)


def test_failed_retry_appends_attempt_history_with_source_run(tmp_path):
    store, bundle, draft_ids = _approved_store(tmp_path)
    publish_approved_serially(
        store,
        run_date=bundle.run_date,
        direction=bundle.direction,
        publisher=FakePublisher(
            [PublishResult(False, error_code="field_error", error_message="bad")]
        ),
        draft_ids=draft_ids,
    )
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1",
        actor="review-again",
    )
    publish_approved_serially(
        store,
        run_date=bundle.run_date,
        direction=bundle.direction,
        publisher=FakePublisher(),
        draft_ids=draft_ids,
    )
    with sqlite3.connect(store.database) as connection:
        attempts = connection.execute(
            """
            SELECT attempt_key, source_run_id, outcome
            FROM talent_pool_publish_attempts ORDER BY id
            """
        ).fetchall()
    assert len(attempts) == 2
    assert attempts[0][0] != attempts[1][0]
    assert [item[1] for item in attempts] == [bundle.source_run_id] * 2
    assert [item[2] for item in attempts] == ["failed", "published"]


def test_unexpected_publisher_exception_releases_global_lease(tmp_path):
    store, bundle, draft_ids = _approved_store(tmp_path)

    class RaisingPublisher:
        def publish(self, payload, *, full_criteria):
            raise RuntimeError("unexpected publisher exception")

    with pytest.raises(RuntimeError, match="unexpected publisher"):
        publish_approved_serially(
            store,
            run_date=bundle.run_date,
            direction=bundle.direction,
            publisher=RaisingPublisher(),
            draft_ids=draft_ids,
        )
    with sqlite3.connect(store.database) as connection:
        released_at = connection.execute(
            "SELECT released_at FROM talent_pool_publish_leases "
            "WHERE lease_key='liepin-account'"
        ).fetchone()[0]
    assert released_at


def test_selected_indexes_are_normalized_to_display_order():
    command = parse_approval_command("发布 5,1", draft_count=5)
    assert command is not None
    assert command.indexes == (1, 5)
