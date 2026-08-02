from __future__ import annotations

import pytest

from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex


def _article(source_id: str, title: str, body: str, *, structured: dict | None = None):
    return CleanArticle(
        index=SourceArticleIndex(
            source_id=source_id,
            source_article_id="route-1",
            channel="test",
            canonical_url="https://example.invalid/route-1",
            title=title,
            published_at="2026-08-01T00:00:00+08:00",
            discovered_at="2026-08-01T01:00:00+08:00",
            cursor_value="1",
            listing_page="https://example.invalid",
            listing_position=1,
            content_hash="index-hash",
            discovery_method="fixture",
        ),
        clean_body=body,
        structured_data=structured or {},
        content_hash="body-hash",
    )


def _digest_article():
    items = (
        "\u6295\u878d\u8d44\uff1a\n\u7532\u516c\u53f8\u5b8c\u6210A\u8f6e\u878d\u8d44\u3002",
        "\u4e59\u516c\u53f8\u5b8c\u6210B\u8f6e\u878d\u8d44\u3002",
        "\u4e19\u516c\u53f8\u83b7\u5f97C\u8f6e\u878d\u8d44\u3002",
    )
    body = "\n".join(items)
    boundaries = []
    cursor = 0
    for item in items:
        start = body.index(item, cursor)
        boundaries.append({"char_start": start, "char_end": start + len(item)})
        cursor = start + len(item)
    return _article(
        "36kr-financing-flash",
        "\u4eca\u65e5\u878d\u8d44\u5feb\u62a5",
        body,
        structured={"item_boundaries": boundaries},
    )


@pytest.mark.parametrize(
    ("source_id", "title", "body", "family", "mode", "llm"),
    [
        (
            "pedaily-vcpe-events",
            "\u521b\u4e1a\u6295\u8d44\u57fa\u91d1\u8bbe\u7acb",
            "\u57fa\u91d1\u8bbe\u7acb\uff0c\u6295\u8d44\u4eba\u53c2\u52a0\u6d3b\u52a8",
            "institutional_funding",
            "institution_or_target_review",
            True,
        ),
        (
            "cyzone-financing",
            "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44",
            "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44",
            "single_company_funding",
            "single_unit",
            False,
        ),
        (
            "lieyunpro-archives",
            "\u6811\u8111\u79d1\u6280\u5b8c\u6210\u5929\u4f7f\u8f6e\u878d\u8d44",
            "\u6811\u8111\u79d1\u6280\u5b8c\u6210\u5929\u4f7f\u8f6e\u878d\u8d44",
            "single_company_funding",
            "single_unit",
            False,
        ),
        (
            "vbdata-funding",
            "\u5bf9\u8bdd\u683c\u5f0f\u5854\u5f6d\u96f7",
            "\u8bb0\u8005\u95ee\uff1a\u8fd9\u662f\u4ec0\u4e48\uff1f\u53d7\u8bbf\u8005\u7b54\uff1a\u8fd9\u662f\u91c7\u8bbf\u3002",
            "interview_commentary",
            "prefix_2000_if_event_else_skip",
            True,
        ),
        (
            "jazzyear-latest",
            "AI\u884c\u4e1a\u89c2\u5bdf",
            "\u884c\u4e1a\u7814\u7a76\u3002" * 900,
            "long_feature",
            "prefix_2000_if_event_else_skip",
            True,
        ),
        (
            "zhidx-financing",
            "\u534a\u5bfc\u4f53\u516c\u53f8\u878d\u8d44\u8d85\u8fc71\u4ebf",
            "\u534a\u5bfc\u4f53\u516c\u53f8\u5b8c\u6210\u8d85\u8fc71\u4ebf\u878d\u8d44",
            "single_company_funding",
            "single_unit",
            False,
        ),
        (
            "stcn-flash",
            "\u516c\u544a\u7cbe\u9009\uff1a\u7532\u82af\u6269\u4ea7\uff1b\u4e59\u673a\u5668\u4eba\u8ba2\u5355",
            "\u7532\u82af\u6269\u4ea7\u3002\u4e59\u673a\u5668\u4eba\u83b7\u5f97\u8ba2\u5355",
            "compound_company_bulletin",
            "split_atomic_claims",
            True,
        ),
        (
            "cls-telegraph",
            "\u884c\u4e1a\u62a5\u544a\uff1a\u5e02\u573a\u6570\u636e",
            "\u62a5\u544a\u4ec5\u8ba8\u8bba\u5e02\u573a\u6570\u636e",
            "policy_market",
            "market_rules_then_company_override",
            False,
        ),
        (
            "miit-science-files",
            "2026\u5e74\u521b\u65b0\u4efb\u52a1\u7533\u62a5\u901a\u77e5",
            "\u5de5\u4fe1\u90e8\u53d1\u5e03\u7533\u62a5\u901a\u77e5",
            "policy_market",
            "policy_rules_then_company_override",
            False,
        ),
    ],
)
def test_route_gate_classifies_representative_aggregate_shapes(
    source_id, title, body, family, mode, llm
):
    route = route_document(_article(source_id, title, body))

    assert route.document_family == family
    assert route.processing_mode == mode
    assert route.llm_gate_required is llm
    assert route.gate_confidence in {"high", "medium", "low"}
    assert route.gate_signals


