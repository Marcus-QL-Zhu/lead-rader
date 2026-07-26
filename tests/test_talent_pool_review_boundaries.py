import json
import subprocess

import pytest

from ht_lead_radar.feishu_notify import load_talent_drafts
from ht_lead_radar.liepin_bridge import (
    ExternalLiepinPublisher,
    FakePublisher,
    publish_approved_serially,
)
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
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
