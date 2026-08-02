"""Fail-closed adapter for NBD's public weekly VC/PE roundup.

The source is a non-corporate Mainland Chinese financial newspaper.  Its
weekly roundup is useful here because each article contains several explicitly
headed company/fund items.  Discovery uses the same public JSON search endpoint
as NBD's own search page; detail extraction remains tied to the canonical SSR
article page.

Scrapling is allowed to relocate the two verified DOM containers only.  URL,
title, date, result-count, ordering, article-body, and item-boundary invariants
are rechecked deterministically after any relocation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlparse
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
_SEARCH_ENDPOINT = "https://www.nbd.com.cn/news-search/queryByMatch"
_SEARCH_KEYWORD = "VC PE 周报"
_SEARCH_SIZE = 16
_LOOKBACK = timedelta(days=35)
_ARTICLE_PATH = re.compile(
    r"/articles/(?P<year>20\d{2})-(?P<month>\d{2})-(?P<day>\d{2})/"
    r"(?P<id>\d+)\.html"
)
_ROUNDUP_TITLE = re.compile(r"^VC/PE周报(?:[丨｜|：:])")
_ACCESS_CONTROL = re.compile(
    r"(?:Access Denied|Forbidden|Just a moment|challenge-platform|"
    r"安全验证|访问验证|访问过于频繁|请求过于频繁|验证码)",
    re.I,
)
_BYLINE = re.compile(r"^每经(?:记者|编辑)[｜|].{1,80}每经编辑[｜|]")
_NON_ITEM_HEADING = re.compile(r"^(?:点评|封面图片|图片来源|免责声明|特别提醒)[:：]?")
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


class _MarkupText(HTMLParser):
    """Extract plain text from the search API's small highlighted snippets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


