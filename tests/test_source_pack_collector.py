from email.message import Message
import json
import urllib.error

from ht_lead_radar.models import Evidence
from ht_lead_radar.source_pack_collector import SourcePackCollector
from ht_lead_radar.source_packs import SourceDefinition, SourcePack, SourcePackRegistry


class FakeResponse:
    def __init__(
        self,
        url: str,
        body: str | bytes,
        *,
        content_type: str = "text/html; charset=utf-8",
        etag: str = "",
        last_modified: str = "",
        status: int = 200,
    ):
        self._url = url
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if etag:
            self.headers["ETag"] = etag
        if last_modified:
            self.headers["Last-Modified"] = last_modified

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class StubOpener:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append(request)
        route = self.routes[request.full_url]
        if callable(route):
            route = route(request)
        if isinstance(route, BaseException):
            raise route
        return route


def _source(
    source_id: str,
    url: str,
    *,
    owner: str = "测试信息源",
    source_type: str = "government",
    grade: str = "A",
    adapter: str = "html_list",
    signals=("funding",),
    tags=("generic",),
    enabled: bool = True,
    status: str = "verified_static_list",
) -> SourceDefinition:
    return SourceDefinition(
        id=source_id,
        name=f"来源-{source_id}",
        owner=owner,
        source_type=source_type,
        grade=grade,
        url=url,
        adapter=adapter,
        signal_types=tuple(signals),
        industry_tags=tuple(tags),
        enabled=enabled,
        verified_on="2026-07-25",
        status=status,
        verification_note="test",
    )


def _registry(
    generic_sources,
    sector_sources=(),
) -> SourcePackRegistry:
    sources = tuple(generic_sources) + tuple(sector_sources)
    packs = [
        SourcePack(
            id="generic-cn",
            name="通用",
            aliases=("generic",),
            industry_tags=("generic",),
            source_ids=tuple(source.id for source in generic_sources),
        )
    ]
    if sector_sources:
        packs.append(SourcePack(
            id="embodied-intelligence-cn",
            name="具身智能",
            aliases=("具身智能", "人形机器人", "embodied intelligence"),
            industry_tags=("embodied_intelligence",),
            source_ids=tuple(source.id for source in sector_sources),
        ))
    registry = SourcePackRegistry(
        version=1,
        verified_on="2026-07-25",
        policy={},
        sources=sources,
        packs=packs,
    )
    registry._validate_cross_references()
    return registry


