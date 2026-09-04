"""Fail-closed adapter for CCID's public policy and expert commentary.

The China Market Intelligence Center is operated by CCIDnet under the China
Center for Information Industry Development.  Its public insights page exposes
finite, server-rendered first-page windows for ``专家观点`` and ``政策解读``.
The adapter indexes every visible card in those two editorial channels.

Scrapling is used only to relocate previously verified DOM nodes.  Acceptance
of a relocated node still requires the original channel id/category, a
whitelisted CCID URL, an exact CMS media timestamp, a consistent full title,
and an independently matching detail title and publication date.
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
from ..industry_rules import IndustryRuleConfig, extract_media_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_CHINA = ZoneInfo("Asia/Shanghai")
_CHANNELS = (
    ("3112", "专家观点", "zjgd2"),
    ("3114", "政策解读", "zcjd"),
)
_ARTICLE_PATH = re.compile(r"/(?P<section>zjgd2|zcjd)/(?P<id>\d{5,12})\.jhtml")
_MEDIA_TIMESTAMP = re.compile(
    r"/u/cms/qbzx/(?P<year>20\d{2})(?P<month>\d{2})/"
    r"(?P<day>\d{2})(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})"
    r"[A-Za-z0-9_-]*\.(?:jpe?g|png|webp)",
    re.I,
)
_DETAIL_META = re.compile(
    r"来源[：:]\s*(?P<source>[^\s]{1,40})\s+"
    r"(?P<date>20\d{2}-\d{2}-\d{2})"
)
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问过于频繁|Access Denied|403 Forbidden|"
    r"captcha-container|TTGCaptcha)",
    re.I,
)
_EVENT_TYPES = (
    "funding",
    "executive_change",
    "factory_or_capacity",
    "procurement_tender",
    "major_order",
    "partnership",
    "customer_validation",
    "new_site_or_entity",
    "regulatory_or_clinical",
    "policy_or_standard",
    "merger_acquisition",
    "ipo_or_listing",
    "enterprise_system",
    "technical_milestone",
)


class CcidReportCommentaryAdapter(AggregateAdapter):
    """Capture CCID expert opinion and policy interpretation articles."""

    adapter_id = "ccid_report_commentary"
    channels = (
        SourceChannel(
            source_id="ccid-report-commentary",
            name="赛迪中国市场情报中心—专家观点与政策解读",
            url="https://www.ccidreport.com/zjgd2.jhtml",
            source_grade="B",
            event_prior=_EVENT_TYPES,
            allowed_hosts=("www.ccidreport.com",),
            allowed_path_patterns=(
                r"/zjgd2/\d{5,12}\.jhtml",
                r"/zcjd/\d{5,12}\.jhtml",
            ),
        ),
    )
    minimum_listing_count = 8
    maximum_listing_count = 40

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
        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        methods: set[str] = set()

        for channel_id, category, expected_section in _CHANNELS:
            cards = adaptive.css(
                "div.tab-panel[data-channelid='"
                f"{channel_id}'] div.news-list > div.case-item",
                identifier=f"{channel.source_id}:listing-{channel_id}-cards",
                minimum_count=4,
                maximum_count=20,
            )
            if not cards.elements:
                raise ListingInvariantError(
                    f"{channel.source_id} {category} selector failed closed"
                )
            methods.add(cards.method)
            previous: datetime | None = None
            for category_position, card in enumerate(cards.elements, start=1):
                item, current = self._listing_card(
                    channel,
                    card,
                    category=category,
                    channel_id=channel_id,
                    expected_section=expected_section,
                    category_position=category_position,
                    discovered_at=discovered_at,
                    discovery_method=cards.method,
                )
                if previous is not None and current > previous:
                    raise ListingInvariantError(
                        f"{channel.source_id} {category} is not newest-first"
                    )
                if current.date() > self._source_today(context.now):
                    raise ListingInvariantError(
                        f"{channel.source_id} future-dated listing item "
                        f"{item.source_article_id}"
                    )
                previous = current
                if (
                    item.source_article_id in seen_ids
                    or item.canonical_url in seen_urls
                ):
                    raise ListingInvariantError(
                        f"{channel.source_id} duplicate listing article "
                        f"{item.canonical_url}"
                    )
                seen_ids.add(item.source_article_id)
                seen_urls.add(item.canonical_url)
                output.append(item)

        numbered = [
            SourceArticleIndex(
                **{
                    **item.to_dict(),
                    "listing_position": position,
                }
            )
            for position, item in enumerate(output, start=1)
        ]
        self.validate_listing(channel, numbered)
        self._record(
            context,
            "listing_window",
            {
                "categories": [category for _, category, _ in _CHANNELS],
                "category_counts": {
                    category: sum(item.channel == category for item in numbered)
                    for _, category, _ in _CHANNELS
                },
                "article_count": len(numbered),
                "adaptive_used": "adaptive" in methods,
                "window_kind": "complete_server_rendered_first_page",
            },
        )
        return numbered

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        self._reject_interstitial(channel.source_id, html, listing=False)
        expected_id = self._article_id(index.canonical_url)
        if not expected_id or expected_id != index.source_article_id:
            raise DetailFetchError(
                f"{channel.source_id} detail URL/id mismatch for "
                f"{index.source_article_id}"
            )
        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        title = adaptive.css(
            "div.content-area div.article-header h1.article-title",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        meta = adaptive.css(
            "div.content-area div.article-header span.article-source",
            identifier=f"{channel.source_id}:detail-meta",
            minimum_count=1,
            maximum_count=1,
        )
        body = adaptive.css(
            "div.content-area div.tab-content.active div.article-content",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not (title.elements and meta.elements and body.elements):
            raise DetailFetchError(
                f"{channel.source_id} detail selector failed closed for "
                f"{index.source_article_id}"
            )

        detail_title = self.clean_text(
            title.elements[0].get_all_text(separator=" ", strip=True)
        )
        if not self._titles_match(index.title, detail_title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        meta_text = self.clean_text(
            meta.elements[0].get_all_text(separator=" ", strip=True)
        )
        detail_source, detail_date = self._detail_metadata(meta_text)
        if (
            not detail_source
            or detail_date is None
            or detail_date.isoformat() != index.published_at[:10]
        ):
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )
        clean_body = self._clean_body(body.elements[0])
        if len(clean_body) < 180:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for "
                f"{index.source_article_id}"
            )
        if len(clean_body) > 100_000:
            raise DetailFetchError(
                f"{channel.source_id} detail body too long for "
                f"{index.source_article_id}"
            )

        method = (
            "adaptive"
            if "adaptive" in {title.method, meta.method, body.method}
            else "exact"
        )
        structured = {
            **index.structured_data,
            "detail_published_at": detail_date.isoformat(),
            "detail_source": detail_source,
            "document_type": "commentary",
            "document_type_target": ("commentary",),
        }
        tags = tuple(
            dict.fromkeys(
                (
                    index.channel,
                    *(str(tag) for tag in index.structured_data.get("tags", ())),
                )
            )
        )
        digest = sha256(
            f"{index.title}\n{clean_body}".encode("utf-8")
        ).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=clean_body,
            tags=tags,
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={
                "title": "detail:div.content-area div.article-header h1.article-title",
                "published_at": (
                    "detail:div.content-area div.article-header "
                    "span.article-source"
                ),
                "body": (
                    "detail:div.content-area div.tab-content.active "
                    "div.article-content"
                ),
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        return extract_media_events(
            channel,
            article,
            config=IndustryRuleConfig(
                processor="rules:ccid-report-commentary-v1"
            ),
            funding_processor="rules:ccid-report-commentary-funding-v1",
        )

    def _listing_card(
        self,
        channel: SourceChannel,
        card: object,
        *,
        category: str,
        channel_id: str,
        expected_section: str,
        category_position: int,
        discovered_at: str,
        discovery_method: str,
    ) -> tuple[SourceArticleIndex, datetime]:
        if str(card.attrib.get("data-cat") or "") != category:
            raise ListingInvariantError(
                f"{channel.source_id} {category} card category mismatch"
            )
        links = tuple(card.css("div.case-info > h3 > a"))
        images = tuple(card.css("div.case-thumb > img"))
        descriptions = tuple(card.css("div.case-info > p.desc"))
        tags = tuple(card.css("div.case-info div.case-meta > span.tag"))
        if (
            len(links) != 1
            or len(images) != 1
            or len(descriptions) != 1
            or not 1 <= len(tags) <= 8
        ):
            raise ListingInvariantError(
                f"{channel.source_id} {category} card {category_position} "
                "has invalid field cardinality"
            )

        url = self._canonical_url(
            urljoin(channel.url, str(links[0].attrib.get("href") or ""))
        )
        article_id = self._article_id(url)
        path_match = _ARTICLE_PATH.fullmatch(urlparse(url).path)
        if (
            not article_id
            or path_match is None
            or path_match.group("section") != expected_section
        ):
            raise ListingInvariantError(
                f"{channel.source_id} rejected {category} URL {url}"
            )
        title = self.clean_text(str(images[0].attrib.get("alt") or ""))
        display_title = self.clean_text(
            links[0].get_all_text(separator=" ", strip=True)
        )
        if not self._listing_titles_match(display_title, title):
            raise ListingInvariantError(
                f"{channel.source_id} listing title mismatch for {article_id}"
            )
        summary = self.clean_text(
            descriptions[0].get_all_text(separator=" ", strip=True)
        )
        if not 20 <= len(summary) <= 600:
            raise ListingInvariantError(
                f"{channel.source_id} invalid summary for {article_id}"
            )
        labels = tuple(
            self.clean_text(tag.get_all_text(separator=" ", strip=True))
            for tag in tags
        )
        if any(not label or len(label) > 40 for label in labels):
            raise ListingInvariantError(
                f"{channel.source_id} invalid tags for {article_id}"
            )

        media_timestamp = self._media_timestamp(
            str(images[0].attrib.get("src") or "")
        )
        if media_timestamp is None:
            raise ListingInvariantError(
                f"{channel.source_id} invalid CMS timestamp for {article_id}"
            )
        published_at = datetime.combine(
            media_timestamp.date(),
            datetime.min.time(),
            tzinfo=_CHINA,
        ).isoformat()
        structured = {
            "editorial_category": category,
            "editorial_channel_id": channel_id,
            "category_position": category_position,
            "tags": labels,
            "listing_media_uploaded_at": media_timestamp.isoformat(),
            "document_type": "commentary",
            "document_type_target": ("commentary",),
        }
        stable_structured = self.stable_index_metadata(structured)
        content_hash = self.stable_hash(
            "\n".join(
                (
                    url,
                    title,
                    published_at,
                    summary,
                    repr(sorted(stable_structured.items())),
                )
            )
        )
        return (
            SourceArticleIndex(
                source_id=channel.source_id,
                source_article_id=article_id,
                channel=category,
                canonical_url=url,
                title=title,
                published_at=published_at,
                discovered_at=discovered_at,
                cursor_value=f"{published_at}|{article_id}",
                listing_page=channel.url,
                listing_position=0,
                content_hash=content_hash,
                discovery_method=discovery_method,
                summary=summary,
                structured_data=structured,
            ),
            media_timestamp,
        )

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "www.ccidreport.com"
        ):
            return value
        match = _ARTICLE_PATH.fullmatch(parsed.path)
        if match is None:
            return value
        return (
            "https://www.ccidreport.com/"
            f"{match.group('section')}/{match.group('id')}.jhtml"
        )

    @staticmethod
    def _article_id(url: str) -> str:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "www.ccidreport.com"
        ):
            return ""
        match = _ARTICLE_PATH.fullmatch(parsed.path)
        return match.group("id") if match else ""

    @staticmethod
    def _media_timestamp(value: str) -> datetime | None:
        match = _MEDIA_TIMESTAMP.fullmatch(value)
        if match is None:
            return None
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                int(match.group("hour")),
                int(match.group("minute")),
                int(match.group("second")),
                tzinfo=_CHINA,
            )
        except ValueError:
            return None

    @staticmethod
    def _detail_metadata(value: str):
        match = _DETAIL_META.fullmatch(value)
        if match is None:
            return "", None
        try:
            return (
                match.group("source").strip(),
                datetime.fromisoformat(match.group("date")).date(),
            )
        except ValueError:
            return "", None

    @staticmethod
    def _listing_titles_match(display: str, full: str) -> bool:
        left = re.sub(r"[\s\u200b|丨｜]", "", display)
        right = re.sub(r"[\s\u200b|丨｜]", "", full)
        if not left or not right:
            return False
        if left == right:
            return True
        truncated = left.rstrip(".…")
        return (
            len(truncated) >= 12
            and left != truncated
            and right.startswith(truncated)
        )

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s\u200b|丨｜]", "", expected)
        right = re.sub(r"[\s\u200b|丨｜]", "", actual)
        return bool(left and left == right)

    @classmethod
    def _clean_body(cls, element: object) -> str:
        return cls.clean_text(
            element.get_all_text(separator=" ", strip=True)
        )

    @staticmethod
    def _source_today(now: datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(_CHINA).date()

    @staticmethod
    def _record(
        context: AdapterContext,
        action: str,
        payload: dict[str, object],
    ) -> None:
        if context.record_decision:
            context.record_decision(action, payload)

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
        error_type = ListingInvariantError if listing else DetailFetchError
        raise error_type(
            f"{source_id} access interstitial detected; no bypass attempted"
        )


__all__ = ["CcidReportCommentaryAdapter"]