class NbdVcpeWeeklyAdapter(AggregateAdapter):
    """Capture NBD VC/PE weekly roundups and their explicit item boundaries."""

    adapter_id = "nbd-vcpe-weekly"
    channels = (
        SourceChannel(
            source_id="nbd-vcpe-weekly",
            name="每日经济新闻—VC/PE周报",
            url=(
                "https://www.nbd.com.cn/search/article_search/"
                "?q=VC%20PE%20%E5%91%A8%E6%8A%A5"
            ),
            source_grade="B",
            event_prior=_EVENT_TYPES,
            allowed_hosts=("www.nbd.com.cn",),
            allowed_path_patterns=(
                r"/articles/20\d{2}-\d{2}-\d{2}/\d+\.html",
            ),
        ),
    )
    minimum_listing_count = 4
    maximum_listing_count = _SEARCH_SIZE

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        self._reject_access_control(channel.source_id, html, listing=True)
        page = AdaptiveSelector(
            html,
            url=channel.url,
            storage_path=context.adaptive_db,
        )
        anchor = page.css(
            "div.search-header div.search-box1",
            identifier=f"{channel.source_id}:search-form",
            minimum_count=1,
            maximum_count=1,
        )
        decoded_page = html.decode("utf-8", errors="replace")
        if not anchor.elements or "/news-search/queryByMatch" not in decoded_page:
            raise ListingInvariantError(
                f"{channel.source_id} public search contract failed closed"
            )
        if context.post_json is None:
            raise ListingInvariantError(
                f"{channel.source_id} requires audited JSON POST transport"
            )

        payload = context.post_json(
            _SEARCH_ENDPOINT,
            {
                "keyword": _SEARCH_KEYWORD,
                "from": 0,
                "size": _SEARCH_SIZE,
                "includeAd": True,
                "platform": [0, 1],
            },
        )
        self._reject_access_control(channel.source_id, payload, listing=True)
        results, total_hits = self._decode_search(channel, payload)
        expected_count = min(total_hits, _SEARCH_SIZE)
        if len(results) != expected_count:
            raise ListingInvariantError(
                f"{channel.source_id} search returned {len(results)} of "
                f"expected {expected_count} visible results"
            )

        discovered_at = context.now.replace(microsecond=0).isoformat()
        source_today = context.now.astimezone(_CHINA).date()
        cutoff = source_today - _LOOKBACK
        parsed: list[SourceArticleIndex] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        previous: date | None = None
        oldest_seen: date | None = None
        for api_position, raw in enumerate(results, start=1):
            if not isinstance(raw, dict):
                raise ListingInvariantError(
                    f"{channel.source_id} result {api_position} is not an object"
                )
            title = self._search_title(str(raw.get("title") or ""))
            if not _ROUNDUP_TITLE.search(title):
                continue
            canonical_url = self._canonical_url(str(raw.get("url") or ""))
            article_id, path_date = self._article_identity(canonical_url)
            published = self._parse_api_date(str(raw.get("publishTime") or ""))
            if not article_id or published is None or path_date != published:
                raise ListingInvariantError(
                    f"{channel.source_id} result {api_position} URL/date mismatch"
                )
            if published > source_today:
                raise ListingInvariantError(
                    f"{channel.source_id} result {article_id} is future dated"
                )
            if previous is not None and published > previous:
                raise ListingInvariantError(
                    f"{channel.source_id} search results are not newest-first"
                )
            previous = published
            oldest_seen = published
            if article_id in seen_ids or canonical_url in seen_urls:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate search result {article_id}"
                )
            seen_ids.add(article_id)
            seen_urls.add(canonical_url)

            if not context.capture_full_visible_window and published < cutoff:
                continue
            author = self.clean_text(str(raw.get("author") or ""))
            editor = self.clean_text(str(raw.get("editor") or ""))
            if not author:
                raise ListingInvariantError(
                    f"{channel.source_id} result {article_id} has no author"
                )
            summary = self._strip_markup(str(raw.get("digest") or ""))
            published_at = datetime.combine(
                published,
                datetime.min.time(),
                tzinfo=_CHINA,
            ).isoformat()
            structured = {
                "author": author,
                "editor": editor,
                "api_position": api_position,
                "search_keyword": _SEARCH_KEYWORD,
                "document_type": "multi_company_bulletin",
            }
            parsed.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="vcpe-weekly",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=len(parsed) + 1,
                    content_hash=self.stable_hash(
                        "\n".join(
                            (
                                canonical_url,
                                title,
                                summary,
                                published_at,
                                author,
                                editor,
                            )
                        )
                    ),
                    discovery_method=(
                        "api+adaptive"
                        if anchor.method == "adaptive"
                        else "api-exact"
                    ),
                    summary=summary,
                    structured_data=structured,
                )
            )

        if not context.capture_full_visible_window:
            if oldest_seen is None or (oldest_seen >= cutoff and total_hits > len(results)):
                raise ListingInvariantError(
                    f"{channel.source_id} search page does not close lookback window"
                )
        self.validate_listing(channel, parsed)
        self._record(
            context,
            "listing_window",
            {
                "search_total_hits": total_hits,
                "visible_result_count": len(results),
                "accepted_roundup_count": len(parsed),
                "lookback_days": _LOOKBACK.days,
                "full_visible_window": context.capture_full_visible_window,
                "adaptive_used": anchor.method == "adaptive",
            },
        )
        return parsed

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        self._reject_access_control(channel.source_id, html, listing=False)
        article_id, path_date = self._article_identity(index.canonical_url)
        if article_id != index.source_article_id:
            raise DetailFetchError(
                f"{channel.source_id} detail URL/id mismatch for {index.source_article_id}"
            )
        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        title = adaptive.css(
            "div.g-article-top > h1",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        date_node = adaptive.css(
            "div.g-article-top p.u-time span.time",
            identifier=f"{channel.source_id}:detail-date",
            minimum_count=1,
            maximum_count=1,
        )
        body = adaptive.css(
            "div.g-articl-text",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not title.elements or not date_node.elements or not body.elements:
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
        published = self._parse_detail_time(
            date_node.elements[0].get_all_text(separator=" ", strip=True)
        )
        if published is None or published.date() != path_date:
            raise DetailFetchError(
                f"{channel.source_id} detail URL/date mismatch for "
                f"{index.source_article_id}"
            )
        if published.date().isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )

        clean_body, boundaries, headings, block_count = self._body_and_boundaries(
            body.elements[0]
        )
        if not 800 <= len(clean_body) <= 100_000 or block_count < 10:
            raise DetailFetchError(
                f"{channel.source_id} detail body failed length/structure "
                f"invariants for {index.source_article_id}"
            )
        if len(boundaries) < 4:
            raise DetailFetchError(
                f"{channel.source_id} has only {len(boundaries)} explicit "
                f"roundup item boundaries for {index.source_article_id}"
            )
        if any(boundary["char_end"] - boundary["char_start"] < 40 for boundary in boundaries):
            raise DetailFetchError(
                f"{channel.source_id} has undersized roundup item for "
                f"{index.source_article_id}"
            )

        methods = {title.method, date_node.method, body.method}
        extraction_method = "adaptive" if "adaptive" in methods else "exact"
        author = self.clean_text(str(index.structured_data.get("author") or ""))
        structured = {
            **index.structured_data,
            "detail_published_at": published.isoformat(),
            "document_type": "multi_company_bulletin",
            "item_boundaries": boundaries,
            "item_headings": headings,
            "article_block_count": block_count,
        }
        digest = sha256(f"{detail_title}\n{clean_body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=clean_body,
            author=author,
            tags=("VC/PE周报", "硬科技投融资"),
            structured_data=structured,
            extraction_method=extraction_method,
            adaptive_similarity=72 if extraction_method == "adaptive" else None,
            evidence_locators={
                "title": "detail:div.g-article-top > h1",
                "published_at": "detail:div.g-article-top p.u-time span.time",
                "body": "detail:div.g-articl-text direct p/h2/h3/h4 blocks",
                "item_boundaries": "detail:full-text heading blocks",
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
            config=IndustryRuleConfig(processor="rules:nbd-vcpe-weekly-v1"),
            funding_processor="rules:nbd-vcpe-weekly-funding-v1",
        )

    @classmethod
    def _decode_search(
        cls,
        channel: SourceChannel,
        payload: bytes,
    ) -> tuple[list[object], int]:
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ListingInvariantError(
                f"{channel.source_id} search response is not UTF-8 JSON"
            ) from exc
        if not isinstance(document, dict) or document.get("code") != 200:
            raise ListingInvariantError(
                f"{channel.source_id} search response status is not 200"
            )
        data = document.get("data")
        if not isinstance(data, dict):
            raise ListingInvariantError(
                f"{channel.source_id} search response has no data object"
            )
        results = data.get("searchResults")
        total_hits = data.get("totalHits")
        if (
            not isinstance(results, list)
            or not isinstance(total_hits, int)
            or total_hits < len(results)
        ):
            raise ListingInvariantError(
                f"{channel.source_id} search result cardinality is invalid"
            )
        return results, total_hits

    @classmethod
    def _body_and_boundaries(
        cls,
        container,
    ) -> tuple[str, list[dict[str, int | str]], list[str], int]:
        nodes = tuple(
            node
            for node in container.css(":scope > *")
            if node.tag in {"p", "h2", "h3", "h4"}
        )
        blocks: list[tuple[str, bool]] = []
        for node in nodes:
            text = cls.clean_text(node.get_all_text(separator=" ", strip=True))
            if not text or _BYLINE.search(text):
                continue
            heading = node.tag in {"h2", "h3", "h4"}
            if node.tag == "p":
                strong_text = "".join(
                    cls.clean_text(item.get_all_text(separator=" ", strip=True))
                    for item in node.css("strong")
                )
                heading = bool(
                    strong_text
                    and cls._heading_key(strong_text) == cls._heading_key(text)
                )
            heading = bool(
                heading
                and 4 <= len(text) <= 100
                and not _NON_ITEM_HEADING.search(text)
            )
            blocks.append((text, heading))

        body = "\n".join(text for text, _ in blocks)
        starts: list[tuple[int, str]] = []
        cursor = 0
        for text, heading in blocks:
            if heading:
                starts.append((cursor, text))
            cursor += len(text) + 1
        boundaries: list[dict[str, int | str]] = []
        for position, (start, heading) in enumerate(starts):
            end = starts[position + 1][0] if position + 1 < len(starts) else len(body)
            boundaries.append(
                {
                    "char_start": start,
                    "char_end": end,
                    "heading": heading,
                    "source": "explicit_detail_heading",
                }
            )
        return body, boundaries, [heading for _, heading in starts], len(blocks)

    @staticmethod
    def _strip_markup(value: str) -> str:
        parser = _MarkupText()
        try:
            parser.feed(value)
            parser.close()
        except (ValueError, AssertionError) as exc:
            raise ListingInvariantError("nbd search result contains malformed markup") from exc
        return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()

    @classmethod
    def _search_title(cls, value: str) -> str:
        title = cls._strip_markup(value)
        title = re.sub(r"\s*([/丨｜|：:])\s*", r"\1", title)
        return re.sub(r"^VC/PE\s+周报", "VC/PE周报", title)

    @staticmethod
    def _heading_key(value: str) -> str:
        return re.sub(r"[\s\u200b]+", "", value)

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s\u200b丨｜|]+", "", expected)
        right = re.sub(r"[\s\u200b丨｜|]+", "", actual)
        return bool(left and left == right)

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if host != "www.nbd.com.cn":
            return value
        return f"https://www.nbd.com.cn{parsed.path}"

    @staticmethod
    def _article_identity(value: str) -> tuple[str, date | None]:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname != "www.nbd.com.cn":
            return "", None
        match = _ARTICLE_PATH.fullmatch(parsed.path)
        if not match:
            return "", None
        try:
            path_date = date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            return "", None
        return match.group("id"), path_date

    @staticmethod
    def _parse_api_date(value: str) -> date | None:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def _parse_detail_time(value: str) -> datetime | None:
        cleaned = re.sub(r"\s+", " ", value).strip()
        try:
            return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=_CHINA
            )
        except ValueError:
            return None

    @staticmethod
    def _reject_access_control(
        source_id: str,
        payload: bytes,
        *,
        listing: bool,
    ) -> None:
        text = payload.decode("utf-8", errors="replace")
        if not _ACCESS_CONTROL.search(text):
            return
        error = ListingInvariantError if listing else DetailFetchError
        kind = "listing" if listing else "detail"
        raise error(f"{source_id} {kind} access control detected; no bypass")

    @staticmethod
    def _record(
        context: AdapterContext,
        key: str,
        payload: dict[str, object],
    ) -> None:
        if context.record_decision is not None:
            context.record_decision(key, payload)


__all__ = ["NbdVcpeWeeklyAdapter"]
