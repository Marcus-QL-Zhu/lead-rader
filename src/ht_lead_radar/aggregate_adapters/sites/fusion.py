"""Dedicated, fail-closed public adapters for China's fusion news sources.

The two sites deliberately have separate DOM contracts despite sharing the
same industry.  Scrapling adaptive selection is used only to relocate the
known title/list/body containers; the URL, date, title, and content
invariants below remain source-specific and mandatory.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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
from ..industry_rules import IndustryRuleConfig, extract_media_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_CHINA = ZoneInfo("Asia/Shanghai")
_OVERLAP_DAYS = 2
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问过于频繁|Access Denied|403 Forbidden)",
    re.I,
)
_ITER_PATH = re.compile(r"/picnews/info/(20\d{2})/(\d+)\.html")
_MEDIA_PATH = re.compile(r"/blog/([A-Za-z0-9][A-Za-z0-9-]{2,127})")
_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_MEDIA_PUBLISHED_AT = re.compile(
    r'"publishedAt"\s*:\s*"([^"\\]+)"'
)
_BODY_NOISE = re.compile(
    r"^(?:参考链接[:：]?|相关阅读[:：]?|更多推荐[:：]?|"
    r"本文(?:来源|作者)[:：]?|版权声明[:：]?)",
    re.I,
)


class _FusionAdapter(AggregateAdapter):
    """Shared validation and deterministic industry-rule wiring."""

    minimum_listing_count = 0
    maximum_listing_count = 100

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        return extract_media_events(
            channel,
            article,
            config=IndustryRuleConfig(processor=f"rules:{self.adapter_id}-v1"),
            funding_processor=f"rules:{self.adapter_id}-funding-v1",
        )

    @staticmethod
    def _source_now(now: datetime) -> datetime:
        if now.tzinfo is None:
            return now.replace(tzinfo=_CHINA)
        return now.astimezone(_CHINA)

    @classmethod
    def _source_today(cls, now: datetime):
        return cls._source_now(now).date()

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        truncated = expected.rstrip().endswith(("...", "\u2026"))
        left = "".join(character.casefold() for character in expected if character.isalnum())
        right = "".join(character.casefold() for character in actual if character.isalnum())
        return bool(
            left
            and right
            and (
                (truncated and right.startswith(left))
                or left == right
                or left in right
                or right in left
            )
        )

    @staticmethod
    def _first_text(node: object, selector: str) -> str:
        elements = tuple(node.css(selector))
        if not elements:
            return ""
        return AggregateAdapter.clean_text(
            elements[0].get_all_text(separator=" ", strip=True)
        )

    @staticmethod
    def _reject_interstitial(source_id: str, html: bytes, *, listing: bool) -> None:
        if not _ACCESS_INTERSTITIAL.search(html.decode("utf-8", errors="ignore")):
            return
        error = (
            f"{source_id} access interstitial detected; no bypass attempted"
        )
        if listing:
            raise ListingInvariantError(error)
        raise DetailFetchError(error)

    @staticmethod
    def _clean_blocks(container: object) -> str:
        blocks: list[str] = []
        seen: set[str] = set()
        for block in container.xpath(".//p | .//h2 | .//h3 | .//blockquote | .//li"):
            text = AggregateAdapter.clean_text(
                block.get_all_text(separator=" ", strip=True)
            )
            if (
                not text
                or text in seen
                or _BODY_NOISE.search(text)
                or re.fullmatch(r"https?://\S+", text)
            ):
                continue
            seen.add(text)
            blocks.append(text)
        return AggregateAdapter.clean_text(" ".join(blocks))


class IterChinaAdapter(_FusionAdapter):
    """Public latest-news list operated by the ITER China domestic agency."""

    adapter_id = "iter_china"
    channels = (
        SourceChannel(
            source_id="iter-china-news",
            name="中国国际核聚变能源计划执行中心—新闻动态",
            url="https://www.iterchina.cn/picnews/index.html",
            source_grade="A",
            event_prior=(
                "procurement_tender",
                "major_order",
                "partnership",
                "policy_or_standard",
                "technical_milestone",
            ),
            allowed_hosts=("www.iterchina.cn", "iterchina.cn"),
            allowed_path_patterns=(r"/picnews/info/20\d{2}/\d+\.html",),
        ),
    )

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        self._reject_interstitial(channel.source_id, html, listing=True)
        adaptive = AdaptiveSelector(html, url=channel.url, storage_path=context.adaptive_db)
        selection = adaptive.css(
            "div.neiye-list.tuwen > ul#content > li",
            identifier=f"{channel.source_id}:listing-item",
            minimum_count=1,
            maximum_count=100,
        )
        if not selection.elements:
            raise ListingInvariantError(f"{channel.source_id} listing selector failed closed")

        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        previous: datetime | None = None
        discovered_at = context.now.replace(microsecond=0).isoformat()
        cutoff = (
            None
            if context.capture_full_visible_window
            else self._source_today(context.now) - timedelta(days=_OVERLAP_DAYS)
        )
        for position, item in enumerate(selection.elements, start=1):
            links = tuple(item.css("a.db[href]"))
            titles = tuple(item.css("div.tuwen-list > div.title"))
            dates = tuple(item.css("div.tuwen-list > div.date-s"))
            if len(links) != 1 or len(titles) != 1 or len(dates) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} title/date/link cardinality failed"
                )
            canonical_url = self._canonical_url(urljoin(channel.url, str(links[0].attrib.get("href") or "")))
            article_id = self._article_id(canonical_url)
            title = self.clean_text(titles[0].get_all_text(separator=" ", strip=True))
            summary = self.clean_text(self._first_text(item, "div.tuwen-list > div.des"))
            published = self._parse_date(
                dates[0].get_all_text(separator=" ", strip=True)
            )
            if not article_id or article_id in seen or not title or published is None:
                raise ListingInvariantError(f"{channel.source_id} invalid item at {position}")
            if published > self._source_now(context.now):
                raise ListingInvariantError(f"{channel.source_id} article {article_id} is future dated")
            if previous is not None and published > previous:
                raise ListingInvariantError(f"{channel.source_id} listing is not newest-first at {position}")
            previous = published
            seen.add(article_id)
            if cutoff is not None and published.date() < cutoff:
                continue
            published_at = published.isoformat()
            structured = {"summary": summary, "page_position": position}
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="latest-news",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=len(output) + 1,
                    content_hash=self.stable_hash("\n".join((canonical_url, title, published_at, summary))),
                    discovery_method=selection.method,
                    summary=summary,
                    structured_data=structured,
                )
            )
        self.validate_listing(channel, output)
        return output

    def parse_detail(
        self, channel: SourceChannel, index: SourceArticleIndex, html: bytes, context: AdapterContext
    ) -> CleanArticle:
        self._reject_interstitial(channel.source_id, html, listing=False)
        adaptive = AdaptiveSelector(html, url=index.canonical_url, storage_path=context.adaptive_db)
        title = adaptive.css("h4.detail-title", identifier=f"{channel.source_id}:detail-title", minimum_count=1, maximum_count=1)
        date = adaptive.css("div.flex-boxs > span", identifier=f"{channel.source_id}:detail-date", minimum_count=1, maximum_count=3)
        body = adaptive.css("div.neiye-detail#detailsCont", identifier=f"{channel.source_id}:detail-body", minimum_count=1, maximum_count=1)
        if not title.elements or not date.elements or not body.elements:
            raise DetailFetchError(f"{channel.source_id} detail selector failed closed for {index.source_article_id}")
        detail_title = self.clean_text(title.elements[0].get_all_text(separator=" ", strip=True))
        if not self._titles_match(index.title, detail_title):
            raise DetailFetchError(f"{channel.source_id} detail title mismatch for {index.source_article_id}")
        detail_date = next((match.group(0) for element in date.elements if (match := _DATE.search(self.clean_text(element.get_all_text(separator=" ", strip=True))))), "")
        if detail_date != index.published_at[:10]:
            raise DetailFetchError(f"{channel.source_id} listing/detail date mismatch for {index.source_article_id}")
        clean_body = self._clean_blocks(body.elements[0])
        if len(clean_body) < 80:
            raise DetailFetchError(f"{channel.source_id} detail body too short for {index.source_article_id}")
        methods = {title.method, date.method, body.method}
        structured = {**index.structured_data, "detail_published_at": detail_date}
        return CleanArticle(
            index=index, clean_body=clean_body, structured_data=structured,
            extraction_method="adaptive" if "adaptive" in methods else "exact",
            adaptive_similarity=72 if "adaptive" in methods else None,
            evidence_locators={"title": "detail:h4.detail-title", "published_at": "detail:div.flex-boxs>span", "body": "detail:div.neiye-detail#detailsCont"},
            fetch_status="ok", content_hash=sha256(f"{index.title}\n{clean_body}".encode()).hexdigest(),
        )

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        match = _ITER_PATH.fullmatch(parsed.path)
        if not match or (parsed.hostname or "").lower() not in {"iterchina.cn", "www.iterchina.cn"}:
            return url
        return f"https://www.iterchina.cn/picnews/info/{match.group(1)}/{match.group(2)}.html"

    @staticmethod
    def _article_id(url: str) -> str:
        match = _ITER_PATH.fullmatch(urlparse(url).path)
        return match.group(2) if match else ""

    @staticmethod
    def _parse_date(value: str) -> datetime | None:
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=_CHINA)
        except ValueError:
            return None


class FusionIndustryMediaAdapter(_FusionAdapter):
    """Public homepage cards and hosted articles from nuclear-fusion.com.cn."""

    adapter_id = "fusion_industry_media"
    channels = (
        SourceChannel(
            source_id="fusion-industry-media",
            name="可控核聚变—行业垂直媒体",
            url="https://www.nuclear-fusion.com.cn/",
            source_grade="B",
            event_prior=(
                "funding", "factory_or_capacity", "major_order", "partnership",
                "policy_or_standard", "technical_milestone",
            ),
            allowed_hosts=("www.nuclear-fusion.com.cn", "nuclear-fusion.com.cn"),
            allowed_path_patterns=(r"/blog/[A-Za-z0-9][A-Za-z0-9-]{2,127}",),
        ),
    )

    def parse_listing(
        self, channel: SourceChannel, html: bytes, context: AdapterContext
    ) -> list[SourceArticleIndex]:
        self._reject_interstitial(channel.source_id, html, listing=True)
        adaptive = AdaptiveSelector(html, url=channel.url, storage_path=context.adaptive_db)
        selection = adaptive.css(
            "div.s-blog-posts > div.s-blog-entry",
            identifier=f"{channel.source_id}:listing-card", minimum_count=1, maximum_count=100,
        )
        if not selection.elements:
            raise ListingInvariantError(f"{channel.source_id} listing selector failed closed")
        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        previous: datetime | None = None
        cutoff = (
            None
            if context.capture_full_visible_window
            else self._source_today(context.now) - timedelta(days=_OVERLAP_DAYS)
        )
        discovered_at = context.now.replace(microsecond=0).isoformat()
        for position, card in enumerate(selection.elements, start=1):
            links = tuple(card.css("div.s-blog-title > a[href]"))
            dates = tuple(card.css("span.s-blog-date"))
            summaries = tuple(card.css("div.s-blog-details-blurb"))
            tags = tuple(card.css("span.s-blog-tag"))
            if len(links) != 1 or len(dates) != 1 or len(summaries) != 1:
                raise ListingInvariantError(f"{channel.source_id} card {position} title/date/summary cardinality failed")
            canonical_url = self._canonical_url(urljoin(channel.url, str(links[0].attrib.get("href") or "")))
            article_id = self._article_id(canonical_url)
            title = self.clean_text(links[0].get_all_text(separator=" ", strip=True))
            summary = self.clean_text(summaries[0].get_all_text(separator=" ", strip=True))
            published = self._parse_date(dates[0].get_all_text(separator=" ", strip=True))
            if not article_id or article_id in seen or not title or len(summary) < 12 or published is None:
                raise ListingInvariantError(f"{channel.source_id} invalid card at {position}")
            if published > self._source_now(context.now):
                raise ListingInvariantError(f"{channel.source_id} article {article_id} is future dated")
            if previous is not None and published > previous:
                raise ListingInvariantError(f"{channel.source_id} listing is not newest-first at {position}")
            previous = published
            seen.add(article_id)
            if cutoff is not None and published.date() < cutoff:
                continue
            published_at = published.isoformat()
            labels = tuple(dict.fromkeys(self.clean_text(tag.get_all_text(separator=" ", strip=True)) for tag in tags if self.clean_text(tag.get_all_text(separator=" ", strip=True))))
            structured = {"tags": labels, "page_position": position}
            output.append(SourceArticleIndex(
                source_id=channel.source_id, source_article_id=article_id, channel="homepage-media",
                canonical_url=canonical_url, title=title, published_at=published_at, discovered_at=discovered_at,
                cursor_value=f"{published_at}|{article_id}", listing_page=channel.url,
                listing_position=len(output) + 1, discovery_method=selection.method, summary=summary,
                structured_data=structured,
                content_hash=self.stable_hash("\n".join((canonical_url, title, published_at, summary, repr(labels)))),
            ))
        self.validate_listing(channel, output)
        return output

    def parse_detail(
        self, channel: SourceChannel, index: SourceArticleIndex, html: bytes, context: AdapterContext
    ) -> CleanArticle:
        self._reject_interstitial(channel.source_id, html, listing=False)
        adaptive = AdaptiveSelector(html, url=index.canonical_url, storage_path=context.adaptive_db)
        title = adaptive.css("div.s-blog-header-content h1", identifier=f"{channel.source_id}:detail-title", minimum_count=1, maximum_count=1)
        body = adaptive.css(
            "div.s-blog-content > div.s-blog-body:not(.s-blog-footer)",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not title.elements or not body.elements:
            raise DetailFetchError(f"{channel.source_id} detail selector failed closed for {index.source_article_id}")
        detail_title = self.clean_text(title.elements[0].get_all_text(separator=" ", strip=True))
        if not self._titles_match(index.title, detail_title):
            raise DetailFetchError(f"{channel.source_id} detail title mismatch for {index.source_article_id}")
        published = _MEDIA_PUBLISHED_AT.search(
            html.decode("utf-8", errors="ignore")
        )
        try:
            published_date = (
                datetime.fromisoformat(published.group(1))
                .astimezone(_CHINA)
                .date()
                .isoformat()
                if published is not None
                else ""
            )
        except ValueError:
            published_date = ""
        if published_date != index.published_at[:10]:
            raise DetailFetchError(f"{channel.source_id} listing/detail date mismatch for {index.source_article_id}")
        clean_body = self._clean_blocks(body.elements[0])
        if len(clean_body) < 80:
            listing_body = self.clean_text(f"{index.title} {index.summary}")
            if len(listing_body) < 20:
                raise DetailFetchError(f"{channel.source_id} detail body too short for {index.source_article_id}")
            return CleanArticle(
                index=index,
                clean_body=listing_body,
                tags=tuple(index.structured_data.get("tags") or ()),
                structured_data={
                    **index.structured_data,
                    "detail_published_at": published_date,
                    "detail_fallback": "hosted_detail_contains_no_extractable_text",
                },
                extraction_method="listing-headline-summary-fallback",
                evidence_locators={
                    "title": "listing:div.s-blog-title>a",
                    "published_at": "listing:span.s-blog-date",
                    "body": "listing:title+summary",
                },
                fetch_status="listing_complete",
                failure_reason="hosted_detail_contains_no_extractable_text",
                content_hash=sha256(
                    f"{index.title}\n{listing_body}".encode()
                ).hexdigest(),
            )
        methods = {title.method, body.method}
        return CleanArticle(
            index=index, clean_body=clean_body, tags=tuple(index.structured_data.get("tags") or ()),
            structured_data={
                **index.structured_data,
                "detail_published_at": published_date,
            },
            extraction_method="adaptive" if "adaptive" in methods else "exact",
            adaptive_similarity=72 if "adaptive" in methods else None,
            evidence_locators={"title": "detail:div.s-blog-header-content h1", "published_at": "detail:blogPostMeta.publishedAt", "body": "detail:div.s-blog-content>div.s-blog-body:not(.s-blog-footer)"},
            fetch_status="ok", content_hash=sha256(f"{index.title}\n{clean_body}".encode()).hexdigest(),
        )

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        match = _MEDIA_PATH.fullmatch(parsed.path)
        if not match or (parsed.hostname or "").lower() not in {"nuclear-fusion.com.cn", "www.nuclear-fusion.com.cn"}:
            return url
        return f"https://www.nuclear-fusion.com.cn/blog/{match.group(1)}"

    @staticmethod
    def _article_id(url: str) -> str:
        match = _MEDIA_PATH.fullmatch(urlparse(url).path)
        return match.group(1) if match else ""

    @staticmethod
    def _parse_date(value: str) -> datetime | None:
        match = re.fullmatch(r"\s*(20\d{2})年(\d{1,2})月(\d{1,2})日\s*", value)
        if match is None:
            return None
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=_CHINA)
        except ValueError:
            return None


__all__ = ["FusionIndustryMediaAdapter", "IterChinaAdapter"]
