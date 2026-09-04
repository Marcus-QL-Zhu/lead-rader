import sqlite3
import json
from io import BytesIO
import time

import pytest

from ht_lead_radar.collectors import (
    _event_date_from_text,
    collect_josint,
    company_local_context,
    company_mentioned,
    extract_company,
    infer_event,
    infer_routes_from_text,
    load_replay,
    normalize_replayed_route,
    result_relevant_to_company,
    SearXNGCollector,
    BingRSSCollector,
)


def test_event_and_company_extraction():
    assert extract_company("戴盟机器人完成亿元A轮融资") == "戴盟机器人"
    assert infer_event("公司完成亿元融资并计划扩产")[0] == "factory_or_capacity"


def test_route_inference_preserves_source_and_needs_review():
    routes = infer_routes_from_text(
        "戴盟机器人完成融资，由汇川产投与中国电信联合投资。",
        "https://example.com/evidence",
    )
    assert routes
    assert routes[0].kind == "investor"
    assert routes[0].evidence_url == "https://example.com/evidence"
    assert routes[0].grade == "C"
    equity_routes = infer_routes_from_text(
        "联想创投等入股戴盟机器人",
        "https://example.com/equity",
        company="戴盟机器人",
    )
    assert equity_routes[0].target == "联想创投"


def test_false_people_and_generic_job_results_are_rejected():
    routes = infer_routes_from_text(
        "创始人之一毕业于具身智能行业及产业链顶尖企业。",
        "https://example.com/noise",
    )
    assert not [
        route
        for route in routes
        if route.kind in {"alumni_or_academic", "former_colleague"}
    ]
    assert company_mentioned("戴盟机器人", "供应链总监高薪职位") is False
    assert company_mentioned("戴盟机器人", "戴盟机器人供应链总监") is True


def test_invalid_year_range_is_not_parsed_as_a_date():
    assert _event_date_from_text("预计2030-2035年爆发", "2026-07-24") == "2026-07-24"


def test_company_local_context_blocks_other_company_event_leakage():
    title = "经纬被投企业大事记"
    snippet = "银河通用完成融资。钛虎机器人亮相 CES，展示新一代灵巧手。"
    context = company_local_context("钛虎机器人", title, snippet, window=10)
    assert "钛虎机器人" in context
    assert "融资" not in context
    assert infer_event(context)[0] == "technical_milestone"


def test_markdown_chinese_date_and_route_normalization():
    assert _event_date_from_text("**2026** 年 3 月 26 日发布", "") == "2026-03-26"
    normalized = normalize_replayed_route(
        {
            "kind": "former_colleague",
            "target": "左家平为精—达闼机器人、九号机器人担任技术负责人",
        }
    )
    assert normalized["target"] == "左家平—达闼机器人、九号机器人"
    investor = normalize_replayed_route({"kind": "investor", "target": "昆仲资本独家"})
    assert investor["target"] == "昆仲资本"
    assert _event_date_from_text("预计2030-2035年爆发", "2030-20-01") == ""


def test_competitor_title_result_is_rejected():
    context = result_relevant_to_company(
        "戴盟机器人",
        "灵巧手领域独角兽「灵心巧手」完成A++轮融资",
        "戴盟机器人孵化于香港科技大学科研团队。",
    )
    assert context == ""


def test_searxng_adapter_uses_existing_json_api(monkeypatch):
    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    payload = json.dumps(
        {
            "results": [
                {
                    "title": "戴盟机器人完成融资",
                    "url": "https://example.com/news",
                    "content": "本轮融资用于量产",
                    "publishedDate": "2026-06-04",
                }
            ]
        }
    ).encode()
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: Response(payload)
    )
    result = SearXNGCollector().search("戴盟机器人 融资", limit=3)[0]
    assert result.title == "戴盟机器人完成融资"
    assert result.published_at == "2026-06-04"


