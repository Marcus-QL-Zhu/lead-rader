from datetime import datetime, timezone
from pathlib import Path

from ht_lead_radar.aggregate_adapters.base import AdapterContext
from ht_lead_radar.aggregate_adapters.body_scope import classify_long_article
from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.entity_ledger import build_article_entity_ledger
from ht_lead_radar.aggregate_adapters.action_span_ledger import build_action_span_ledger
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from ht_lead_radar.aggregate_adapters.sites.kr36 import Kr36Adapter


ARCHIVE = (
    Path(__file__).parents[1]
    / ".acceptance/aggregate-v2/kr36-reference-v4/36kr-financing-flash"
)


def _real_article(tmp_path):
    adapter = Kr36Adapter()
    channel = adapter.channel_for("36kr-financing-flash")
    context = AdapterContext.create(
        state_db=tmp_path / "kr36.sqlite3",
        fetch=lambda _url: b"",
        now=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    indexes = adapter.parse_listing(channel, (ARCHIVE / "listing.html").read_bytes(), context)
    index = next(item for item in indexes if item.source_article_id == "3916547493965442")
    return adapter.parse_detail(
        channel,
        index,
        (ARCHIVE / "detail-3916547493965442.html").read_bytes(),
        context,
    )


def test_real_kr36_digest_preserves_items_and_full_body(tmp_path):
    article = _real_article(tmp_path)

    assert len(article.clean_body) > 2_000
    assert article.structured_data["document_type"] == "multi_company_bulletin"
    assert len(article.structured_data["item_boundaries"]) == 13
    assert "\u8054\u7535" in article.clean_body
    assert "\u6708\u4e4b\u6697\u9762" in article.clean_body

    route = route_document(article)
    assert route.document_type == "multi_company_bulletin"
    assert route.reason == "adapter_document_type"
    assert len(route.units) == 13
    assert all(unit.boundary_source == "adapter" for unit in route.units)

    decision = classify_long_article(
        article.clean_body,
        title=article.index.title,
        document_type=article.structured_data["document_type"],
    )
    assert decision.mode == "multi_event_digest"
    assert decision.semantic_chars == len(article.clean_body)


def test_flattened_digest_labels_are_still_detected_without_adapter_metadata():
    from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex

    body = "\u5927\u516c\u53f8\uff1aA\u516c\u53f8\u5b8c\u6210\u4ea4\u4ed8\u3002\u65b0\u4ea7\u54c1\uff1aB\u516c\u53f8\u53d1\u5e03\u65b0\u4ea7\u54c1\u3002\u6295\u878d\u8d44\uff1aC\u516c\u53f8\u5b8c\u6210A\u8f6e\u878d\u8d44\u3002"
    index = SourceArticleIndex(
        source_id="test",
        source_article_id="digest-1",
        channel="test",
        canonical_url="https://example.com/digest-1",
        title="\u65e5\u62a5",
        published_at="2026-08-02",
        discovered_at="2026-08-02",
        cursor_value="1",
        listing_page="https://example.com",
        listing_position=1,
        content_hash="hash",
        discovery_method="fixture",
    )
    route = route_document(CleanArticle(index=index, clean_body=body))

    assert route.document_type == "multi_company_bulletin"
    assert route.reason == "body_digest_structure"
    assert len(route.units) == 3
    assert all(unit.boundary_source == "deterministic_digest" for unit in route.units)


def test_real_digest_subjects_are_item_local_and_public_items_unbound(tmp_path):
    article = _real_article(tmp_path)
    adapter = Kr36Adapter()
    channel = adapter.channel_for("36kr-financing-flash")
    rules = adapter.rule_events(channel, article)
    processor = MiniMaxSemanticProcessor(None, claim_centric_v27=True)
    candidates = processor._claim_candidates(article, rules)
    entities = build_article_entity_ledger(article, candidates, rules)
    actions = build_action_span_ledger(article, entities, candidates)
    by_id = entities.by_id()

    eligible_names = {entity.canonical_name for entity in entities.eligible()}
    assert {"\u6708\u4e4b\u6697\u9762", "\u8054\u7535", "\u96c6\u521b\u5317\u65b9", "\u667a\u8c37\u5929\u53a8"} <= eligible_names
    assert {"\u793e\u533a\u5f00\u5c55\u6c7d\u8f66", "\u6b64\u9879"}.isdisjoint(eligible_names)

    grounded = [
        (claim.event_type_hint, by_id[claim.primary_subject_entity_id].canonical_name)
        for claim in actions.claims
        if claim.primary_subject_entity_id
    ]
    assert ("factory_or_capacity", "\u8054\u7535") in grounded
    assert ("technical_milestone", "\u96c6\u521b\u5317\u65b9") in grounded
    assert ("funding", "\u6708\u4e4b\u6697\u9762") in grounded
    assert ("funding", "\u667a\u8c37\u5929\u53a8") in grounded
    assert all(
        "\u793e\u533a\u5f00\u5c55\u6c7d\u8f66" not in [by_id[item].canonical_name for item in claim.allowed_subject_entity_ids]
        and "\u6b64\u9879" not in [by_id[item].canonical_name for item in claim.allowed_subject_entity_ids]
        for claim in actions.claims
    )
