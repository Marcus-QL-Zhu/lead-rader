import json
from pathlib import Path

from ht_lead_radar.aggregate_adapters.body_scope import (
    LONG_ARTICLE_WINDOW_CHARS,
    scope_long_article,
)


def test_real_jiqizhixin_long_feature_keeps_openai_event_inside_window():
    """The second real long-form case must retain its Gold event."""

    bundle_path = (
        Path(__file__).parents[1]
        / "evaluation/semantic-v27/development-v2-bundle.jsonl"
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    row = next(
        item
        for item in bundle["articles"]
        if item["key"] == "jiqizhixin-industry-analysis:2026-08-01-2"
    )
    article = row["article"]
    body = article["clean_body"]

    scoped, decision = scope_long_article(
        body,
        title=article["index"]["title"],
        document_type="long_feature",
    )

    assert decision.mode == "single_event_expansion"
    assert decision.original_chars == 2_128
    assert decision.semantic_chars == LONG_ARTICLE_WINDOW_CHARS
    assert decision.tail_action_count == 0
    assert scoped[2_000:].strip() == ""
    gold_evidence = (
        "OpenAI 表示 已封禁与该行动相关的账号 ，向行业伙伴和有关部门共享了威胁指标，并采取措施提高这些行为者重新获取其产品和服务的难度。"
    )
    assert gold_evidence in scoped[:LONG_ARTICLE_WINDOW_CHARS]
