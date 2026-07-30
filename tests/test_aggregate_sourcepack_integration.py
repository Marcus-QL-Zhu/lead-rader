from email.message import Message

from ht_lead_radar.source_pack_collector import SourcePackCollector
from ht_lead_radar.source_packs import (
    SourceDefinition,
    SourcePack,
    SourcePackRegistry,
)


class Response:
    def __init__(self, url, body):
        self.url = url
        self.body = body
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_source_pack_routes_36kr_through_dedicated_adapter(tmp_path):
    source = SourceDefinition(
        id="36kr-financing-flash",
        name="36氪—融资快报",
        owner="36氪",
        source_type="financing_media",
        grade="B",
        url="https://pitchhub.36kr.com/financing-flash",
        adapter="html_list",
        signal_types=("funding",),
        industry_tags=("generic", "semiconductor"),
        enabled=True,
        verified_on="2026-07-29",
        status="verified_static_list",
        verification_note="test",
    )
    registry = SourcePackRegistry(
        version=1,
        verified_on="2026-07-29",
        policy={},
        sources=(source,),
        packs=(
            SourcePack(
                id="generic-cn",
                name="通用",
                aliases=("通用",),
                industry_tags=("generic",),
                source_ids=(source.id,),
            ),
        ),
    )
    items = []
    routes = {}
    for number in range(1, 6):
        article_id = f"900{number}"
        title = f"测试芯片{number}完成1亿元A轮融资"
        items.append(
            f"""
            <div class="css-xle9x">
              <div class="item-title">
                <span class="type">快讯</span>
                <a class="title" href="//36kr.com/newsflashes/{article_id}">{title}</a>
              </div>
              <div class="item-desc">{title}，资金用于研发。</div>
              <div class="project-card-wrp">
                <div class="right-top">
                  <div class="title">测试芯片{number}</div>
                  <div class="tag fin-tag">A轮</div>
                </div>
                <div class="right-bottom">半导体芯片企业</div>
              </div>
              <span class="time">2026-07-29</span>
            </div>
            """
        )
        routes[f"https://36kr.com/newsflashes/{article_id}"] = (
            f"""
            <html><head><meta name="description"
              content="测试芯片{number}完成1亿元A轮融资，资金用于半导体芯片研发和量产。"></head>
              <body><div class="newsflash-item">
                测试芯片{number}完成1亿元A轮融资，资金用于半导体芯片研发和量产。
              </div></body>
            </html>
            """.encode()
        )
    routes[source.url] = f"<html>{''.join(items)}</html>".encode()

    def urlopen(request, timeout):
        del timeout
        return Response(request.full_url, routes[request.full_url])

    collector = SourcePackCollector(
        registry=registry,
        state_db=tmp_path / "state.sqlite3",
        urlopen=urlopen,
        dedicated_llm_runner=False,
    )
    evidence = collector.collect("semiconductor", year=2026, limit_per_query=1)

    assert len(evidence) == 5
    assert {item.event_type for item in evidence} == {"funding"}
    source_health = collector.last_run_summary["sources"][source.id]
    assert source_health["discovered_count"] == 5
    assert source_health["evidence_count"] == 5
    assert collector.last_run_summary["dedicated_semantic_mode"] == "rules_only"
    collector.close()
