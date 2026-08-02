"""Fail-closed adapter for Shanghai's official annual development plans.

The Shanghai Development and Reform Commission publishes one server-rendered
annual plan report per year.  These reports combine the preceding year's
execution review with explicit targets and projects for the plan year.  The
adapter accepts only that exact report family; mid-year execution-only reports
in the same column are deliberately excluded.

Scrapling is used solely to relocate previously verified DOM selectors.  URL,
title/year, publication-date, government-marker, and future-plan invariants
remain deterministic and cannot be relaxed by adaptive matching.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from ..adaptive import AdaptiveSelector
from ..base import (
    AdapterContext,
    AggregateAdapter,
    DetailFetchError,
    ListingInvariantError,
)
from ..industry_rules import IndustryRuleConfig, extract_industry_events
from ..models import CleanArticle, SourceArticleIndex, SourceChannel


_ENTRY = "https://fgw.sh.gov.cn/fgw_ndjh/index.html"
_HOST = "fgw.sh.gov.cn"
_PATH = re.compile(
    r"/fgw_ndjh/(?P<day>20\d{6})/(?P<id>[0-9a-f]{32})\.html"
)
_ANNUAL_TITLE = re.compile(
    r"^关于上海市(?P<executed>20\d{2})年国民经济和社会发展计划执行情况"
    r"与(?P<planned>20\d{2})年国民经济和社会发展计划草案的报告$"
)
_DATE = re.compile(r"(?P<date>20\d{2}[-年]\d{2}[-月]\d{2})")
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问过于频繁|Access Denied|403 Forbidden|"
    r"captcha-container|TTGCaptcha)",
    re.I,
)
_VALIDATED_ROLLING_WINDOW = 4


class ShanghaiFgwAnnualPlanAdapter(AggregateAdapter):
    """Capture only annual reports that contain a verifiable future roadmap."""

    adapter_id = "shanghai_fgw_annual_plan"
    channels = (
        SourceChannel(
            source_id="shanghai-fgw-annual-plan",
            name="上海市发展和改革委员会—年度计划",
            url=_ENTRY,
            source_grade="A",
            event_prior=(
                "policy_or_standard",
                "factory_or_capacity",
                "new_site_or_entity",
                "technical_milestone",
            ),
            allowed_hosts=(_HOST,),
            allowed_path_patterns=(
                r"/fgw_ndjh/20\d{6}/[0-9a-f]{32}\.html",
            ),
        ),
    )
    minimum_listing_count = 4
    maximum_listing_count = 30

    def should_fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
    ) -> bool:
        del channel
        return bool(index.structured_data.get("validated_roadmap_window"))

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        self._reject_interstitial(channel.source_id, html, listing=True)
        adaptive = AdaptiveSelector(
            html,
            url=channel.url,
            storage_path=context.adaptive_db,
        )
        marker = self._meta(adaptive, "SiteIDCode")
        column = self._meta(adaptive, "ColumnName")
        if marker != "3100000087" or column != "年度计划":
            raise ListingInvariantError(
                f"{channel.source_id} official listing marker mismatch"
            )
        selected = adaptive.css(
            "ul.news-list > li",
            identifier=f"{channel.source_id}:listing-items",
            minimum_count=4,
            maximum_count=30,
        )
        if not selected.elements:
            raise ListingInvariantError(
                f"{channel.source_id} listing selector failed closed"
            )

        output: list[SourceArticleIndex] = []
        seen_ids: set[str] = set()
        previous_date = None
        discovered_at = context.now.replace(microsecond=0).isoformat()
        qualified_position = 0
        for page_position, node in enumerate(selected.elements, start=1):
            links = tuple(node.css("a"))
            dates = tuple(node.css("span.time"))
            if len(links) != 1 or len(dates) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} listing title/date cardinality failed"
                )
            title = self.clean_text(
                str(links[0].attrib.get("title") or "")
                or links[0].get_all_text(separator=" ", strip=True)
            )
            title_years = self._title_years(title)
            if title_years is None:
                # The official column also contains execution-only interim
                # reports.  They are audibly outside this roadmap source.
                continue
            executed_year, planned_year = title_years
            qualified_position += 1
            href = str(links[0].attrib.get("href") or "")
            canonical_url = self._canonical(urljoin(channel.url, href))
            article_id = self._article_id(canonical_url)
            published_date = self._parse_date(
                dates[0].get_all_text(separator=" ", strip=True)
            )
            if not canonical_url or not article_id or published_date is None:
                raise ListingInvariantError(
                    f"{channel.source_id} malformed annual-plan listing item"
                )
            if published_date.year != planned_year:
                raise ListingInvariantError(
                    f"{channel.source_id} plan/publication year mismatch"
                )
            if published_date > self._source_today(context.now):
                raise ListingInvariantError(
                    f"{channel.source_id} future-dated listing item"
                )
            if previous_date is not None and published_date > previous_date:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid listing date order"
                )
            previous_date = published_date
            if article_id in seen_ids:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate annual plan {article_id}"
                )
            seen_ids.add(article_id)
            published_at = datetime.combine(
                published_date,
                datetime.min.time(),
                tzinfo=context.now.tzinfo,
            ).isoformat()
            structured = {
                "executed_year": executed_year,
                "planned_year": planned_year,
                "document_type_target": "roadmap",
                "listing_page_position": page_position,
                "validated_roadmap_window": qualified_position
                <= _VALIDATED_ROLLING_WINDOW,
            }
            if qualified_position > _VALIDATED_ROLLING_WINDOW:
                continue
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="annual_plan",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=len(output) + 1,
                    content_hash=self.stable_hash(
                        f"{canonical_url}\n{title}\n{published_at}"
                    ),
                    discovery_method=selected.method,
                    structured_data=structured,
                )
            )
        self.validate_listing(channel, output)
        return output

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        self._reject_interstitial(channel.source_id, html, listing=False)
        if self._article_id(index.canonical_url) != index.source_article_id:
            raise DetailFetchError(
                f"{channel.source_id} detail URL/id mismatch for "
                f"{index.source_article_id}"
            )
        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        if (
            self._meta(adaptive, "SiteIDCode") != "3100000087"
            or self._meta(adaptive, "ColumnName") != "年度计划"
            or self._meta(adaptive, "SiteName") != "上海市发展和改革委员会"
        ):
            raise DetailFetchError(
                f"{channel.source_id} official detail marker mismatch"
            )
        title = adaptive.css(
            "meta[name='ArticleTitle']",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        date = adaptive.css(
            "meta[name='PubDate']",
            identifier=f"{channel.source_id}:detail-date",
            minimum_count=1,
            maximum_count=1,
        )
        body = adaptive.css(
            "div#ivs_content.Article_content",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not title.elements or not date.elements or not body.elements:
            raise DetailFetchError(
                f"{channel.source_id} detail selector failed closed for "
                f"{index.source_article_id}"
            )
        detail_title = self.clean_text(
            str(title.elements[0].attrib.get("content") or "")
        )
        if not self._titles_match(index.title, detail_title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        detail_date = self._parse_date(
            str(date.elements[0].attrib.get("content") or "")
        )
        if detail_date is None or detail_date.isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )
        title_years = self._title_years(detail_title)
        if title_years is None:
            raise DetailFetchError(
                f"{channel.source_id} detail is not an annual-plan report"
            )
        clean_body = self._clean_body(body.elements[0])
        if not 2_000 <= len(clean_body) <= 200_000:
            raise DetailFetchError(
                f"{channel.source_id} detail body length outside safe bounds"
            )
        planned_year = title_years[1]
        roadmap_markers = self._roadmap_markers(clean_body, planned_year)
        if not all(roadmap_markers.values()):
            raise DetailFetchError(
                f"{channel.source_id} explicit future roadmap markers missing"
            )

        method = (
            "adaptive"
            if "adaptive" in {title.method, date.method, body.method}
            else "exact"
        )
        structured = dict(index.structured_data)
        structured.update(
            {
                "detail_published_at": detail_date.isoformat(),
                "document_type": "roadmap",
                "roadmap_plan_year": planned_year,
                "roadmap_markers": roadmap_markers,
            }
        )
        digest = sha256(
            f"{index.title}\n{clean_body}".encode("utf-8")
        ).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=clean_body,
            author=self._meta(adaptive, "ContentSource"),
            tags=("年度计划", "产业规划"),
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={
                "title": "meta:ArticleTitle",
                "published_at": "meta:PubDate",
                "body": "detail:#ivs_content",
                "roadmap": "detail:annual-plan-title+future-section+target-language",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(self, channel, article):
        return extract_industry_events(
            channel,
            article,
            config=IndustryRuleConfig(processor="rules:shanghai-fgw-plan-v1"),
        )

    @staticmethod
    def _title_years(title: str) -> tuple[int, int] | None:
        match = _ANNUAL_TITLE.fullmatch(title)
        if not match:
            return None
        executed = int(match.group("executed"))
        planned = int(match.group("planned"))
        return (executed, planned) if planned == executed + 1 else None

    @staticmethod
    def _roadmap_markers(body: str, planned_year: int) -> dict[str, bool]:
        return {
            "plan_draft_named": bool(
                re.search(
                    rf"{planned_year}年国民经济(?:和|与)社会发展计划草案",
                    body,
                )
            ),
            "future_section": bool(
                re.search(
                    rf"[二三]、\s*{planned_year}年.*?(?:国民经济|经济社会)",
                    body,
                )
            ),
            "explicit_targets": bool(
                re.search(
                    r"主要预期目标|全年目标|计划安排|今年经济社会发展",
                    body,
                )
            ),
        }

    @staticmethod
    def _clean_body(element) -> str:
        blocks: list[str] = []
        seen: set[str] = set()
        for node in element.xpath(
            ".//p | .//h1 | .//h2 | .//h3 | .//h4 | .//li | .//blockquote"
        ):
            text = re.sub(
                r"\s+",
                " ",
                node.get_all_text(separator=" ", strip=True),
            ).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            blocks.append(text)
        return "\n".join(blocks).strip()

    @staticmethod
    def _canonical(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != _HOST:
            return ""
        if not _PATH.fullmatch(parsed.path):
            return ""
        return f"https://{_HOST}{parsed.path}"

    @staticmethod
    def _article_id(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != _HOST:
            return ""
        match = _PATH.fullmatch(parsed.path)
        return match.group("id") if match else ""

    @staticmethod
    def _parse_date(value: str):
        match = _DATE.search(value)
        if not match:
            return None
        try:
            return datetime.strptime(
                match.group("date")
                .replace("年", "-")
                .replace("月", "-")
                .replace("日", ""),
                "%Y-%m-%d",
            ).date()
        except ValueError:
            return None

    @staticmethod
    def _source_today(now: datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(ZoneInfo("Asia/Shanghai")).date()

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s\u200b]", "", expected)
        right = re.sub(r"[\s\u200b]", "", actual)
        return bool(left and right and left == right)

    @staticmethod
    def _meta(adaptive: AdaptiveSelector, name: str) -> str:
        values = tuple(adaptive.selector.css(f"meta[name='{name}']"))
        if len(values) != 1:
            return ""
        return str(values[0].attrib.get("content") or "").strip()

    @staticmethod
    def _reject_interstitial(
        source_id: str,
        html: bytes,
        *,
        listing: bool,
    ) -> None:
        text = html.decode("utf-8", errors="ignore")
        if not _ACCESS_INTERSTITIAL.search(text):
            return
        error = ListingInvariantError if listing else DetailFetchError
        raise error(f"{source_id} access interstitial detected; no bypass attempted")


__all__ = ["ShanghaiFgwAnnualPlanAdapter"]
