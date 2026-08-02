from __future__ import annotations

import json

from scripts import run_minimax_input_loop as loop


def _article(body: str) -> loop.ExperimentArticle:
    return loop.ExperimentArticle(
        source_id="test-source",
        source_article_id="article-1",
        article={
            "clean_body": body,
            "index": {
                "title": "Example company completed financing",
                "published_at": "2026-07-15",
            },
        },
        rule_events=(),
    )


def test_manifest_has_disjoint_unique_splits() -> None:
    manifest = json.loads(loop.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    train = [
        tuple(pair) for batch in manifest["train_rounds"].values() for pair in batch
    ]
    holdout = [tuple(pair) for pair in manifest["holdout"]]

    assert len(train) == len(set(train))
    assert len(holdout) == len(set(holdout))
    assert set(train).isdisjoint(holdout)


def test_holdout_order_is_seeded_without_replacement() -> None:
    manifest = json.loads(loop.DEFAULT_MANIFEST.read_text(encoding="utf-8"))

    first = loop._holdout_order(manifest)
    second = loop._holdout_order(manifest)

    assert first == second
    assert len(first) == len({tuple(pair) for pair in first})
    assert {tuple(pair) for pair in first} == {
        tuple(pair) for pair in manifest["holdout"]
    }


def test_article_sections_reconstruct_source_exactly() -> None:
    body = "A" * 600 + "." + "B" * 650 + "?" + "C" * 410

    sections = loop._article_sections(body, max_chars=700)

    assert "".join(section["text"] for section in sections) == body
    assert sections[0]["char_start"] == 0
    assert sections[-1]["char_end"] == len(body)
    assert all(
        left["char_end"] == right["char_start"]
        for left, right in zip(sections, sections[1:], strict=False)
    )


def test_round5_ledger_keeps_full_body_once_and_unique_ids() -> None:
    article = _article(
        "Example company completed A-round financing in 2026. "
        "It plans to build a production line next year. " + "detail " * 60
    )

    _, prompt, adjudication = loop._round5_variant(article, "C")
    payload = json.loads(prompt)
    ids = [item["id"] for item in adjudication]

    assert payload["input"]["article_text"] == article.body
    assert prompt.count(article.body) == 1
    assert len(ids) == len(set(ids))
    assert any(item.startswith("h_title_") for item in ids)


def test_locked_merge_refuses_changed_required_fields() -> None:
    article = _article("Example company completed A-round financing.")
    slot = {
        "event_id": "e_1",
        "company": "Example company",
        "event_type": "funding",
        "event_status": "completed",
        "funding_round": "",
        "funding_amount": "",
        "cumulative_funding_amount": "",
        "investors": [],
        "evidence_quotes": ["Example company completed A-round financing."],
        "covered_candidate_ids": ["c_1"],
    }
    changed = {
        "events": [
            {
                **slot,
                "company": "Invented company",
                "funding_round": "A-round",
            }
        ]
    }

    merged = loop._merge_locked_events(article, [slot], changed)

    assert merged[0]["company"] == "Example company"
    assert merged[0]["funding_round"] == ""
    assert merged[0]["evidence_quotes"] == slot["evidence_quotes"]
