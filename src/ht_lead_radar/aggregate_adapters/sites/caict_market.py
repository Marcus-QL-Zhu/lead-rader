"""Fail-closed adapter for CAICT's public mobile-market research series.

The CAICT main site currently applies an access interstitial to automated
clients.  The Global Market Access public-service platform is also operated by
CAICT, is server-rendered, and exposes a stable ``业内新闻`` archive.  This
adapter deliberately narrows that mixed archive to the recurring
``国内手机市场运行分析报告`` series and keeps the six newest visible reports.

Scrapling is used only to relocate already-verified DOM containers.  Report
identity, URL, date, ordering, pagination, title/detail equality, authorship,
and body structure remain deterministic and fail closed.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import math
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
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_CHINA = ZoneInfo("Asia/Shanghai")
_PAGE_SIZE = 20
_VISIBLE_REPORT_WINDOW = 6
_ARTICLE_PATH = re.compile(
    r"/plat/news/(?P<slug>caict-release-china-mobile-phone-market-analysis-"
    r"report-(?P<month>[a-z]+)-(?P<year>20\d{2}))"
)
_REPORT_TITLE = re.compile(
    r"^中国信通院发布(?P<year>20\d{2})年(?P<month>1[0-2]|[1-9])月"
    r"国内手机市场运行分析报告(?:：|:).+$"
)
_PAGINATION = re.compile(r"^总数(?P<total>\d+),\s*共(?P<pages>\d+)页$")
_DATE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
_META_DATE = re.compile(r"^发布时间[：:]\s*(20\d{2}-\d{2}-\d{2})$")
_META_AUTHOR = re.compile(r"^作者[：:]\s*(.+)$")
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问防护规则|访问过于频繁|Access Denied|"
    r"403 Forbidden|captcha-container|TTGCaptcha)",
    re.I,
)
_FIGURE_CAPTION = re.compile(r"^图\s*\d+[：:\s]")
_SECTION_HEADING = re.compile(r"^[一二三四五六七八九十]{1,3}、")
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_ALLOWED_AUTHORS = frozenset({"CTTL-T", "CAICT", "中国信通院"})


class CaictMarketAnalysisAdapter(AggregateAdapter):
    """Enumerate CAICT's newest six public mobile-market analyses."""

    adapter_id = "caict_market_analysis"
    channels = (
        SourceChannel(
            source_id="caict-mobile-market-analysis",
            name="中国信通院—国内手机市场运行分析",
            url="https://gma.caict.ac.cn/plat/news",
            source_grade="A",
            event_prior=(),
            allowed_hosts=("gma.caict.ac.cn",),
            allowed_path_patterns=(
                r"/plat/news/caict-release-china-mobile-phone-market-analysis-"
                r"report-[a-z]+-20\d{2}",
            ),
        ),
    )
    minimum_listing_count = _VISIBLE_REPORT_WINDOW
    maximum_listing_count = _VISIBLE_REPORT_WINDOW

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
        rows = adaptive.css(
            "main div.row.mt-0.mb-1 > div.col-lg-9.col-md-8.col-sm-12.mb-2 "
            "> div.row.p-2",
            identifier=f"{channel.source_id}:listing-row",
            minimum_count=1,
            maximum_count=_PAGE_SIZE,
        )
        pagination = adaptive.css(
            "main ul.pagination",
            identifier=f"{channel.source_id}:listing-pagination",
            minimum_count=1,
            maximum_count=1,
        )
        if not rows.elements or not pagination.elements:
            raise ListingInvariantError(
                f"{channel.source_id} listing selector failed closed"
            )

        total, pages = self._pagination_metadata(
            channel.source_id,
            pagination.elements[0],
        )
        expected_rows = min(total, _PAGE_SIZE)
        if len(rows.elements) != expected_rows:
            raise ListingInvariantError(
                f"{channel.source_id} listing row count {len(rows.elements)} "
                f"does not match expected {expected_rows}"
            )
        if pages != math.ceil(total / _PAGE_SIZE):
            raise ListingInvariantError(
                f"{channel.source_id} pagination page count mismatch"
            )

        discovered_at = context.now.replace(microsecond=0).isoformat()
        source_today = self._source_today(context.now)
        report_indexes: list[SourceArticleIndex] = []
        seen_page_urls: set[str] = set()
        previous_date = None
        for page_position, row in enumerate(rows.elements, start=1):
            links = tuple(row.css("div.col-md-10 > a[href]"))
            dates = tuple(row.css("div.col-md-2 > span"))
            if len(links) != 1 or len(dates) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} row {page_position} "
                    "title/date/link cardinality failed"
                )
            title = self.clean_text(
                links[0].get_all_text(separator=" ", strip=True)
            )
            href = str(links[0].attrib.get("href") or "")
            page_url = self._canonical_url(urljoin(channel.url, href))
            date_value = self.clean_text(
                dates[0].get_all_text(separator=" ", strip=True)
            )
            published = self._parse_date(date_value)
            if not title or published is None:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid row {page_position}"
                )
            if published > source_today:
                raise ListingInvariantError(
                    f"{channel.source_id} row {page_position} is future dated"
                )
            if previous_date is not None and published > previous_date:
                raise ListingInvariantError(
                    f"{channel.source_id} listing is not newest-first at "
                    f"row {page_position}"
                )
            previous_date = published
            if page_url in seen_page_urls:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate listing URL {page_url}"
                )
            seen_page_urls.add(page_url)

            title_match = _REPORT_TITLE.fullmatch(title)
            article_id = self._article_id(page_url)
            if bool(title_match) != bool(article_id):
                raise ListingInvariantError(
                    f"{channel.source_id} report title/URL identity mismatch "
                    f"at row {page_position}"
                )
            if title_match is None:
                continue
            self._validate_report_identity(
                channel.source_id,
                article_id,
                title_match,
                page_position,
            )
            published_at = datetime.combine(
                published,
                datetime.min.time(),
                tzinfo=_CHINA,
            ).isoformat()
            structured = {
                "document_type": "commentary",
                "source_section": "业内新闻",
                "research_series": "国内手机市场运行分析报告",
                "report_year": int(title_match.group("year")),
                "report_month": int(title_match.group("month")),
                "page_position": page_position,
                "archive_total_count": total,
                "archive_total_pages": pages,
            }
            report_indexes.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="业内新闻—产业运行分析",
                    canonical_url=page_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=len(report_indexes) + 1,
                    content_hash=self.stable_hash(
                        "\n".join((page_url, title, published_at))
                    ),
                    discovery_method=rows.method,
                    structured_data=structured,
                )
            )

        if len(report_indexes) < _VISIBLE_REPORT_WINDOW:
            raise ListingInvariantError(
                f"{channel.source_id} exposes only {len(report_indexes)} "
                "validated market-analysis reports"
            )
        output = report_indexes[:_VISIBLE_REPORT_WINDOW]
        self.validate_listing(channel, output)
        self._record(
            context,
            "listing_window",
            {
                "page_row_count": len(rows.elements),
                "validated_report_count": len(report_indexes),
                "selected_report_count": len(output),
                "adaptive_used": rows.method == "adaptive",
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
            "main div.col-lg-9.col-md-8.col-sm-12.mb-2 > h4.text-center.py-2",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        metadata = adaptive.css(
            "main div.col-lg-9.col-md-8.col-sm-12.mb-2 "
            "> p.text-center > span.post-meta.m-2",
            identifier=f"{channel.source_id}:detail-metadata",
            minimum_count=2,
            maximum_count=2,
        )
        body = adaptive.css(
            "main div.col-lg-9.col-md-8.col-sm-12.mb-2 > div.post-body",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not title.elements or not metadata.elements or not body.elements:
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
        author, published_at = self._detail_metadata(metadata.elements)
        if author not in _ALLOWED_AUTHORS:
            raise DetailFetchError(
                f"{channel.source_id} unexpected detail author for "
                f"{index.source_article_id}"
            )
        if published_at != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )

        clean_body, section_count = self._clean_report_body(body.elements[0])
        if not 300 <= len(clean_body) <= 50_000:
            raise DetailFetchError(
                f"{channel.source_id} detail body length invalid for "
                f"{index.source_article_id}"
            )
        if (
            section_count < 3
            or "出货量" not in clean_body
            or "%" not in clean_body
            or "报告完成单位：中国信息通信研究院" not in clean_body
        ):
            raise DetailFetchError(
                f"{channel.source_id} detail research structure invalid for "
                f"{index.source_article_id}"
            )

        methods = {title.method, metadata.method, body.method}
        extraction_method = "adaptive" if "adaptive" in methods else "exact"
        structured = {
            **index.structured_data,
            "document_type": "commentary",
            "detail_published_at": published_at,
            "author": author,
            "section_count": section_count,
            "report_completion_unit": "中国信息通信研究院",
        }
        digest = sha256(f"{index.title}\n{clean_body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=clean_body,
            author=author,
            tags=("行业研究", "手机市场", "ICT产业"),
            structured_data=structured,
            extraction_method=extraction_method,
            adaptive_similarity=72 if extraction_method == "adaptive" else None,
            evidence_locators={
                "title": "detail:main h4.text-center.py-2",
                "published_at": "detail:span.post-meta 发布时间",
                "author": "detail:span.post-meta 作者",
                "body": "detail:div.post-body",
                "research_identity": "detail:报告完成单位：中国信息通信研究院",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        """Do not invent company events from an official macro research report."""

        del channel, article
        return []

    @staticmethod
    def _pagination_metadata(source_id: str, element: object) -> tuple[int, int]:
        matches = []
        for node in element.css("span.page-link"):
            text = CaictMarketAnalysisAdapter.clean_text(
                node.get_all_text(separator=" ", strip=True)
            )
            match = _PAGINATION.fullmatch(text)
            if match is not None:
                matches.append(match)
        if len(matches) != 1:
            raise ListingInvariantError(
                f"{source_id} pagination metadata missing or ambiguous"
            )
        total = int(matches[0].group("total"))
        pages = int(matches[0].group("pages"))
        if total < _VISIBLE_REPORT_WINDOW or pages < 1:
            raise ListingInvariantError(f"{source_id} pagination values invalid")
        return total, pages

    @staticmethod
    def _validate_report_identity(
        source_id: str,
        article_id: str,
        title_match: re.Match[str],
        page_position: int,
    ) -> None:
        path_match = _ARTICLE_PATH.fullmatch(f"/plat/news/{article_id}")
        month = _MONTHS.get(path_match.group("month") if path_match else "")
        if (
            path_match is None
            or month != int(title_match.group("month"))
            or path_match.group("year") != title_match.group("year")
        ):
            raise ListingInvariantError(
                f"{source_id} report month/year identity mismatch at "
                f"row {page_position}"
            )

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlparse(value)
        if (parsed.hostname or "").lower() != "gma.caict.ac.cn":
            return value
        match = _ARTICLE_PATH.fullmatch(parsed.path.rstrip("/"))
        if match is None:
            return value
        return f"https://gma.caict.ac.cn/plat/news/{match.group('slug')}"

    @staticmethod
    def _article_id(value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "gma.caict.ac.cn"
        ):
            return ""
        match = _ARTICLE_PATH.fullmatch(parsed.path.rstrip("/"))
        return match.group("slug") if match else ""

    @staticmethod
    def _parse_date(value: str):
        if not _DATE.fullmatch(value):
            return None
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None

    @staticmethod
    def _source_today(now: datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(_CHINA).date()

    @classmethod
    def _detail_metadata(cls, elements: tuple[object, ...]) -> tuple[str, str]:
        author = ""
        published_at = ""
        for element in elements:
            value = cls.clean_text(
                element.get_all_text(separator=" ", strip=True)
            )
            author_match = _META_AUTHOR.fullmatch(value)
            date_match = _META_DATE.fullmatch(value)
            if author_match is not None:
                if author:
                    return "", ""
                author = cls.clean_text(author_match.group(1))
            elif date_match is not None:
                if published_at:
                    return "", ""
                published_at = date_match.group(1)
            else:
                return "", ""
        return author, published_at

    @classmethod
    def _clean_report_body(cls, element: object) -> tuple[str, int]:
        blocks: list[str] = []
        seen: set[str] = set()
        section_count = 0
        for block in element.css("p, h2, h3, li"):
            text = cls.clean_text(
                block.get_all_text(separator=" ", strip=True)
            )
            if not text or text in seen or _FIGURE_CAPTION.match(text):
                continue
            seen.add(text)
            section_count += bool(_SECTION_HEADING.match(text))
            blocks.append(text)
        return "\n".join(blocks), section_count

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s\u200b]+", "", expected)
        right = re.sub(r"[\s\u200b]+", "", actual)
        return bool(left and right and left == right)

    @staticmethod
    def _reject_interstitial(
        source_id: str,
        html: bytes,
        *,
        listing: bool,
    ) -> None:
        if not _ACCESS_INTERSTITIAL.search(
            html.decode("utf-8", errors="ignore")
        ):
            return
        error = f"{source_id} access interstitial detected; no bypass attempted"
        if listing:
            raise ListingInvariantError(error)
        raise DetailFetchError(error)

    @staticmethod
    def _record(
        context: AdapterContext,
        key: str,
        payload: dict[str, object],
    ) -> None:
        context.decision_state[key] = dict(payload)
        if context.record_decision is not None:
            context.record_decision(key, dict(payload))


__all__ = ["CaictMarketAnalysisAdapter"]
