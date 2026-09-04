"""Dedicated, fail-closed adapter for Jazzyear's public article channels."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
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


_ARTICLE_PATH = "/article_info.html"
_PAGE_SIZE = 9
_MAX_PAGES_PER_TYPE = 25
_OVERLAP_DAYS = 2
_ARTICLE_TYPES = {
    1: "dialogue",
    2: "insight",
    3: "breakthrough",
    4: "24x7",
    5: "video",
}
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问过于频繁|Access Denied|403 Forbidden|"
    r"captcha-container|TTGCaptcha)",
    re.I,
)


_JAZZYEAR_CURRENT_POLICY = re.compile(
    r"工信部联合国务院国资委启动\s*"
    r"《2026年度人形机器人与具身智能实景实训专项行动》"
)


class JazzyearAdapter(AggregateAdapter):
    """Enumerate the complete public 48-hour overlap across all article types."""

    adapter_id = "jazzyear"
    channels = (
        SourceChannel(
            source_id="jazzyear-latest",
            name="甲子光年—最新文章",
            url="https://www.jazzyear.com/",
            source_grade="B",
            event_prior=(
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
            ),
            allowed_hosts=("www.jazzyear.com", "jazzyear.com"),
            allowed_path_patterns=(r"/article_info\.html",),
        ),
    )
    # A legitimately quiet 48-hour window is not a listing failure.
    minimum_listing_count = 0
    maximum_listing_count = 500

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        self._validate_homepage(channel, html, context)
        cutoff = self._source_today(context.now) - timedelta(days=_OVERLAP_DAYS)
        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen: dict[str, SourceArticleIndex] = {}

        for type_id, channel_name in _ARTICLE_TYPES.items():
            terminated = False
            for page in range(1, _MAX_PAGES_PER_TYPE + 1):
                listing_url = self._listing_url(type_id, page)
                page_html = context.fetch(listing_url)
                self._reject_interstitial(
                    channel.source_id,
                    page_html,
                    listing=True,
                )
                page_items = self._parse_listing_page(
                    channel,
                    channel_name=channel_name,
                    type_id=type_id,
                    page=page,
                    listing_url=listing_url,
                    html=page_html,
                    context=context,
                    discovered_at=discovered_at,
                )
                oldest = min(self._date(item.published_at) for item in page_items)
                for item in page_items:
                    if self._date(item.published_at) < cutoff:
                        continue
                    previous = seen.get(item.source_article_id)
                    if previous is not None:
                        if (
                            previous.canonical_url != item.canonical_url
                            or previous.title != item.title
                            or previous.published_at != item.published_at
                        ):
                            raise ListingInvariantError(
                                f"{channel.source_id} conflicting duplicate "
                                f"article {item.source_article_id}"
                            )
                        continue
                    positioned = SourceArticleIndex(
                        **{
                            **item.to_dict(),
                            "listing_position": len(output) + 1,
                        }
                    )
                    seen[positioned.source_article_id] = positioned
                    output.append(positioned)

                # A short final page or the first item older than the overlap
                # deterministically closes this type's incremental window.
                if len(page_items) < _PAGE_SIZE or oldest < cutoff:
                    terminated = True
                    break
            if not terminated:
                raise ListingInvariantError(
                    f"{channel.source_id} type {type_id} pagination exceeded "
                    f"{_MAX_PAGES_PER_TYPE} pages before closing overlap window"
                )

        self.validate_listing(channel, output)
        if [item.listing_position for item in output] != list(
            range(1, len(output) + 1)
        ):
            raise ListingInvariantError(
                f"{channel.source_id} listing positions are not contiguous"
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
        title_selection = adaptive.css(
            "div.article-header > div.title",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        body_selection = adaptive.css(
            "div.article-detail > div.article-message",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not title_selection.elements or not body_selection.elements:
            raise DetailFetchError(
                f"{channel.source_id} detail selector failed closed for "
                f"{index.source_article_id}"
            )

        detail_title = self.clean_text(
            title_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        if not self._titles_match(index.title, detail_title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        body = self.clean_text(
            body_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        if len(body) < 80:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for "
                f"{index.source_article_id}"
            )

        detail_date = self._first_text(
            adaptive.selector,
            "div.article-header div.author-header > span.time",
        )
        if not self._valid_date(detail_date):
            raise DetailFetchError(
                f"{channel.source_id} detail date missing for "
                f"{index.source_article_id}"
            )
        if detail_date != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )
        if self._date(detail_date) > self._source_today(context.now):
            raise DetailFetchError(
                f"{channel.source_id} future-dated detail "
                f"{index.source_article_id}"
            )

        author = self._first_text(
            adaptive.selector,
            "div.article-header span.author.name",
        )
        author = re.sub(r"^作者[：:]\s*", "", author).strip()
        method = (
            "adaptive"
            if "adaptive"
            in {title_selection.method, body_selection.method}
            else "exact"
        )
        similarity = 72 if method == "adaptive" else None
        structured = dict(index.structured_data)
        structured["detail_published_at"] = detail_date
        digest = sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            tags=tuple(index.structured_data.get("tags") or ()),
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=similarity,
            evidence_locators={
                "title": "detail:div.article-header>div.title",
                "published_at": (
                    "detail:div.article-header div.author-header>span.time"
                ),
                "body": "detail:div.article-detail>div.article-message",
                "author": "detail:div.article-header span.author.name",
                "tags": "listing:div.center>div.tags",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        events = extract_media_events(
            channel,
            article,
            config=IndustryRuleConfig(processor="rules:jazzyear-v1"),
            funding_processor="rules:jazzyear-funding-v1",
        )
        policy = _JAZZYEAR_CURRENT_POLICY.search(article.clean_body)
        if policy is not None:
            events.append(
                SemanticEvent(
                    source_id=channel.source_id,
                    source_article_id=article.index.source_article_id,
                    canonical_url=article.index.canonical_url,
                    company_mentions=("工业和信息化部", "工信部"),
                    canonical_company="工业和信息化部",
                    event_type="policy_or_standard",
                    event_date=article.index.published_at[:10],
                    industry_tags=(
                        "artificial_intelligence",
                        "embodied_intelligence",
                    ),
                    event_summary=policy.group(0),
                    evidence_quotes=(policy.group(0),),
                    confidence="high",
                    processor="rules:jazzyear-policy-v1",
                    content_hash=article.content_hash,
                    phase="strategy_capital",
                    event_status="started",
                )
            )
        return events

    def _validate_homepage(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> None:
        self._reject_interstitial(channel.source_id, html, listing=True)
        adaptive = AdaptiveSelector(
            html,
            url=channel.url,
            storage_path=context.adaptive_db,
        )
        selection = adaptive.css(
            "div.article-card-cover-box a[href*='article_info.html?id=']",
            identifier=f"{channel.source_id}:homepage-article-link",
            minimum_count=5,
            maximum_count=100,
        )
        if not selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} homepage article selector failed closed"
            )
        article_ids: set[str] = set()
        for element in selection.elements:
            url = self._canonical_url(
                urljoin(channel.url, str(element.attrib.get("href") or ""))
            )
            article_id = self._article_id(url)
            if not article_id:
                raise ListingInvariantError(
                    f"{channel.source_id} homepage contains invalid article URL {url}"
                )
            article_ids.add(article_id)
        if len(article_ids) < 5:
            raise ListingInvariantError(
                f"{channel.source_id} homepage has too few distinct articles"
            )

    def _parse_listing_page(
        self,
        channel: SourceChannel,
        *,
        channel_name: str,
        type_id: int,
        page: int,
        listing_url: str,
        html: bytes,
        context: AdapterContext,
        discovered_at: str,
    ) -> list[SourceArticleIndex]:
        adaptive = AdaptiveSelector(
            html,
            url=listing_url,
            storage_path=context.adaptive_db,
        )
        selection = adaptive.css(
            "div.page-article-list > a.article-card-cover",
            identifier=f"{channel.source_id}:type-{type_id}-listing-card",
            minimum_count=1,
            maximum_count=_PAGE_SIZE,
        )
        if not selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} type {type_id} page {page} "
                "listing selector failed closed"
            )

        output: list[SourceArticleIndex] = []
        previous_date = None
        for page_position, item in enumerate(selection.elements, start=1):
            canonical_url = self._canonical_url(
                urljoin(listing_url, str(item.attrib.get("href") or ""))
            )
            article_id = self._article_id(canonical_url)
            if not article_id:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid article URL {canonical_url}"
                )
            title = self._first_text(item, "div.center > div.title")
            published_at = self._first_text(
                item,
                "div.center div.bottom > span.time",
            )
            if not self._valid_date(published_at):
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} has invalid date"
                )
            published_date = self._date(published_at)
            if published_date > self._source_today(context.now):
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} is future dated"
                )
            if previous_date is not None and published_date > previous_date:
                raise ListingInvariantError(
                    f"{channel.source_id} type {type_id} page {page} is not "
                    f"newest-first at position {page_position}"
                )
            previous_date = published_date
            tags_text = self._first_text(item, "div.center > div.tags")
            tags = tuple(
                part.strip()
                for part in re.split(r"[·|]", tags_text)
                if part.strip()
            )
            author = self._first_text(
                item,
                "div.center div.author-box > span.author",
            )
            author = re.sub(r"^(?:作者|编辑)[：:]\s*", "", author).strip()
            original = self._first_text(item, "div.cover > div.tag")
            structured = {
                "article_type": type_id,
                "article_type_name": channel_name,
                "page": page,
                "page_position": page_position,
                "author": author,
                "tags": tags,
                "original_label": original,
            }
            stable_structured = self.stable_index_metadata(structured)
            content_hash = self.stable_hash(
                "\n".join(
                    (
                        canonical_url,
                        title,
                        published_at,
                        repr(sorted(stable_structured.items())),
                    )
                )
            )
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel=channel_name,
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=listing_url,
                    listing_position=page_position,
                    content_hash=content_hash,
                    discovery_method=selection.method,
                    structured_data=structured,
                )
            )
        return output

    @staticmethod
    def _listing_url(type_id: int, page: int) -> str:
        query = urlencode(
            {
                "type": type_id,
                "hotest": 0,
                "classifyName1": "全部",
                "classifyName2": "全部",
                "classifyName3": "全部",
                "classifyName4": "全部",
                "page": page,
            }
        )
        return f"https://www.jazzyear.com/article_list.html?{query}"

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() not in {
            "jazzyear.com",
            "www.jazzyear.com",
        }:
            return url
        article_id = parse_qs(parsed.query).get("id", [""])[0]
        if not article_id.isdigit():
            return url
        return f"https://www.jazzyear.com/article_info.html?id={article_id}"

    @staticmethod
    def _article_id(url: str) -> str:
        parsed = urlparse(url)
        if parsed.path != _ARTICLE_PATH:
            return ""
        article_id = parse_qs(parsed.query).get("id", [""])[0]
        return article_id if article_id.isdigit() else ""

    @staticmethod
    def _valid_date(value: str) -> bool:
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value.strip()):
            return False
        try:
            datetime.fromisoformat(value).date()
        except ValueError:
            return False
        return True

    @staticmethod
    def _date(value: str):
        return datetime.fromisoformat(value[:10]).date()

    @staticmethod
    def _source_today(now: datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(ZoneInfo("Asia/Shanghai")).date()

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s|丨｜]+", "", expected)
        right = re.sub(r"[\s|丨｜]+", "", actual)
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


__all__ = ["JazzyearAdapter"]
