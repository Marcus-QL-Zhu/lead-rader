from datetime import datetime, timezone

import pytest

from ht_lead_radar.aggregate_adapters.action_span_ledger import build_action_span_ledger
from ht_lead_radar.aggregate_adapters.base import AdapterContext, DetailFetchError
from ht_lead_radar.aggregate_adapters.entity_ledger import build_article_entity_ledger
from ht_lead_radar.aggregate_adapters.sites.shenzhen_sasac import (
    ShenzhenSasacAppointmentsAdapter,
)


NOW = datetime(2026, 1, 23, 2, tzinfo=timezone.utc)


def _context(tmp_path, *, full=False):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
        decision_state={"capture_full_visible_window": {"enabled": full}},
    )


def _listing(date="2026-01-22") -> bytes:
    return f"""
    <html><ul class="tzgg_content">
      <li><a href="https://gzw.sz.gov.cn/zwgk/qt/rsxx/content/post_12612456.html">
        <p>深圳市国资委关于肖春林等职务任免的通知</p><span>{date}</span>
      </a></li>
      <li><a href="https://gzw.sz.gov.cn/zwgk/qt/rsxx/content/post_12885568.html">
        <p>深圳市国资委2026年公开选调公务员拟选调人员公示公告</p><span>{date}</span>
      </a></li>
    </ul></html>
    """.encode()


def _detail(title="深圳市国资委关于肖春林等职务任免的通知") -> bytes:
    return f"""
    <html><head>
      <meta name="SiteIDCode" content="4403000052">
      <meta name="ColumnName" content="人事信息">
    </head><body>
      <div class="xl_wrap"><div class="title">{title}</div>
        <div class="title_info"><span>发布时间：2026-01-22 11:40:11</span></div>
      </div>
      <div class="xl_main articleBox">
        <p>深圳市机场（集团）有限公司：</p><p>经研究决定：</p>
        <p>委派肖春林任深圳市机场（集团）有限公司财务总监、董事，试用期一年；</p>
        <p>推荐张磊任深圳市机场（集团）有限公司董事长；</p>
        <p>免去刘秀丽的深圳市机场（集团）有限公司财务总监、董事职务。</p>
      </div>
    </body></html>
    """.encode()


def test_shenzhen_sasac_filters_non_enterprise_rows_and_extracts_detail(tmp_path):
    adapter = ShenzhenSasacAppointmentsAdapter()
    channel = adapter.channels[0]
    context = _context(tmp_path)

    indexes = adapter.parse_listing(channel, _listing(), context)
    assert len(indexes) == 1
    assert indexes[0].source_article_id == "12612456"
    article = adapter.parse_detail(channel, indexes[0], _detail(), context)

    assert article.extraction_method == "exact"
    assert "委派肖春林" in article.clean_body
    rule_events = adapter.rule_events(channel, article)
    assert rule_events
    assert {event.canonical_company for event in rule_events} == {
        "深圳市机场（集团）有限公司"
    }
    ledger = build_article_entity_ledger(article, (), rule_events)
    actions = build_action_span_ledger(article, ledger, ())
    executive = [claim for claim in actions.claims if claim.event_type_hint == "executive_change"]
    assert executive
    assert {claim.event_status_hint for claim in executive} >= {"completed", "target"}


def test_shenzhen_sasac_monitor_change_is_reason_coded_not_a_company(tmp_path):
    adapter = ShenzhenSasacAppointmentsAdapter()
    channel = adapter.channels[0]
    index = adapter.parse_listing(channel, _listing(), _context(tmp_path))[0]
    html = _detail().replace(
        "委派肖春林任深圳市机场（集团）有限公司财务总监、董事，试用期一年；".encode(),
        "建议宋爱平不再担任深圳市机场（集团）有限公司监事；".encode(),
    ).replace(
        "推荐张磊任深圳市机场（集团）有限公司董事长；".encode(),
        b"",
    ).replace(
        "免去刘秀丽的深圳市机场（集团）有限公司财务总监、董事职务。".encode(),
        b"",
    )
    article = adapter.parse_detail(channel, index, html, _context(tmp_path))
    entities = build_article_entity_ledger(article, (), adapter.rule_events(channel, article))
    actions = build_action_span_ledger(article, entities, ())

    assert entities.entity_for_name("深圳市机场（集团）有限公司") is not None
    assert entities.entity_for_name(
        "建议宋爱平不再担任深圳市机场（集团）有限公司"
    ) is None
    assert actions.claims == ()
    assert [item.reason for item in actions.exclusions] == [
        "governance_role_outside_operating_director_scope"
    ]


def test_shenzhen_sasac_full_window_and_detail_invariants(tmp_path):
    adapter = ShenzhenSasacAppointmentsAdapter()
    channel = adapter.channels[0]
    old = _listing("2025-12-01")
    assert adapter.parse_listing(channel, old, _context(tmp_path / "daily")) == []
    assert len(adapter.parse_listing(channel, old, _context(tmp_path / "full", full=True))) == 1

    index = adapter.parse_listing(channel, _listing(), _context(tmp_path / "detail"))[0]
    with pytest.raises(DetailFetchError, match="title mismatch"):
        adapter.parse_detail(channel, index, _detail("错误标题"), _context(tmp_path / "bad"))
