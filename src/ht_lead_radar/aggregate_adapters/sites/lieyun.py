"""Dedicated, fail-closed adapter for Lieyun's public article archive."""

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
from ..finance_rules import FundingRuleConfig, extract_funding_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_ARTICLE_PATH = re.compile(r"/archives/(\d+)")
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|验证您是真人)",
    re.I,
)
_COMPANY_BEFORE_EVENT = re.compile(
    r"(?P<company>[\u4e00-\u9fffA-Za-z0-9·（）()\-]{2,40}?)"
    r"(?:已|宣布|正式|再度|又)?(?:完成|获|获得|斩获)"
)
_DESCRIPTOR_PREFIX = re.compile(
    r"^.*?(?:研发商|服务商|提供商|解决方案商|运营商|制造商|开发商|企业|公司)"
)


class LieyunAdapter(AggregateAdapter):
    """Enumerate the whole visible archive window, then parse article facts."""

    adapter_id = "lieyun"
    channels = (
        SourceChannel(
            source_id="lieyunpro-archives",
            name="猎云网—文章归档",
            url="https://lieyunpro.com/archives",
            source_grade="B",
            event_prior=("funding",),
            allowed_hosts=("lieyunpro.com", "www.lieyunpro.com"),
            allowed_path_patterns=(r"/archives/\d+",),
        ),
    )
    minimum_listing_count = 5
    maximum_listing_count = 120
    archive_page_size = 20
    maximum_archive_pages = 6

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        output: list[SourceArticleIndex] = []
        seen_ids: set[str] = set()
        overlap_cutoff = None
        page_html = html
        page_url = channel.url
        closed_overlap = False
        for page_number in range(1, self.maximum_archive_pages + 1):
            page_items = self._parse_listing_page(
                channel,
                page_html,
                context,
                page_url=page_url,
                position_offset=len(output),
                first_page=page_number == 1,
            )
            duplicate_ids = {
                item.source_article_id for item in page_items
            } & seen_ids
            if duplicate_ids:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate IDs across pages: "
                    f"{sorted(duplicate_ids)}"
                )
            seen_ids.update(item.source_article_id for item in page_items)
            output.extend(page_items)
            page_dates = [
                datetime.fromisoformat(item.published_at).date()
                for item in page_items
            ]
            if overlap_cutoff is None:
                overlap_cutoff = max(page_dates) - timedelta(days=2)
            if (
                len(page_items) < self.archive_page_size
                or min(page_dates) < overlap_cutoff
            ):
                closed_overlap = True
                break
            next_page = page_number + 1
            page_url = f"https://lieyunpro.com/archives/p{next_page}.html"
            page_html = context.fetch(page_url)
        if not closed_overlap:
            raise ListingInvariantError(
                f"{channel.source_id} did not close the two-day overlap "
                f"within {self.maximum_archive_pages} pages"
            )
        self.validate_listing(channel, output)
        if [article.listing_position for article in output] != list(
            range(1, len(output) + 1)
        ):
            raise ListingInvariantError(
                f"{channel.source_id} listing positions are not contiguous"
            )
        return output

    def _parse_listing_page(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
        *,
        page_url: str,
        position_offset: int,
        first_page: bool,
    ) -> list[SourceArticleIndex]:
        self._reject_interstitial(channel.source_id, html, listing=True)
        adaptive = AdaptiveSelector(
            html,
            url=page_url,
            storage_path=context.adaptive_db,
        )
        selection = adaptive.css(
            "div.article-container > div.article-bar",
            identifier=f"{channel.source_id}:listing-article",
            minimum_count=self.minimum_listing_count if first_page else 1,
            maximum_count=self.archive_page_size,
        )
        if not selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} listing selector failed closed"
            )

        output: list[SourceArticleIndex] = []
        discovered_at = context.now.replace(microsecond=0).isoformat()
        for page_position, item in enumerate(selection.elements, start=1):
            position = position_offset + page_position
            links = tuple(item.css("a.lyw-article-title"))
            if len(links) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} has "
                    f"{len(links)} title links"
                )
            link = links[0]
            canonical_url = self._canonical_url(
                urljoin(channel.url, str(link.attrib.get("href") or "").strip())
            )
            match = _ARTICLE_PATH.fullmatch(urlparse(canonical_url).path)
            if not match:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid article URL {canonical_url}"
                )
            article_id = match.group(1)
            title = self.clean_text(link.get_all_text(separator=" ", strip=True))
            summary = self._first_text(item, "p.article-digest")
            time_label = self._first_text(item, "span.timestamp")
            published_at = self._parse_listing_time(time_label, context)
            if not published_at:
                raise ListingInvariantError(
                    f"{channel.source_id} item {article_id} has no valid date"
                )
            author = self._first_text(item, "a.author")
            tags = tuple(
                dict.fromkeys(
                    self.clean_text(node.get_all_text(separator=" ", strip=True))
                    for node in item.css("span.article-tag a")
                    if self.clean_text(node.get_all_text(separator=" ", strip=True))
                )
            )
            structured = {
                "author": author,
                "tags": tags,
                "time_label": time_label,
                "company": self._company_from_title(title),
            }
            content_hash = self.stable_hash(
                "\n".join(
                    (
                        canonical_url,
                        title,
                        summary,
                        published_at,
                        repr(sorted(structured.items())),
                    )
                )
            )
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="archives",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=page_url,
                    listing_position=position,
                    content_hash=content_hash,
                    discovery_method=selection.method,
                    summary=summary,
                    structured_data=structured,
                )
            )
        return output
    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        self._reject_interstitial(channel.source_id, html, listing=False)
        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        selection = adaptive.css(
            "div.main-text#main-text-id",
            identifier=f"{channel.source_id}:article-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not selection.elements:
            raise DetailFetchError(
                f"{channel.source_id} body selector failed closed for "
                f"{index.source_article_id}"
            )
        body = self.clean_text(
            selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        if len(body) < 80:
            raise DetailFetchError(
                f"{channel.source_id} body too short for {index.source_article_id}"
            )
        detail_title = self._first_text(
            adaptive.selector, "h1.lyw-article-title-inner"
        )
        detail_title = re.sub(
            r"^(?:\d+\s*(?:分钟|小时|天)前)\s*", "", detail_title
        ).strip()
        if not self._titles_match(index.title, detail_title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )

        author = self._first_text(adaptive.selector, "a.author-name")
        if not author:
            author = str(index.structured_data.get("author") or "")
        tags = tuple(
            dict.fromkeys(
                self.clean_text(node.get_all_text(separator=" ", strip=True))
                for node in adaptive.selector.css("ul.article-tags a")
                if self.clean_text(node.get_all_text(separator=" ", strip=True))
            )
        )
        if not tags:
            tags = tuple(index.structured_data.get("tags") or ())
        structured = dict(index.structured_data)
        structured["detail_tags"] = tags
        digest = sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            tags=tags,
            structured_data=structured,
            extraction_method=selection.method,
            adaptive_similarity=selection.similarity_threshold,
            evidence_locators={
                "title": "detail:h1.lyw-article-title-inner",
                "body": "detail:div.main-text#main-text-id",
                "author": "detail:a.author-name / listing:a.author",
                "tags": "detail:ul.article-tags a / listing:span.article-tag a",
                "company": "listing:title-before-funding-verb",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        return extract_funding_events(
            channel,
            article,
            config=FundingRuleConfig(processor="rules:lieyun-v1"),
        )

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"lieyunpro.com", "www.lieyunpro.com"}:
            return url
        return f"https://lieyunpro.com{parsed.path.rstrip('/')}"

    @staticmethod
    def _parse_listing_time(value: str, context: AdapterContext) -> str:
        normalized = re.sub(r"\s+", "", value)
        site_now = context.now.astimezone(ZoneInfo("Asia/Shanghai"))
        full_date = re.fullmatch(r"(20\d{2})-(\d{2})-(\d{2})", normalized)
        if full_date:
            try:
                return datetime.fromisoformat(normalized).date().isoformat()
            except ValueError:
                return ""
        month_day = re.fullmatch(r"(\d{1,2})-(\d{1,2})", normalized)
        if month_day:
            try:
                candidate = site_now.date().replace(
                    month=int(month_day.group(1)),
                    day=int(month_day.group(2)),
                )
            except ValueError:
                return ""
            if candidate > site_now.date() + timedelta(days=1):
                candidate = candidate.replace(year=candidate.year - 1)
            return candidate.isoformat()
        relative = re.fullmatch(r"(\d+)(分钟|小时|天)前", normalized)
        if relative:
            value_int = int(relative.group(1))
            unit = relative.group(2)
            delta = {
                "分钟": timedelta(minutes=value_int),
                "小时": timedelta(hours=value_int),
                "天": timedelta(days=value_int),
            }[unit]
            return (site_now - delta).date().isoformat()
        return ""

    @staticmethod
    def _company_from_title(title: str) -> str:
        match = _COMPANY_BEFORE_EVENT.search(title)
        if not match:
            return ""
        company = _DESCRIPTOR_PREFIX.sub("", match.group("company")).strip(" ：:，,")
        return company if 2 <= len(company) <= 40 else ""

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"\s+", "", expected)
        right = re.sub(r"\s+", "", actual)
        return bool(left and right and (left == right or left in right or right in left))

    @staticmethod
    def _first_text(node: object, selector: str) -> str:
        elements = tuple(node.css(selector))
        if not elements:
            return ""
        return re.sub(
            r"\s+",
            " ",
            elements[0].get_all_text(separator=" ", strip=True),
        ).strip()

    @staticmethod
    def _reject_interstitial(source_id: str, html: bytes, *, listing: bool) -> None:
        text = html.decode("utf-8", errors="ignore")
        if not _ACCESS_INTERSTITIAL.search(text):
            return
        if listing:
            raise ListingInvariantError(
                f"{source_id} access interstitial detected; no bypass attempted"
            )
        raise DetailFetchError(
            f"{source_id} access interstitial detected; no bypass attempted"
        )


__all__ = ["LieyunAdapter"]
