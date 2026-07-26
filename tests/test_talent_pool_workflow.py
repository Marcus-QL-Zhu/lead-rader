import json
import sqlite3

import pytest

from ht_lead_radar.liepin_bridge import (
    FakePublisher,
    PublishResult,
    publish_approved_serially,
)
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import (
    TalentPoolStore,
    parse_approval_command,
)
from test_talent_pool import sample_report


def seeded_store(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    bundle = generate_draft_bundle(sample_report())
    store.save_bundle(bundle.to_dict())
    return store, bundle


def test_only_exact_commands_are_accepted():
    assert parse_approval_command("发布全部", draft_count=5).indexes == (1, 2, 3, 4, 5)
    assert parse_approval_command("发布 1,3,5", draft_count=5).indexes == (1, 3, 5)
    assert parse_approval_command("跳过全部", draft_count=5).action == "reject"
    assert parse_approval_command("查看 2 的完整广告 JSON", draft_count=5).action == "view"
    for fuzzy in ("可以", "发吧", "发布1,3", "发布 1，3", "发布 0", "发布 1,1"):
        assert parse_approval_command(fuzzy, draft_count=5) is None


def test_approval_records_actor_time_original_command_and_selection(tmp_path):
    store, bundle = seeded_store(tmp_path)
    result = store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1,3,5",
        actor="ou-user",
    )
    assert result["draft_ids"] == [
        bundle.drafts[0].draft_id,
        bundle.drafts[2].draft_id,
        bundle.drafts[4].draft_id,
    ]
    rows = store.batch(bundle.run_date, bundle.direction)
    assert [row["status"] for row in rows] == [
        "approved",
        "pending_approval",
        "approved",
        "pending_approval",
        "approved",
    ]
    assert rows[0]["approved_by"] == "ou-user"
    assert rows[0]["approved_at"]
    assert rows[0]["approval_command"] == "发布 1,3,5"


def test_view_and_reject_never_call_a_publisher(tmp_path):
    store, bundle = seeded_store(tmp_path)
    viewed = store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="查看 2 的完整广告 JSON",
        actor="ou-user",
    )
    assert viewed["draft"]["draft_id"] == bundle.drafts[1].draft_id
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="跳过全部",
        actor="ou-user",
    )
    assert all(
        row["status"] == "rejected"
        for row in store.batch(bundle.run_date, bundle.direction)
    )


def test_payload_change_invalidates_approval_before_publish(tmp_path):
    store, bundle = seeded_store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1",
        actor="ou-user",
    )
    draft_id = bundle.drafts[0].draft_id
    with sqlite3.connect(store.database) as connection:
        row = connection.execute(
            "SELECT draft_json FROM talent_pool_drafts WHERE draft_id=?",
            (draft_id,),
        ).fetchone()
        draft = json.loads(row[0])
        draft["public_payload"]["position_scope"] += "已修改"
        connection.execute(
            "UPDATE talent_pool_drafts SET draft_json=? WHERE draft_id=?",
            (json.dumps(draft, ensure_ascii=False), draft_id),
        )
    lease = store.acquire_publish_lease(bundle.run_date, bundle.direction)
    with pytest.raises(ValueError, match="approval invalidated"):
        store.begin_publish(draft_id, lease_token=lease)
    assert store.batch(bundle.run_date, bundle.direction)[0]["status"] == "pending_approval"


def test_serial_fake_publish_is_idempotent(tmp_path):
    store, bundle = seeded_store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1,3,5",
        actor="ou-user",
    )
    publisher = FakePublisher()
    first = publish_approved_serially(
        store,
        run_date=bundle.run_date,
        direction=bundle.direction,
        publisher=publisher,
        draft_ids=[bundle.drafts[index].draft_id for index in (0, 2, 4)],
    )
    second = publish_approved_serially(
        store,
        run_date=bundle.run_date,
        direction=bundle.direction,
        publisher=publisher,
        draft_ids=[],
    )
    assert [item["status"] for item in first] == ["published"] * 3
    assert second == []
    assert len(publisher.calls) == 3
    rows = store.batch(bundle.run_date, bundle.direction)
    assert [row["status"] for row in rows].count("published") == 3
    assert all(row["liepin_job_id"] for row in rows if row["status"] == "published")


def test_normal_failure_continues_but_blocking_failure_stops_queue(tmp_path):
    store, bundle = seeded_store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布全部",
        actor="ou-user",
    )
    publisher = FakePublisher(
        [
            PublishResult(False, error_code="field_error", error_message="bad field"),
            PublishResult(True, job_id="ok-2", job_url="https://example.invalid/2"),
            PublishResult(
                False,
                error_code="captcha",
                error_message="captcha",
                blocking=True,
            ),
        ]
    )
    result = publish_approved_serially(
        store,
        run_date=bundle.run_date,
        direction=bundle.direction,
        publisher=publisher,
        draft_ids=[draft.draft_id for draft in bundle.drafts],
    )
    assert [item["status"] for item in result] == ["failed", "published", "blocked"]
    rows = store.batch(bundle.run_date, bundle.direction)
    assert [row["status"] for row in rows] == [
        "publish_failed",
        "published",
        "publish_failed",
        "approved",
        "approved",
    ]
    assert len(publisher.calls) == 3


def test_expired_draft_cannot_publish(tmp_path):
    store, bundle = seeded_store(tmp_path)
    store.apply_command(
        run_date=bundle.run_date,
        direction=bundle.direction,
        command="发布 1",
        actor="ou-user",
    )
    changed = store.expire(
        run_date=bundle.run_date,
        direction=bundle.direction,
        today="2026-08-03",
    )
    assert changed >= 1
    assert store.batch(bundle.run_date, bundle.direction)[0]["status"] == "expired"
