import json

import pytest

from ht_lead_radar.talent_pool import (
    assert_anonymized,
    generate_draft_bundle,
    is_director_plus,
    validate_liepin_payload,
)


def sample_report(leads=3):
    items = [
        {
            "company": "星火机器人",
            "score": 88,
            "target_roles": ["机器人研发总监", "产品总监"],
            "evidence": [
                {
                    "event_type": "funding",
                    "source_url": "https://example.com/a",
                    "people": ["张秘密"],
                }
            ],
            "basic_research": {
                "aliases": ["星火智造"],
                "products": ["独角兽一号"],
                "founders": ["张秘密"],
            },
        },
        {
            "company": "深空动力",
            "score": 79,
            "target_roles": ["供应链总监", "业务拓展VP"],
            "evidence": [
                {
                    "event_type": "factory_or_capacity",
                    "source_url": "https://example.com/b",
                }
            ],
        },
        {
            "company": "量子芯片",
            "score": 72,
            "target_roles": ["战略与运营总监"],
            "evidence": [
                {
                    "event_type": "major_order",
                    "source_url": "https://example.com/c",
                }
            ],
        },
    ][:leads]
    return {
        "manifest": {
            "run_id": "run-20260726",
            "as_of": "2026-07-26",
            "direction": "具身智能",
        },
        "leads": items,
    }


def test_generation_is_stable_diverse_director_plus_and_anonymized():
    first = generate_draft_bundle(sample_report())
    second = generate_draft_bundle(sample_report())

    assert first.to_dict() == second.to_dict()
    assert 3 <= len(first.drafts) <= 10
    assert len(first.drafts) == 5
    assert len({item.role_family for item in first.drafts}) == len(first.drafts)
    assert all(item.seniority == "Director+" for item in first.drafts)
    assert all(is_director_plus(item.recommended_title) for item in first.drafts)
    assert all(item.source_leads for item in first.drafts)

    public = json.dumps(
        [item.public_payload for item in first.drafts], ensure_ascii=False
    )
    for secret in ("星火机器人", "星火智造", "独角兽一号", "张秘密", "深空动力"):
        assert secret not in public
    internal = json.dumps(first.to_dict(), ensure_ascii=False)
    assert "星火机器人" in internal
    assert "https://example.com/a" in internal


def test_sparse_current_report_produces_three_or_more_nonduplicate_personas():
    bundle = generate_draft_bundle(sample_report(leads=1), target_count=5)
    assert len(bundle.drafts) == 3
    assert len({item.talent_persona for item in bundle.drafts}) == 3
    assert all(item.source_leads[0].company == "星火机器人" for item in bundle.drafts)


def test_no_lead_is_clear_and_does_not_reuse_stale_drafts():
    report = sample_report(leads=0)
    bundle = generate_draft_bundle(report)
    assert bundle.drafts == ()
    assert bundle.run_date == "2026-07-26"


def test_real_liepin_contract_fields_and_advertisement_shape():
    bundle = generate_draft_bundle(sample_report())
    for draft in bundle.drafts:
        payload = draft.public_payload
        validate_liepin_payload(payload)
        assert payload["seniority"] == "10年以上"
        assert payload["education"] == "本科"
        assert payload["cities"] == ["上海"]
        assert payload["work_experience_years"] == [10]
        assert payload["salary_low"].endswith("k")
        assert payload["salary_high"].endswith("k")
        assert "人才蓄水" not in payload["position_scope"]
        assert "长期机会储备" not in payload["position_scope"]
        assert payload["position_scope"].startswith("【岗位职责】\n• ")
        assert "\n\n【任职要求】\n• " in payload["position_scope"]
        assert payload["job_type"] == "社招"
        assert payload["languages"] == ["普通话"]
        assert "五险一金" in payload["benefits"]
        assert len(payload["position_scope"]) <= 500
        assert set(payload) == {
            "position_name",
            "position_scope",
            "cities",
            "seniority",
            "work_experience_years",
            "education",
            "salary_low",
            "salary_high",
            "must_have_signals",
            "preferred_signals",
            "benefits",
            "target_count",
            "job_type",
            "recruit_count",
            "languages",
        }


def test_anonymization_gate_and_contract_reject_invalid_payload():
    draft = generate_draft_bundle(sample_report()).drafts[0]
    with pytest.raises(ValueError, match="leaks"):
        assert_anonymized(draft.public_payload, forbidden_terms=["岗位职责"])
    broken = dict(draft.public_payload)
    broken["seniority"] = "总监"
    with pytest.raises(ValueError, match="seniority"):
        validate_liepin_payload(broken)
    broken = dict(draft.public_payload)
    broken["position_name"] = "研发经理"
    with pytest.raises(ValueError, match="Director"):
        validate_liepin_payload(broken)


def test_old_morning_payload_variants_fail_before_persistence():
    payload = dict(generate_draft_bundle(sample_report()).drafts[0].public_payload)

    invalid_values = (
        ("job_type", "全职", "job_type"),
        ("languages", ["中文"], "languages"),
        ("seniority", "10 年以上", "seniority"),
        ("benefits", ["参与业务规模化"], "五险一金"),
        ("position_scope", "岗位使命：建设业务能力。", "two separated sections"),
    )
    for key, value, message in invalid_values:
        broken = dict(payload)
        broken[key] = value
        with pytest.raises(ValueError, match=message):
            validate_liepin_payload(broken)

    broken = dict(payload)
    broken["source_leads"] = []
    with pytest.raises(ValueError, match="unsupported Liepin fields"):
        validate_liepin_payload(broken)

    for legacy_field, value in (
        ("salary_months", "15个月"),
        ("hard_rejects", ["仅有个人贡献者经历且无团队管理责任"]),
    ):
        broken = dict(payload)
        broken[legacy_field] = value
        with pytest.raises(ValueError, match="unsupported Liepin fields"):
            validate_liepin_payload(broken)


def test_local_liepin_executable_contract_is_still_compatible_when_available():
    publish_script = (
        __import__("pathlib").Path(__file__).parents[2]
        / "liepin-skills"
        / "liepin-job-posting"
        / "scripts"
        / "publish_job.py"
    )
    if not publish_script.exists():
        pytest.skip("read-only sibling Liepin Skills checkout is not present")
    source = publish_script.read_text(encoding="utf-8")
    skill = (publish_script.parents[1] / "SKILL.md").read_text(encoding="utf-8")
    assert "position_name" in source
    assert "position_scope" in source
    assert "seniority" in source
    assert "education" in source
    assert "salary_low" in source
    assert "salary_high" in source
    assert "cities" in source
    assert "--no-pipeline" in source
    assert '"job_type": "社招"' in skill
    assert '"languages": ["普通话"]' in skill
    assert "【岗位职责】" in skill and "【任职要求】" in skill
    assert "seniority 字段必须与猎聘 DOM 选项**逐字匹配**（无空格）" in skill
