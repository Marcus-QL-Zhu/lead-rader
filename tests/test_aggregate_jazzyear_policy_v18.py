from datetime import datetime, timezone

import pytest

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SourceArticleIndex,
)
from ht_lead_radar.aggregate_adapters.sites.jazzyear import JazzyearAdapter


def _article(body: str) -> CleanArticle:
    index = SourceArticleIndex(
        source_id="jazzyear-latest",
        source_article_id="1827",
        channel="insight",
        canonical_url="https://www.jazzyear.com/article_info.html?id=1827",
        title="GDPS全球场景征集启动",
        published_at="2026-07-29",
        discovered_at=datetime(
            2026,
            7,
            30,
            tzinfo=timezone.utc,
        ).isoformat(),
        cursor_value="1827",
        listing_page="https://www.jazzyear.com/",
        listing_position=1,
        content_hash="index-policy",
        discovery_method="exact",
    )
    return CleanArticle(
        index=index,
        clean_body=body,
        content_hash="article-policy",
    )


def test_jazzyear_extracts_exact_current_joint_ministry_policy():
    adapter = JazzyearAdapter()
    article = _article(
        "工信部联合国务院国资委启动 "
        "《2026年度人形机器人与具身智能实景实训专项行动》，"
        "明确生产制造、仓储物流等重点场景。"
    )

    events = adapter.rule_events(adapter.channels[0], article)
    policy = [
        event
        for event in events
        if event.processor == "rules:jazzyear-policy-v1"
    ]

    assert [
        (
            event.canonical_company,
            event.event_type,
            event.event_status,
        )
        for event in policy
    ] == [("工业和信息化部", "policy_or_standard", "started")]
    assert policy[0].company_mentions == ("工业和信息化部", "工信部")
    assert policy[0].evidence_quotes == (
        "工信部联合国务院国资委启动 "
        "《2026年度人形机器人与具身智能实景实训专项行动》",
    )


@pytest.mark.parametrize(
    "body",
    (
        "工信部启动《2026年度人形机器人与具身智能实景实训专项行动》。",
        "工信部联合国务院国资委介绍"
        "《2026年度人形机器人与具身智能实景实训专项行动》。",
        "工信部联合国务院国资委启动"
        "《2025年度人形机器人与具身智能实景实训专项行动》。",
    ),
)
def test_jazzyear_policy_seed_requires_exact_joint_current_launch(body):
    adapter = JazzyearAdapter()

    events = adapter.rule_events(adapter.channels[0], _article(body))

    assert not any(
        event.processor == "rules:jazzyear-policy-v1"
        for event in events
    )