def test_search_collector_dns_connect_has_real_wall_clock_boundary(monkeypatch):
    def stuck_open(*_args, **_kwargs):
        time.sleep(0.5)
        return BytesIO(b"<rss/>")

    monkeypatch.setattr("urllib.request.urlopen", stuck_open)
    collector = BingRSSCollector(timeout=0.02)

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="connect"):
        collector.search("hardtech", limit=1)
    assert time.monotonic() - started < 0.15


def test_replay_removes_generic_job_ads_and_bad_routes(tmp_path):
    replay = tmp_path / "live.json"
    replay.write_text(
        json.dumps(
            [
                {
                    "company": "戴盟机器人",
                    "evidence": [
                        {
                            "event_type": "funding",
                            "phase": "strategy_capital",
                            "event_date": "2026-06-04",
                            "title": "戴盟机器人完成亿元A轮融资",
                            "snippet": "本轮由联想创投投资，用于模型研发和量产",
                            "source_url": "https://example.com/funding",
                            "source_name": "source",
                            "source_grade": "C",
                        },
                        {
                            "event_type": "job_ad",
                            "phase": "recruit",
                            "event_date": "2026-05-01",
                            "title": "供应链总监招聘",
                            "snippet": "某高端制造公司",
                            "source_url": "https://example.com/generic-job",
                            "source_name": "source",
                            "source_grade": "C",
                        },
                    ],
                    "outreach_routes": [
                        {
                            "kind": "alumni",
                            "target": "之一—某大学",
                            "path": "x",
                            "evidence_url": "u",
                            "grade": "C",
                            "note": "n",
                        },
                        {
                            "kind": "investor",
                            "target": "联想创投",
                            "path": "x",
                            "evidence_url": "u",
                            "grade": "C",
                            "note": "n",
                        },
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    evidence, metadata = load_replay(replay, "灵巧手")
    assert len(evidence) == 1
    assert evidence[0].event_type == "factory_or_capacity"
    assert [route["target"] for route in metadata["routes"]["戴盟机器人"]] == [
        "联想创投"
    ]


def test_josint_adapter_accepts_real_field_variants_and_filters_seniority(tmp_path):
    database = tmp_path / "jobs.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE jobs ("
        "source_name TEXT, title TEXT, canonical_url TEXT, url TEXT, jd_text TEXT, "
        "company_description TEXT, published TEXT, first_seen TEXT)"
    )
    connection.executemany(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "agency",
                "灵巧手制造总监",
                "https://example.com/1",
                "",
                "全面负责团队搭建",
                "北京示例机器人公司",
                "2026-07-20",
                "2026-07-20",
            ),
            (
                "agency",
                "灵巧手高级经理",
                "https://example.com/2",
                "",
                "负责项目",
                "北京示例机器人公司",
                "2026-07-20",
                "2026-07-20",
            ),
        ],
    )
    connection.commit()
    connection.close()

    evidence = collect_josint(database, "灵巧手")
    assert len(evidence) == 1
    assert evidence[0].title == "灵巧手制造总监"
    assert evidence[0].phase == "marketed_competitive"


def test_josint_multi_topic_union_reads_both_topics_once(tmp_path):
    database = tmp_path / "jobs-multi.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE jobs ("
        "source_name TEXT, title TEXT, canonical_url TEXT, url TEXT, "
        "jd_text TEXT, company_description TEXT, published TEXT, "
        "first_seen TEXT)"
    )
    connection.executemany(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "agency",
                "半导体制造总监",
                "https://example.com/chip",
                "",
                "全面负责团队搭建",
                "示例芯片公司",
                "2026-07-20",
                "2026-07-20",
            ),
            (
                "agency",
                "具身智能算法总监",
                "https://example.com/robot",
                "",
                "全面负责团队搭建",
                "示例机器人公司",
                "2026-07-20",
                "2026-07-20",
            ),
        ],
    )
    connection.commit()
    connection.close()

    evidence = collect_josint(
        database,
        "硬科技组合",
        topics=("具身智能", "半导体"),
    )

    assert {item.title for item in evidence} == {
        "半导体制造总监",
        "具身智能算法总监",
    }