def test_collects_generic_and_sector_sources_with_provenance_and_health(tmp_path):
    generic = _source(
        "generic-projects",
        "https://gov.example/list",
        signals=("project_buildout", "factory"),
        tags=("generic", "embodied_intelligence"),
    )
    sector_media = _source(
        "robot-news",
        "https://robot.example/news",
        owner="机器人行业协会",
        source_type="industry_association",
        grade="B",
        signals=("funding",),
        tags=("embodied_intelligence",),
    )
    opener = StubOpener({
        generic.url: FakeResponse(
            generic.url,
            '<a href="/project-1">2025年7月20日 具身智能产业基地项目启动</a>',
        ),
        "https://gov.example/project-1": FakeResponse(
            "https://gov.example/project-1",
            "<h1>具身智能产业基地项目</h1><p>建设单位：未来机器人有限公司。项目启动建设。</p>",
        ),
        sector_media.url: FakeResponse(
            sector_media.url,
            '<a href="/round-a">2025年7月21日 示例机器人有限公司完成亿元融资</a>',
        ),
        "https://robot.example/round-a": FakeResponse(
            "https://robot.example/round-a",
            "<h1>示例机器人有限公司完成亿元融资</h1><p>公司将扩充研发团队。</p>",
        ),
    })
    collector = SourcePackCollector(
        registry=_registry([generic], [sector_media]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=opener,
    )

    evidence = collector.collect("具身智能", year=2025, limit_per_query=10)

    assert {item.company for item in evidence} == {"未来机器人有限公司", "示例机器人有限公司"}
    assert {item.source_grade for item in evidence} == {"A", "B"}
    assert all("[" in item.source_name and item.source_name.endswith("]") for item in evidence)
    assert any("[generic-projects]" in item.source_name for item in evidence)
    assert any("[robot-news]" in item.source_name for item in evidence)
    assert collector.last_run_summary["pack_ids"] == [
        "generic-cn", "embodied-intelligence-cn"
    ]
    assert collector.last_run_summary["failed_source_count"] == 0
    assert collector.source_health_summary("具身智能")["healthy_count"] == 2


def test_ambiguous_company_is_kept_as_observation_but_not_evidence(tmp_path):
    source = _source(
        "generic-funding",
        "https://media.example/list",
        grade="B",
        signals=("funding",),
    )
    opener = StubOpener({
        source.url: FakeResponse(
            source.url,
            '<a href="/story">2025年7月20日 具身智能赛道完成新一轮融资</a>',
        ),
        "https://media.example/story": FakeResponse(
            "https://media.example/story",
            "<p>该项目宣布完成融资，但公开页面没有明确披露公司主体。</p>",
        ),
    })
    collector = SourcePackCollector(
        registry=_registry([source]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=opener,
    )

    assert collector.collect("具身智能", year=2025) == []
    observations = collector.load_observations("具身智能")
    assert len(observations) == 1
    assert observations[0].company_candidates == ()
    assert observations[0].source_id == "generic-funding"


def test_listing_and_detail_failures_are_isolated(tmp_path):
    broken = _source("broken", "https://broken.example/list")
    good = _source("good", "https://good.example/list")
    opener = StubOpener({
        broken.url: RuntimeError("temporary source failure"),
        good.url: FakeResponse(
            good.url,
            '<a href="/story">2025年7月20日 未来机器人有限公司完成具身智能融资</a>',
        ),
        "https://good.example/story": RuntimeError("detail unavailable"),
    })
    collector = SourcePackCollector(
        registry=_registry([broken, good]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=opener,
    )

    evidence = collector.collect("具身智能", year=2025)

    assert [item.company for item in evidence] == ["未来机器人有限公司"]
    assert collector.last_run_summary["failed_source_count"] == 1
    assert collector.last_run_summary["sources"]["broken"]["status"] == "error"
    assert collector.last_run_summary["sources"]["good"]["status"] == "ok"
    assert collector.last_run_summary["sources"]["good"]["detail_error_count"] == 1


def test_etag_and_last_modified_are_sent_and_304_reuses_stored_evidence(tmp_path):
    source = _source(
        "official",
        "https://robot.example/news",
        owner="机器人产业协会",
        source_type="industry_association",
    )
    state = {"count": 0, "conditional_headers": None}

    def route(request):
        state["count"] += 1
        if state["count"] == 1:
            return FakeResponse(
                source.url,
                '<a href="/a">2025年7月20日 缓存机器人有限公司完成具身智能融资</a>',
                etag='"version-1"',
                last_modified="Sun, 20 Jul 2025 10:00:00 GMT",
            )
        state["conditional_headers"] = dict(request.header_items())
        headers = Message()
        headers["ETag"] = '"version-1"'
        raise urllib.error.HTTPError(source.url, 304, "Not Modified", headers, None)

    opener = StubOpener({source.url: route})
    collector = SourcePackCollector(
        registry=_registry([source]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=opener,
        detail_fetch=False,
    )

    first = collector.collect("具身智能", year=2025)
    second = collector.collect("具身智能", year=2025)

    assert len(first) == len(second) == 1
    normalized = {key.lower(): value for key, value in state["conditional_headers"].items()}
    assert normalized["if-none-match"] == '"version-1"'
    assert normalized["if-modified-since"] == "Sun, 20 Jul 2025 10:00:00 GMT"
    assert collector.last_run_summary["sources"]["official"]["status"] == "not_modified"


def test_disabled_blocked_and_browser_only_sources_are_never_fetched(tmp_path):
    disabled = _source(
        "blocked",
        "https://blocked.example/list",
        enabled=False,
        status="blocked_automated_access",
    )
    browser = _source(
        "browser-only",
        "https://dynamic.example/list",
        adapter="browser_dynamic_list",
    )
    opener = StubOpener({})
    collector = SourcePackCollector(
        registry=_registry([disabled, browser]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=opener,
    )

    assert collector.collect("具身智能") == []
    assert opener.calls == []
    assert collector.last_run_summary["sources"]["blocked"]["status"] == "disabled"
    assert (
        collector.last_run_summary["sources"]["browser-only"]["status"]
        == "unsupported_adapter"
    )


def test_rss_adapter_emits_industry_media_evidence(tmp_path):
    source = _source(
        "rss-news",
        "https://robot.example/feed.xml",
        owner="机器人产业协会",
        source_type="industry_association",
        adapter="rss",
        signals=("funding",),
    )
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><item>
      <title>订阅机器人股份有限公司完成具身智能融资</title>
      <link>https://robot.example/items/1</link>
      <description>公司将扩大研发投入。</description>
      <pubDate>2025-07-20</pubDate>
    </item></channel></rss>"""
    collector = SourcePackCollector(
        registry=_registry([source]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=StubOpener({
            source.url: FakeResponse(
                source.url, rss, content_type="application/rss+xml; charset=utf-8"
            ),
        }),
        detail_fetch=False,
    )

    evidence = collector.collect("具身智能", year=2025)

    assert len(evidence) == 1
    assert evidence[0].company == "订阅机器人股份有限公司"
    assert evidence[0].event_type == "funding"
    assert evidence[0].event_date == "2025-07-20"


def test_json_feed_observes_signal_mismatch_but_only_emits_supported_event(tmp_path):
    source = _source(
        "json-news",
        "https://media.example/feed.json",
        adapter="json_feed",
        signals=("technology_milestone",),
    )
    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "items": [
            {
                "title": "融资科技有限公司完成具身智能融资",
                "url": "https://media.example/funding",
                "date_published": "2025-07-19",
            },
            {
                "title": "创新科技有限公司发布具身智能样机",
                "url": "https://media.example/product",
                "date_published": "2025-07-20",
            },
        ],
    }
    collector = SourcePackCollector(
        registry=_registry([source]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=StubOpener({
            source.url: FakeResponse(
                source.url,
                json.dumps(feed, ensure_ascii=False),
                content_type="application/feed+json; charset=utf-8",
            ),
        }),
        detail_fetch=False,
    )

    evidence = collector.collect("具身智能", year=2025)

    assert [item.company for item in evidence] == ["创新科技有限公司"]
    assert evidence[0].event_type == "technical_milestone"
    assert len(collector.load_observations("具身智能")) == 2


def test_direct_html_is_incremental_deduplicated_and_year_filtered(tmp_path):
    source = _source(
        "direct-page",
        "https://robot.example/latest",
        owner="机器人产业协会",
        source_type="industry_association",
        adapter="direct_html",
        signals=("funding",),
    )
    page = """
    <html><head><title>公司动态</title></head>
    <body><h1>2025年7月20日 直连机器人有限公司完成具身智能融资</h1>
    <p>本轮资金将用于研发。</p></body></html>
    """
    opener = StubOpener({
        source.url: FakeResponse(source.url, page),
    })
    collector = SourcePackCollector(
        registry=_registry([source]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=opener,
    )

    assert collector.collect("具身智能", year=2024) == []
    first = collector.collect("具身智能", year=2025)
    second = collector.collect("具身智能", year=2025)

    assert len(first) == len(second) == 1
    assert first[0].source_url == source.url
    assert len(collector.load_observations("具身智能")) == 1


def test_early_tender_signal_uses_declared_source_signal_type(tmp_path):
    source = _source(
        "tenders",
        "https://gov.example/tenders",
        signals=("procurement_tender",),
    )
    opener = StubOpener({
        source.url: FakeResponse(
            source.url,
            '<a href="/t1">2025年7月20日 具身智能设备公开招标公告</a>',
        ),
        "https://gov.example/t1": FakeResponse(
            "https://gov.example/t1",
            "<p>招标人：未来机器人有限公司。项目现公开征集。</p>",
        ),
    })
    collector = SourcePackCollector(
        registry=_registry([source]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=opener,
    )

    evidence = collector.collect("具身智能", year=2025)

    assert len(evidence) == 1
    assert evidence[0].company == "未来机器人有限公司"
    assert evidence[0].event_type == "procurement_tender"


def test_explicit_quoted_brand_can_be_attributed_without_legal_suffix(tmp_path):
    source = _source("media", "https://media.example/list")
    collector = SourcePackCollector(
        registry=_registry([source]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=StubOpener({
            source.url: FakeResponse(
                source.url,
                '<a href="/q1">2025年7月20日 “未来智能”完成具身智能融资</a>',
            ),
            "https://media.example/q1": FakeResponse(
                "https://media.example/q1",
                "<p>本轮资金将用于产品研发。</p>",
            ),
        }),
    )

    evidence = collector.collect("具身智能", year=2025)

    assert [item.company for item in evidence] == ["未来智能"]


def test_company_official_cached_evidence_is_not_returned(tmp_path):
    broad = _source(
        "broad",
        "https://media.example/list",
        source_type="vertical_technology_media",
    )
    official = _source(
        "official-company",
        "https://company.example/news",
        owner="特定公司",
        source_type="company_official",
    )
    collector = SourcePackCollector(
        registry=_registry([broad, official]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=StubOpener({
            broad.url: FakeResponse(broad.url, "<html></html>"),
        }),
    )
    collector._store_evidence(official, "具身智能", Evidence(
        company="特定公司",
        event_type="funding",
        phase="upstream",
        event_date="2026-07-20",
        title="特定公司融资",
        snippet="历史缓存",
        source_url="https://company.example/news/1",
        source_name="特定公司官网 [official-company]",
        source_grade="A",
        direction="具身智能",
    ))

    assert collector.collect("具身智能") == []


def test_generic_government_listing_can_defer_topic_match_to_detail(tmp_path):
    source = _source(
        "government-projects",
        "https://gov.example/list",
        source_type="government_industrial_park",
        signals=("project_buildout", "factory"),
    )
    collector = SourcePackCollector(
        registry=_registry([source]),
        state_db=tmp_path / "state.sqlite3",
        urlopen=StubOpener({
            source.url: FakeResponse(
                source.url,
                '<a href="/project">重大项目集中开工</a>',
            ),
            "https://gov.example/project": FakeResponse(
                "https://gov.example/project",
                "半导体生产基地启动，建设主体为北京奕行智能科技有限公司，"
                "项目计划年内投产。",
            ),
        }),
    )

    evidence = collector.collect("半导体")

    assert [item.company for item in evidence] == [
        "北京奕行智能科技有限公司"
    ]
