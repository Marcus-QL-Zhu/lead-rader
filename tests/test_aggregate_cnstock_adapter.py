from datetime import datetime, timezone
import json

import pytest

from ht_lead_radar.aggregate_adapters.base import AdapterContext, DetailFetchError
from ht_lead_radar.aggregate_adapters.sites.cnstock import CnstockCompanyChannelAdapter


NOW = datetime(2026, 8, 2, 2, tzinfo=timezone.utc)


def _context(tmp_path, *, full=False):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
        decision_state={"capture_full_visible_window": {"enabled": full}},
    )


def _script(payload):
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script></html>"
    ).encode()


def _item(article_id="754022", day="01"):
    return {
        "contId": article_id,
        "name": "橡鹿机器人董事长杨建成：烹饪机器人从商业后厨走向家庭厨房",
        "pubTime": "08-01",
        "summary": "具身智能进入家庭应用。",
        "author": "窦世平",
        "nodeInfo": {"name": "聚焦"},
        "tagInfo": {"name": "橡鹿机器人"},
        "shareInfo": {
            "dateInfo": {
                "year": 2026,
                "month": "08",
                "day": day,
                "hour": "13",
                "minute": "35",
            }
        },
    }


def _listing(item=None):
    return _script(
        {
            "buildId": "build_20260801",
            "props": {"pageProps": {"data": {"pageInfo": {"list": [item or _item()]}}}},
        }
    )


def _nested_listing():
    item = _item()
    return _script(
        {
            "buildId": "build_20260801",
            "props": {
                "pageProps": {
                    "data": {
                        "pageInfo": {
                            "list": [
                                {"cardMode": 1, "childList": [item]},
                                item,
                            ]
                        }
                    }
                }
            }
        }
    )


def _detail(article_id="754022", title=None):
    title = title or _item()["name"]
    return _script(
        {
            "props": {
                "pageProps": {
                    "data": {
                        "contId": article_id,
                        "name": title,
                        "title": title,
                        "pubTime": "2026-08-01 13:35",
                        "author": "作者：窦世平",
                        "nodeInfo": {"name": "聚焦"},
                        "tagInfo": {"name": "橡鹿机器人"},
                        "textInfo": {
                            "content": "<body><p>橡鹿机器人发布新一代烹饪机器人，并已在商业后厨完成客户验证。</p><p>公司计划明年扩大交付。</p></body>"
                        },
                    }
                }
            }
        }
    )


def _detail_json(article_id="754022", title=None):
    html_payload = json.loads(
        _detail(article_id=article_id, title=title)
        .decode()
        .split('<script id="__NEXT_DATA__" type="application/json">', 1)[1]
        .split("</script>", 1)[0]
    )
    return json.dumps(html_payload["props"], ensure_ascii=False).encode()


def test_cnstock_next_data_listing_and_detail(tmp_path):
    adapter = CnstockCompanyChannelAdapter()
    channel = adapter.channels[0]
    indexes = adapter.parse_listing(channel, _listing(), _context(tmp_path))
    assert len(indexes) == 1
    assert indexes[0].published_at == "2026-08-01T13:35:00+08:00"

    article = adapter.parse_detail(channel, indexes[0], _detail(), _context(tmp_path))
    assert article.extraction_method == "next-data-structured"
    assert "完成客户验证" in article.clean_body
    assert adapter.rule_events(channel, article)

    nested = adapter.parse_listing(channel, _nested_listing(), _context(tmp_path / "nested"))
    assert len(nested) == 1


def test_cnstock_fetches_same_host_next_json_and_parses_it(tmp_path):
    requested = []
    context = AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda url: requested.append(url) or _detail_json(),
        now=NOW,
    )
    adapter = CnstockCompanyChannelAdapter()
    channel = adapter.channels[0]
    index = adapter.parse_listing(channel, _listing(), context)[0]

    payload = adapter.fetch_detail(channel, index, context)
    article = adapter.parse_detail(channel, index, payload, context)

    assert requested == [
        "https://www.cnstock.com/_next/data/build_20260801/"
        "commonDetail/754022.json?id=754022"
    ]
    assert "完成客户验证" in article.clean_body


def test_cnstock_closed_day_filter_full_window_and_detail_mismatch(tmp_path):
    adapter = CnstockCompanyChannelAdapter()
    channel = adapter.channels[0]
    old = _listing(_item(day="31"))
    assert adapter.parse_listing(channel, old, _context(tmp_path / "daily")) == []
    assert len(adapter.parse_listing(channel, old, _context(tmp_path / "full", full=True))) == 1

    index = adapter.parse_listing(channel, _listing(), _context(tmp_path / "detail"))[0]
    with pytest.raises(DetailFetchError, match="title mismatch"):
        adapter.parse_detail(channel, index, _detail(title="错误标题"), _context(tmp_path / "bad"))
