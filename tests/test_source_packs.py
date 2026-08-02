import json
from datetime import date
from pathlib import Path

import pytest

from ht_lead_radar.source_packs import (
    SourcePackError,
    SourcePackRegistry,
    load_source_packs,
)


def test_checked_in_registry_loads_and_has_all_required_packs():
    registry = load_source_packs()

    assert registry.version == 2
    assert registry.verified_on == "2026-07-25"
    assert {pack.id for pack in registry.packs}.issuperset(
        {
            "generic-cn",
            "brain-computer-interface-cn",
            "semiconductor-cn",
            "commercial-space-cn",
            "fusion-cn",
            "embodied-intelligence-cn",
        }
    )


@pytest.mark.parametrize(
    "topic,pack_id,specific_source",
    [
        ("脑机接口", "brain-computer-interface-cn", "beijing-etown-major-projects"),
        ("芯片", "semiconductor-cn", "cnipa-ic-layout-announcements"),
        ("商业航天", "commercial-space-cn", "cnsa-policy-announcements"),
        ("可控核聚变", "fusion-cn", "iter-china-news"),
        ("人形机器人", "embodied-intelligence-cn", "suzhou-robot-association"),
    ],
)
def test_sector_selection_fans_in_generic_and_matching_pack(
    topic, pack_id, specific_source
):
    selection = load_source_packs().select(topic)

    assert selection.pack_ids == ("generic-cn", pack_id)
    assert specific_source in {source.id for source in selection.sources}
    assert "miit-science-files" in {source.id for source in selection.sources}
    assert selection.unmatched_topic is False


def test_arbitrary_topic_uses_generic_policy_financing_eia_and_tender_sources():
    selection = load_source_packs().select("合成生物学")
    ids = {source.id for source in selection.sources}

    assert selection.pack_ids == ("generic-cn",)
    assert selection.unmatched_topic is True
    assert {
        "miit-science-files",
        "ccgp-central-open-tenders",
        "ccgp-local-open-tenders",
        "mee-eia-list",
        "pedaily-investment-news",
    }.issubset(ids)


def test_disabled_dynamic_or_blocked_sources_are_visible_but_not_scheduled_by_default():
    registry = load_source_packs()
    default = registry.select("脑机接口")
    audit = registry.select("脑机接口", include_disabled=True)

    default_ids = {source.id for source in default.sources}
    audit_ids = {source.id for source in audit.sources}
    disabled_ids = {source.id for source in default.disabled_sources}
    assert "nmpa-medical-device-notices" not in default_ids
    assert "nmpa-medical-device-notices" in audit_ids
    assert "nmpa-medical-device-notices" in disabled_ids
    assert "chictr-public-search" in disabled_ids
    assert any(
        source.source_type == "company_official" for source in default.disabled_sources
    )


def test_every_source_has_provenance_signal_tags_adapter_and_verification_state():
    registry = load_source_packs()

    for source in registry.sources:
        assert source.url.startswith(("http://", "https://"))
        assert source.owner
        assert source.signal_types
        assert source.industry_tags
        assert source.adapter
        assert date.fromisoformat(source.verified_on) >= date(2026, 7, 25)
        assert source.status
        assert source.verification_note
        if source.enabled:
            assert source.status in {"verified_static_list", "verified_public_listing"}


def test_signal_filter_keeps_only_sources_that_can_emit_requested_signal():
    selection = load_source_packs().select("商业航天", signal_types=("project_call",))

    assert selection.sources
    assert all("project_call" in source.signal_types for source in selection.sources)
    assert "cnsa-policy-announcements" in {source.id for source in selection.sources}


def test_registry_serialization_is_json_compatible():
    payload = load_source_packs().to_dict()

    encoded = json.dumps(payload, ensure_ascii=False)
    assert "中国大陆脑机接口" in encoded
    assert (
        payload["policy"]["metaso_role"]
        == "verification_only_after_fixed-source discovery"
    )


def _write_registry(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "source-packs.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _minimal_payload() -> dict:
    return {
        "version": 1,
        "verified_on": "2026-07-25",
        "policy": {},
        "sources": [
            {
                "id": "one",
                "name": "One",
                "owner": "Owner",
                "source_type": "government",
                "grade": "A",
                "url": "https://example.gov.cn/list",
                "adapter": "html_list",
                "signal_types": ["policy"],
                "industry_tags": ["generic"],
                "enabled": True,
                "verified_on": "2026-07-25",
                "status": "verified_static_list",
                "verification_note": "test",
            }
        ],
        "packs": [
            {
                "id": "generic-cn",
                "name": "Generic",
                "aliases": ["generic"],
                "industry_tags": ["generic"],
                "source_ids": ["one"],
            }
        ],
    }


def test_unknown_pack_source_reference_fails_closed(tmp_path):
    payload = _minimal_payload()
    payload["packs"][0]["source_ids"] = ["missing"]

    with pytest.raises(SourcePackError, match="unknown sources"):
        SourcePackRegistry.load(_write_registry(tmp_path, payload))


def test_enabled_source_with_unverified_or_blocked_status_fails_closed(tmp_path):
    payload = _minimal_payload()
    payload["sources"][0]["status"] = "blocked_automated_access"

    with pytest.raises(SourcePackError, match="enabled but status"):
        SourcePackRegistry.load(_write_registry(tmp_path, payload))


def test_undocumented_adapter_or_non_public_url_is_rejected(tmp_path):
    payload = _minimal_payload()
    payload["sources"][0]["adapter"] = "secret_api"
    with pytest.raises(SourcePackError, match="unsupported"):
        SourcePackRegistry.load(_write_registry(tmp_path, payload))

    payload = _minimal_payload()
    payload["sources"][0]["url"] = "file:///etc/passwd"
    with pytest.raises(SourcePackError, match=r"public http\(s\)"):
        SourcePackRegistry.load(_write_registry(tmp_path, payload))


def test_multi_topic_selection_unions_all_packs_without_duplicate_sources():
    registry = load_source_packs()
    selection = registry.select("具身智能|半导体|商业航天|核聚变|脑机接口")
    source_ids = [source.id for source in selection.sources]

    assert set(selection.pack_ids) == {
        "generic-cn",
        "embodied-intelligence-cn",
        "semiconductor-cn",
        "commercial-space-cn",
        "fusion-cn",
        "brain-computer-interface-cn",
    }
    assert len(source_ids) == len(set(source_ids))
    assert all(source.source_type != "company_official" for source in selection.sources)


def test_enabled_legacy_and_source_pack_urls_do_not_overlap():
    legacy = json.loads(
        (Path(__file__).parents[1] / "config" / "fixed-sources.json").read_text(
            encoding="utf-8"
        )
    )
    registry = load_source_packs()
    selected = registry.select("具身智能|半导体|商业航天|核聚变|脑机接口")
    legacy_urls = {
        item["list_url"]
        for item in legacy["sources"]
        if item.get("enabled", True) and not item.get("company")
    }
    pack_urls = {source.url for source in selected.sources}

    assert legacy_urls.isdisjoint(pack_urls)
