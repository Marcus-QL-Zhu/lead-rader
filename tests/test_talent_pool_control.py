import importlib.util
import json
from pathlib import Path

import pytest

from ht_lead_radar.talent_pool import generate_draft_bundle, validate_liepin_payload
from ht_lead_radar.talent_pool_store import TalentPoolStore
from test_talent_pool import sample_report


SCRIPT = Path(__file__).parents[1] / "scripts" / "talent_pool_control.py"
spec = importlib.util.spec_from_file_location("talent_pool_control", SCRIPT)
assert spec and spec.loader
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def _reported_store(tmp_path):
    database = tmp_path / "talent.sqlite"
    store = TalentPoolStore(database)
    bundle = generate_draft_bundle(sample_report())
    store.save_bundle(bundle.to_dict())
    current = store.pending_openclaw_report(claim=True)
    assert current is not None
    assert store.mark_openclaw_reported(current["snapshot_id"])
    return store, bundle, current


def _base_args(store, bundle, current):
    return [
        "--actor",
        "ou-user",
        "--run-date",
        bundle.run_date,
        "--direction",
        bundle.direction,
        "--state-db",
        str(store.database),
        "--context-snapshot-id",
        current["snapshot_id"],
    ]


def test_natural_language_view_returns_exact_persisted_final_json(tmp_path, capsys):
    store, bundle, current = _reported_store(tmp_path)

    result = control.main(
        [
            "--action",
            "view",
            "--indexes",
            "1，2",
            "--user-message",
            "查看两条广告 json",
            *_base_args(store, bundle, current),
        ]
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert [item["index"] for item in output["drafts"]] == [1, 2]
    for item, persisted in zip(output["drafts"], bundle.drafts[:2], strict=True):
        assert item["job_posting_json"] == persisted.public_payload
        validate_liepin_payload(item["job_posting_json"])
        assert "role_family" not in item["job_posting_json"]
        assert "source_leads" not in item["job_posting_json"]
        assert item["target_companies"]


def test_openclaw_can_publish_from_semantic_intent_without_exact_user_phrase(
    tmp_path, capsys
):
    store, bundle, current = _reported_store(tmp_path)

    result = control.main(
        [
            "--action",
            "publish",
            "--indexes",
            "1",
            "--user-message",
            "发布第一个草稿",
            "--fake-publish",
            *_base_args(store, bundle, current),
        ]
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["interpreted_action"] == {
        "action": "publish",
        "indexes": [1],
        "user_message": "发布第一个草稿",
    }
    assert output["publication"][0]["status"] == "published"
    first = store.batch(bundle.run_date, bundle.direction)[0]
    assert first["status"] == "published"
    assert first["approval_command"] == "发布第一个草稿"


def test_contextual_confirmation_can_be_audited_after_openclaw_resolves_index(
    tmp_path, capsys
):
    store, bundle, current = _reported_store(tmp_path)

    result = control.main(
        [
            "--action",
            "publish",
            "--indexes",
            "2",
            "--user-message",
            "确认",
            *_base_args(store, bundle, current),
        ]
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["command"]["draft_ids"] == [bundle.drafts[1].draft_id]
    second = store.batch(bundle.run_date, bundle.direction)[1]
    assert second["status"] == "approved"
    assert second["approval_command"] == "确认"


def test_missing_indexes_never_defaults_to_all(tmp_path, capsys):
    store, bundle, current = _reported_store(tmp_path)

    result = control.main(
        [
            "--action",
            "publish",
            "--indexes",
            "",
            "--user-message",
            "发布",
            *_base_args(store, bundle, current),
        ]
    )

    assert result == 72
    assert "use all explicitly" in capsys.readouterr().err
    assert all(
        row["status"] == "pending_approval"
        for row in store.batch(bundle.run_date, bundle.direction)
    )


def test_save_bundle_rejects_legacy_job_posting_payload(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    bundle = generate_draft_bundle(sample_report()).to_dict()
    bundle["drafts"][0]["public_payload"]["job_type"] = "全职"

    with pytest.raises(ValueError, match="job_type must be 社招"):
        store.save_bundle(bundle)

    assert store.batch(bundle["run_date"], bundle["direction"]) == []