def test_route_gate_classifies_real_36kr_digest_as_funding_digest():
    route = route_document(_digest_article())

    assert route.document_type == "multi_company_bulletin"
    assert route.document_family == "multi_company_funding_digest"
    assert route.processing_mode == "split_units"
    assert route.llm_gate_required is True
    assert "adapter_item_boundaries" in route.gate_signals



def test_route_gate_prefers_adapter_company_over_institution_cue():
    route = route_document(
        _article(
            "zhidx-financing",
            "\u6df1\u5733\u534a\u5bfc\u4f53\u5c0f\u5de8\u4eba\uff0c\u878d\u8d44\u8fd110\u4ebf",
            "\u57c3\u82af\u534a\u5bfc\u4f53\u83b7\u5f97\u878d\u8d44?\u6295\u8d44\u673a\u6784\u53c2\u4e0e\u3002",
            structured={
                "company": "\u57c3\u82af\u534a\u5bfc\u4f53",
                "company_mentions": ["\u57c3\u82af\u534a\u5bfc\u4f53"],
            },
        )
    )

    assert route.document_family == "single_company_funding"
    assert route.processing_mode == "single_unit"


def test_newswire_company_metadata_overrides_bulletin_shape():
    route = route_document(
        _article(
            "stcn-flash",
            "\u957f\u6c5f\u5b58\u50a8\uff1a\u516c\u53f8\u53d1\u5e03\u65b0\u6d88\u606f",
            "\u957f\u6c5f\u5b58\u50a8\u53d1\u5e03\u65b0\u6d88\u606f\u3002",
            structured={"company": "\u957f\u6c5f\u5b58\u50a8"},
        )
    )

    assert route.document_type == "single_company_flash"
    assert route.document_family == "single_company_flash"
    assert route.reason == "adapter_company_field"


def test_policy_issuer_is_not_treated_as_target_company():
    route = route_document(
        _article(
            "miit-science-files",
            "\u5de5\u4fe1\u90e8\u53d1\u5e03\u901a\u77e5",
            "\u5de5\u4fe1\u90e8\u53d1\u5e03\u901a\u77e5\u3002",
            structured={
                "company": "\u5de5\u4fe1\u90e8",
                "company_mentions": ["\u5de5\u4fe1\u90e8"],
            },
        )
    )

    assert route.document_family == "policy_market"
    assert "adapter_company_field" not in route.gate_signals


def test_long_body_without_explicit_event_uses_long_article_window():
    route = route_document(
        _article(
            "cyzone-latest",
            "\u4f30\u503c3500\u4ebf\uff0c\u4e2d\u56fd\u6a21\u578b\u5168\u7403\u6447\u4eba",
            "\u884c\u4e1a\u5206\u6790\u3002" * 700,
        )
    )

    assert route.document_type == "long_feature"
    assert route.processing_mode == "prefix_2000_if_event_else_skip"


def test_market_financing_noise_does_not_become_company_event():
    route = route_document(
        _article(
            "36kr-financing-flash",
            "\u4e2a\u8d37\u878d\u8d44\u6210\u672c\u660e\u793a\u5373\u5c06\u5b9e\u65bd",
            "\u591a\u5bb6\u673a\u6784\u63d0\u524d\u5b8c\u6210\u5408\u89c4\u5e03\u5c40\u3002",
        )
    )

    assert route.document_family == "policy_market"
    assert route.processing_mode == "market_rules_then_company_override"
