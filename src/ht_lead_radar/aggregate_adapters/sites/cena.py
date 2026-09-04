"""Fail-closed adapter for China Electronics News' public e-paper.

The current-issue landing page exposes every issue section.  This adapter
deliberately follows only analysis-oriented sections and then enumerates every
headline shown on those section pages.  Scrapling is used solely to relocate a
previously verified DOM selector; URL, issue, section, title, and date
invariants remain deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
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
_ISSUE_DATE = re.compile(r"(20\d{2})年(\d{2})月(\d{2})日")
_ARTICLE_PATH = re.compile(
    r"/pc/content/(?P<year>20\d{2})(?P<month>\d{2})/(?P<day>\d{2})/"
    r"content_(?P<id>\d+)\.html"
)
_ANALYSIS_SECTION = re.compile(
    r"政策解读|专题|产业观察|评论|深度|集成电路|半导体|人工智能|机器人|"
    r"信息通信|电子信息|新型显示|软件|数字经济|智能制造|先进制造"
)
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问过于频繁|Access Denied|403 Forbidden|"
    r"captcha-container|TTGCaptcha)",
    re.I,
)
_BODY_NOISE = re.compile(
    r"^(?:责任编辑|版面导航|标题导航|上一篇|下一篇|放大\+|缩小-|默认o)$"
)
_BYLINE_ONLY = re.compile(r"^(?:编辑|责任编辑|记者)[：:]\s*[^，。；]{1,30}$")
_NON_ARTICLE_TITLE = re.compile(r"^公益广告$")
_DECORATIVE_ANCHOR_TITLE = re.compile(r"^(?:导读|目录|更多|返回|上一篇|下一篇)$")
_DECORATIVE_CLASS = re.compile(r"(?:^|\s)(?:decorative|navigation|nav|more|placeholder)(?:\s|$)", re.I)
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


class CenaAdapter(AggregateAdapter):
    """Enumerate all articles in the latest issue's analysis sections."""

    adapter_id = "cena"
    channels = (
        SourceChannel(
            source_id="cena-industry-analysis",
            name="中国电子报—产业分析版面",
            url="https://epaper.cena.com.cn/pc/layout/index.html",
            source_grade="B",
            event_prior=_EVENT_TYPES,
            allowed_hosts=("epaper.cena.com.cn",),
            allowed_path_patterns=(
                r"/pc/content/20\d{4}/\d{2}/content_\d+\.html",
            ),
        ),
    )
    minimum_listing_count = 1
    maximum_listing_count = 200

    def should_fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
    ) -> bool:
        """Skip linked byline records while preserving their audited index."""

        del channel
        return not (
            _BYLINE_ONLY.fullmatch(index.title)
            or _NON_ARTICLE_TITLE.fullmatch(index.title)
        )

    def validate_listing(
        self,
        channel: SourceChannel,
        articles: list[SourceArticleIndex],
    ) -> None:
        """CENA permits concise editorial titles while retaining URL invariants."""

        if not self.minimum_listing_count <= len(articles) <= self.maximum_listing_count:
            raise ListingInvariantError(
                f"{channel.source_id} listing count {len(articles)} outside "
                f"{self.minimum_listing_count}..{self.maximum_listing_count}"
            )
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        for article in articles:
            if not article.source_article_id or article.source_article_id in seen_ids:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate/empty article id"
                )
            if article.canonical_url in seen_urls:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate canonical URL"
                )
            if not 1 <= len(article.title.strip()) <= 300:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid title: {article.title!r}"
                )
            parsed = urlparse(article.canonical_url)
            if parsed.scheme != "https" or parsed.hostname not in channel.allowed_hosts:
                raise ListingInvariantError(
                    f"{channel.source_id} rejected URL: {article.canonical_url}"
                )
            if channel.allowed_path_patterns and not any(
                re.fullmatch(pattern, parsed.path)
                for pattern in channel.allowed_path_patterns
            ):
                raise ListingInvariantError(
                    f"{channel.source_id} rejected path: {parsed.path}"
                )
            seen_ids.add(article.source_article_id)
            seen_urls.add(article.canonical_url)

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        self._reject_interstitial(channel.source_id, html, listing=True)
        issue_date, sections = self._issue_sections(channel, html, context)
        source_today = self._source_today(context.now)
        if issue_date > source_today:
            raise ListingInvariantError(
                f"{channel.source_id} issue is future dated: {issue_date}"
            )
        if issue_date < source_today - timedelta(days=7):
            raise ListingInvariantError(
                f"{channel.source_id} current issue is stale: {issue_date}"
            )

        selected = [item for item in sections if _ANALYSIS_SECTION.search(item[1])]
        if not selected:
            raise ListingInvariantError(
                f"{channel.source_id} has no analysis-oriented issue sections"
            )

        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen_ids: set[str] = set()
        seen_urls: set[str] = set()
        methods: set[str] = set()
        for page_number, section_name, page_url in selected:
            page_html = context.fetch(page_url)
            self._reject_interstitial(
                channel.source_id,
                page_html,
                listing=True,
            )
            page_items, method = self._parse_section_page(
                channel,
                html=page_html,
                context=context,
                page_url=page_url,
                issue_date=issue_date,
                page_number=page_number,
                section_name=section_name,
                discovered_at=discovered_at,
            )
            methods.add(method)
            for item in page_items:
                if (
                    item.source_article_id in seen_ids
                    or item.canonical_url in seen_urls
                ):
                    raise ListingInvariantError(
                        f"{channel.source_id} duplicate issue article "
                        f"{item.canonical_url}"
                    )
                seen_ids.add(item.source_article_id)
                seen_urls.add(item.canonical_url)
                output.append(
                    SourceArticleIndex(
                        **{
                            **item.to_dict(),
                            "listing_position": len(output) + 1,
                        }
                    )
                )

        self.validate_listing(channel, output)
        if [item.listing_position for item in output] != list(
            range(1, len(output) + 1)
        ):
            raise ListingInvariantError(
                f"{channel.source_id} listing positions are not contiguous"
            )
        self._record(
            context,
            "listing_window",
            {
                "issue_date": issue_date.isoformat(),
                "analysis_sections": [name for _, name, _ in selected],
                "article_count": len(output),
                "adaptive_used": "adaptive" in methods,
            },
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
        title = adaptive.css(
            "div.detail-art h2#Title.art-title",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        date = adaptive.css(
            "div#paperdate.header-time",
            identifier=f"{channel.source_id}:detail-date",
            minimum_count=1,
            maximum_count=1,
        )
        body = adaptive.css(
            "div.detail-art div#ozoom.content",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not (title.elements and date.elements and body.elements):
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
        detail_date = self._date_from_label(
            date.elements[0].get_all_text(separator=" ", strip=True)
        )
        if detail_date is None or detail_date.isoformat() != index.published_at[:10]:
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

        author = self._first_text(adaptive.selector, "div.detail-art div#Author.author")
        method = (
            "adaptive"
            if "adaptive" in {title.method, date.method, body.method}
            else "exact"
        )
        structured = dict(index.structured_data)
        structured.update(
            {
                "detail_published_at": detail_date.isoformat(),
                "document_type_target": ("commentary", "long_feature"),
            }
        )
        if str(index.structured_data.get("issue_section") or "") == "政策解读":
            structured["document_type"] = "commentary"
        digest = sha256(f"{index.title}\n{clean_body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=clean_body,
            author=author,
            tags=(str(index.structured_data.get("issue_section") or ""),),
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={
                "title": "detail:div.detail-art h2#Title.art-title",
                "published_at": "detail:div#paperdate.header-time",
                "body": "detail:div.detail-art div#ozoom.content",
                "author": "detail:div.detail-art div#Author.author",
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
            config=IndustryRuleConfig(processor="rules:cena-v1"),
            funding_processor="rules:cena-funding-v1",
        )

    def _issue_sections(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> tuple[object, list[tuple[int, str, str]]]:
        adaptive = AdaptiveSelector(
            html,
            url=channel.url,
            storage_path=context.adaptive_db,
        )
        links = adaptive.css(
            "ul#list > li > a[href*='/node_']",
            identifier=f"{channel.source_id}:issue-section-link",
            minimum_count=1,
            maximum_count=24,
        )
        if not links.elements:
            raise ListingInvariantError(
                f"{channel.source_id} issue selector failed closed"
            )

        sections: list[tuple[int, str, str]] = []
        seen_pages: set[int] = set()
        issue_date = None
        for link in links.elements:
            href = str(link.attrib.get("href") or "")
            match = re.fullmatch(
                r"(?P<year>20\d{2})(?P<month>\d{2})/(?P<day>\d{2})/"
                r"node_(?P<page>\d{2})\.html",
                href,
            )
            text = self.clean_text(link.get_all_text(separator=" ", strip=True))
            label = re.fullmatch(r"第(\d{2})版(?:[：:]|\s)+\s*(.+)", text)
            if not match or not label or match.group("page") != label.group(1):
                raise ListingInvariantError(
                    f"{channel.source_id} invalid issue section link {href!r}"
                )
            try:
                link_date = datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                ).date()
            except ValueError as exc:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid issue date in {href!r}"
                ) from exc
            if issue_date is None:
                issue_date = link_date
            elif link_date != issue_date:
                raise ListingInvariantError(
                    f"{channel.source_id} issue index mixes publication dates"
                )
            page_number = int(match.group("page"))
            if page_number in seen_pages:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate issue section {page_number}"
                )
            seen_pages.add(page_number)
            section_name = self.clean_text(label.group(2))
            page_url = (
                "https://epaper.cena.com.cn/pc/layout/"
                f"{issue_date:%Y%m}/{issue_date:%d}/node_{page_number:02d}.html"
            )
            sections.append((page_number, section_name, page_url))
        sections.sort(key=lambda item: item[0])
        if issue_date is None:
            raise ListingInvariantError(
                f"{channel.source_id} invalid current issue date"
            )
        if [item[0] for item in sections] != list(
            range(sections[0][0], sections[-1][0] + 1)
        ):
            raise ListingInvariantError(
                f"{channel.source_id} issue sections are not contiguous"
            )
        return issue_date, sections

    def _parse_section_page(
        self,
        channel: SourceChannel,
        *,
        html: bytes,
        context: AdapterContext,
        page_url: str,
        issue_date,
        page_number: int,
        section_name: str,
        discovered_at: str,
    ) -> tuple[list[SourceArticleIndex], str]:
        adaptive = AdaptiveSelector(
            html,
            url=page_url,
            storage_path=context.adaptive_db,
        )
        issue = adaptive.css(
            "div#paperdate.header-time",
            identifier=f"{channel.source_id}:section-date",
            minimum_count=1,
            maximum_count=1,
        )
        layout = adaptive.css(
            "span#layout",
            identifier=f"{channel.source_id}:section-name",
            minimum_count=1,
            maximum_count=1,
        )
        links = adaptive.css(
            "ul#articlelist.newsList li.clearfix > a[href*='content_']",
            identifier=f"{channel.source_id}:section-article-link",
            minimum_count=1,
            maximum_count=40,
        )
        if not issue.elements or not layout.elements or not links.elements:
            raise ListingInvariantError(
                f"{channel.source_id} section {page_number} selector failed closed"
            )
        page_date = self._date_from_label(
            issue.elements[0].get_all_text(separator=" ", strip=True)
        )
        if page_date != issue_date:
            raise ListingInvariantError(
                f"{channel.source_id} section {page_number} issue date mismatch"
            )
        layout_text = self.clean_text(
            layout.elements[0].get_all_text(separator=" ", strip=True)
        )
        layout_match = re.fullmatch(r"第(\d{2})版[：:]\s*(.+)", layout_text)
        if (
            not layout_match
            or int(layout_match.group(1)) != page_number
            or self.clean_text(layout_match.group(2)) != section_name
        ):
            raise ListingInvariantError(
                f"{channel.source_id} section {page_number} layout mismatch"
            )

        output: list[SourceArticleIndex] = []
        for page_position, link in enumerate(links.elements, start=1):
            title = self.clean_text(link.get_all_text(separator=" ", strip=True))
            href = str(link.attrib.get("href") or "")
            article_match = re.fullmatch(
                r"\.\./\.\./\.\./content/20\d{4}/\d{2}/content_(\d+)\.html",
                href,
            )
            if not article_match:
                raise ListingInvariantError(
                    f"{channel.source_id} rejected section article href {href!r}"
                )
            canonical_url = (
                "https://epaper.cena.com.cn/pc/content/"
                f"{issue_date:%Y%m}/{issue_date:%d}/content_"
                f"{article_match.group(1)}.html"
            )
            article_id = self._article_id(canonical_url)
            if not article_id:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid section article {canonical_url}"
                )
            # Only structurally confirmed decoration is skipped. Short titles
            # such as "AI" or "芯片" are valid editorial headlines.
            link_class = str(link.attrib.get("class") or "")
            structural_decoration = (
                str(link.attrib.get("aria-hidden") or "").lower() == "true"
                or str(link.attrib.get("role") or "").lower() in {"none", "presentation"}
                or bool(_DECORATIVE_CLASS.search(link_class))
            )
            if structural_decoration and (
                not title or _DECORATIVE_ANCHOR_TITLE.fullmatch(title)
            ):
                self._record(
                    context,
                    "listing_anchor_skipped",
                    {
                        "reason": "structural_decoration",
                        "canonical_url": canonical_url,
                        "page_position": page_position,
                    },
                )
                continue
            if not title:
                detail_html = context.fetch(canonical_url)
                self._reject_interstitial(channel.source_id, detail_html, listing=True)
                detail = AdaptiveSelector(
                    detail_html, url=canonical_url, storage_path=context.adaptive_db
                )
                recovered = detail.css(
                    "div.detail-art h2#Title.art-title",
                    identifier=f"{channel.source_id}:listing-title-recovery",
                    minimum_count=1,
                    maximum_count=1,
                )
                detail_date = detail.css(
                    "div#paperdate.header-time",
                    identifier=f"{channel.source_id}:listing-date-recovery",
                    minimum_count=1,
                    maximum_count=1,
                )
                if not recovered.elements or not detail_date.elements:
                    raise ListingInvariantError(
                        f"{channel.source_id} could not recover empty listing title"
                    )
                title = self.clean_text(
                    recovered.elements[0].get_all_text(separator=" ", strip=True)
                )
                recovered_date = self._date_from_label(
                    detail_date.elements[0].get_all_text(separator=" ", strip=True)
                )
                if not title or recovered_date != issue_date:
                    raise ListingInvariantError(
                        f"{channel.source_id} invalid recovered listing title/date"
                    )
                self._record(
                    context,
                    "listing_title_recovered",
                    {"canonical_url": canonical_url, "page_position": page_position},
                )
            published_at = datetime.combine(
                issue_date,
                datetime.min.time(),
                tzinfo=_CHINA,
            ).isoformat()
            structured = {
                "issue_date": issue_date.isoformat(),
                "issue_page": page_number,
                "issue_section": section_name,
                "page_position": page_position,
                "document_type_target": ("commentary", "long_feature"),
            }
            content_hash = self.stable_hash(
                "\n".join(
                    (
                        canonical_url,
                        title,
                        published_at,
                        repr(sorted(structured.items())),
                    )
                )
            )
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel=section_name,
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=page_url,
                    listing_position=page_position,
                    content_hash=content_hash,
                    discovery_method=links.method,
                    structured_data=structured,
                )
            )
        method = (
            "adaptive"
            if "adaptive" in {issue.method, layout.method, links.method}
            else "exact"
        )
        return output, method

    @staticmethod
    def _article_id(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "epaper.cena.com.cn":
            return ""
        match = _ARTICLE_PATH.fullmatch(parsed.path)
        if not match:
            return ""
        return match.group("id")

    @staticmethod
    def _date_from_label(value: str):
        match = _ISSUE_DATE.search(value)
        if not match:
            return None
        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            ).date()
        except ValueError:
            return None

    @staticmethod
    def _source_today(now: datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(_CHINA).date()

    @classmethod
    def _clean_body(cls, element) -> str:
        blocks: list[str] = []
        seen: set[str] = set()
        for block in element.xpath(".//p | .//h2 | .//h3 | .//h4 | .//li | .//blockquote"):
            text = cls.clean_text(block.get_all_text(separator=" ", strip=True))
            if not text or text in seen or _BODY_NOISE.search(text):
                continue
            seen.add(text)
            blocks.append(text)
        return "\n".join(blocks).strip()

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s\u200b｜|]", "", expected)
        right = re.sub(r"[\s\u200b｜|]", "", actual)
        return bool(left and right and left == right)

    @staticmethod
    def _first_text(node: object, selector: str) -> str:
        elements = tuple(node.css(selector))
        if len(elements) != 1:
            return ""
        return re.sub(
            r"\s+",
            " ",
            elements[0].get_all_text(separator=" ", strip=True),
        ).strip()

    @staticmethod
    def _record(context: AdapterContext, key: str, payload: dict[str, object]) -> None:
        if context.record_decision is not None:
            context.record_decision(key, payload)

    @staticmethod
    def _reject_interstitial(source_id: str, html: bytes, *, listing: bool) -> None:
        text = html.decode("utf-8", errors="ignore")
        if not _ACCESS_INTERSTITIAL.search(text):
            return
        error = ListingInvariantError if listing else DetailFetchError
        raise error(f"{source_id} access interstitial detected; no bypass attempted")


__all__ = ["CenaAdapter"]
