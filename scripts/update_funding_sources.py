"""Idempotently add the validated funding-source bundle to the registry."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "source-packs.json"
VERIFIED_ON = "2026-07-25"
INDUSTRIES = [
    "generic",
    "brain_computer_interface",
    "semiconductor",
    "commercial_space",
    "fusion",
    "embodied_intelligence",
]


def source(
    source_id: str,
    name: str,
    owner: str,
    source_type: str,
    grade: str,
    url: str,
    adapter: str,
    signal_types: list[str],
    verification_note: str,
    *,
    industry_tags: list[str] | None = None,
) -> dict:
    return {
        "id": source_id,
        "name": name,
        "owner": owner,
        "source_type": source_type,
        "grade": grade,
        "url": url,
        "adapter": adapter,
        "signal_types": signal_types,
        "industry_tags": industry_tags or INDUSTRIES,
        "enabled": True,
        "verified_on": VERIFIED_ON,
        "status": "verified_static_list",
        "verification_note": verification_note,
    }


SOURCES = [
    source(
        "36kr-financing-flash",
        "36氪—融资快报",
        "北京多氪信息科技有限公司",
        "financing_media",
        "B",
        "https://pitchhub.36kr.com/financing-flash",
        "html_list",
        ["funding", "investor", "funding_round", "funding_amount"],
        "公开融资专门列表；2026-07-25 从生产服务器直接 GET 成功，项目现有解析器发现 63 个链接，其中 30 个标题或摘要含融资字段。",
    ),
    source(
        "pedaily-vcpe-events",
        "投资界—投资事件",
        "清科创业",
        "financing_media",
        "B",
        "https://www.pedaily.cn/vcpeevent/",
        "html_list",
        ["funding", "investor", "funding_round", "funding_amount", "merger_acquisition"],
        "公开投资事件专门列表；比行业资讯频道更接近逐笔融资入口。2026-07-25 生产服务器直接 GET 与项目解析均通过。",
    ),
    source(
        "cyzone-financing",
        "创业邦—首页融资增量",
        "爱奇清科（北京）信息科技有限公司",
        "financing_media",
        "B",
        "https://www.cyzone.cn/",
        "html_homepage_list",
        ["funding", "investor", "funding_round", "funding_amount", "fund_report"],
        "旧 capital.cyzone.cn 专题已停更；专用适配器从当前首页互斥投影带融资标签的每日增量，并与最新资讯共享一次 GET。",
    ),
    source(
        "cyzone-latest",
        "创业邦—最新资讯",
        "爱奇清科（北京）信息科技有限公司",
        "financing_media",
        "B",
        "https://www.cyzone.cn/",
        "html_homepage_list",
        ["funding", "investor", "funding_round", "funding_amount", "company_activity"],
        "当前首页的非融资标签资讯投影；融资标签条目由 cyzone-financing 互斥接管，同一轮不重复抓取或语义处理。",
    ),
    source(
        "lieyunpro-archives",
        "猎云网—文章归档",
        "猎云网",
        "financing_media",
        "B",
        "https://lieyunpro.com/archives",
        "html_list",
        ["funding", "investor", "funding_round", "funding_amount", "company_activity"],
        "新域名公开静态归档；旧 lieyunwang 品牌已迁移。归档混合非融资文章，依赖融资事件过滤和跨源去重。",
    ),
    source(
        "vbdata-funding",
        "动脉网—投融资",
        "动脉网",
        "vertical_financing_media",
        "B",
        "https://www.vbdata.cn/articleList?tag=5512",
        "html_list",
        ["funding", "investor", "funding_round", "funding_amount", "clinical", "regulatory_approval"],
        "医疗健康投融资垂直补漏入口；2026-07-25 生产服务器直接 GET 与项目解析均通过。",
        industry_tags=["generic", "brain_computer_interface"],
    ),
    source(
        "jazzyear-latest",
        "甲子光年—最新文章",
        "甲子光年",
        "vertical_technology_media",
        "B",
        "https://www.jazzyear.com/",
        "html_homepage_list",
        ["funding", "investor", "technology_milestone", "commercialization", "company_activity"],
        "硬科技垂直补漏源，没有融资专门 Feed；从公开首页文章流按融资关键词筛选，选择性强、不能承担全量发现。",
        industry_tags=["generic", "semiconductor", "commercial_space", "fusion", "embodied_intelligence"],
    ),
    source(
        "zhidx-financing",
        "智东西—融投资消息",
        "智东西",
        "vertical_technology_media",
        "B",
        "https://zhidx.com/p/category/%E8%9E%8D%E6%8A%95%E8%B5%84%E6%B6%88%E6%81%AF",
        "html_list",
        ["funding", "investor", "funding_round", "funding_amount", "semiconductor"],
        "半导体、AI 硬件垂直补漏；公开 WordPress 风格列表可直接读取，但频道含历史内容，日期窗口必须生效。",
        industry_tags=["generic", "semiconductor", "embodied_intelligence"],
    ),
    source(
        "qimingvc-newsroom",
        "启明创投—新闻中心",
        "启明创投",
        "investor_official",
        "A",
        "https://www.qimingvc.com/cn/newsroom?news_tid=All",
        "html_list",
        ["funding", "investor", "investor_comment", "portfolio_company", "fund_launch"],
        "投资机构官方反向发现与一手核验入口；只覆盖该机构参与或关注的项目，不能替代综合融资媒体。",
    ),
    source(
        "crunchbase-news-venture-rss",
        "Crunchbase News—Venture RSS",
        "Crunchbase News",
        "global_financing_media",
        "B",
        "https://news.crunchbase.com/sections/venture/feed/",
        "rss",
        ["funding", "investor", "funding_round", "funding_amount", "venture_market"],
        "公开 Venture RSS，仅指 Crunchbase News 新闻内容，不等同于 Crunchbase 商业数据库 API 或数据许可。2026-07-25 生产服务器返回 RSS XML。",
    ),
    source(
        "techcrunch-venture-rss",
        "TechCrunch—Venture RSS",
        "TechCrunch",
        "global_financing_media",
        "B",
        "https://techcrunch.com/category/venture/feed/",
        "rss",
        ["funding", "investor", "funding_round", "funding_amount", "venture_market"],
        "公开 Venture RSS，用于全球科技融资补漏；2026-07-25 生产服务器返回 RSS XML，项目解析发现 19 条、其中 15 条为融资相关。",
    ),
]


def main() -> None:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    payload["version"] = max(int(payload.get("version", 1)), 2)
    new_ids = [item["id"] for item in SOURCES]
    existing = {item["id"]: item for item in payload["sources"]}
    for item in SOURCES:
        existing[item["id"]] = item
    original_ids = [
        item["id"] for item in payload["sources"] if item["id"] not in new_ids
    ]
    insert_at = original_ids.index("pedaily-investment-news")
    ordered_ids = original_ids[:insert_at] + new_ids + original_ids[insert_at:]
    payload["sources"] = [existing[source_id] for source_id in ordered_ids]
    generic = next(pack for pack in payload["packs"] if pack["id"] == "generic-cn")
    retained = [source_id for source_id in generic["source_ids"] if source_id not in new_ids]
    insert_at = retained.index("pedaily-investment-news")
    generic["source_ids"] = retained[:insert_at] + new_ids + retained[insert_at:]
    REGISTRY.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
