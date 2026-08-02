"""Fail-closed adapter for Jazzyear's public research-report column.

The source exposes a server-rendered first page of research reports.  This
adapter intentionally captures that complete visible page: storage-level
deduplication determines the daily increment.  Scrapling may relocate the
verified DOM selectors, but deterministic source, URL, title, date, report
type, copyright, and body invariants decide whether an item is accepted.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re
from urllib.parse import parse_qsl, urljoin, urlparse
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
_DETAIL_PATH = "/study_info.html"
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问过于频繁|Access Denied|403 Forbidden|"
    r"captcha-container|TTGCaptcha)",
    re.I,
)
_RESEARCH_DOCUMENT_TYPES = {
    "月度观察": "commentary",
    "年度观察": "commentary",
    "趋势判断": "commentary",
    "深度行研": "long_feature",
    "定义者": "long_feature",
    "白皮书": "long_feature",
    "产业图谱": "long_feature",
    "甲子Builders": "long_feature",
    "甲子破壁机": "long_feature",
}
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


class JazzyearResearchAdapter(AggregateAdapter):
    """Enumerate and validate Jazzyear's current visible research window."""

    adapter_id = "jazzyear_research"
    channels = (
        SourceChannel(
            source_id="jazzyear-research",
            name="甲子光年—研究报告",
            url="https://www.jazzyear.com/study_list.html",
            source_grade="B",
            event_prior=_EVENT_TYPES,
            allowed_hosts=("www.jazzyear.com", "jazzyear.com"),
            allowed_path_patterns=(r"/study_info\.html",),
        ),
    )
    minimum_listing_count = 4
    maximum_listing_count = 20

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
        cards = adaptive.css(
            "div.study-list > a.article-card-cover-sm[href*='study_info.html?id=']",
            identifier=f"{channel.source_id}:listing-card",
            minimum_count=self.minimum_listing_count,
            maximum_count=self.maximum_listing_count,
        )
        if not cards.elements:
            raise ListingInvariantError(
                f"{channel.source_id} research listing selector failed closed"
            )

        output: list[SourceArticleIndex] = []
        seen_ids: set[str] = set()
        previous_date = None
        discovered_at = context.now.replace(microsecond=0).isoformat()
        for page_position, card in enumerate(cards.elements, start=1):
            canonical_url = self._canonical_url(
                urljoin(channel.url, str(card.attrib.get("href") or ""))
            )
            article_id = self._article_id(canonical_url)
            if not article_id or article_id in seen_ids:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid or duplicate research URL"
                )

            title = self._only_text(
                card,
                "div.center > div.title",
                error=(
                    f"{channel.source_id} article {article_id} title cardinality failed"
                ),
            )
            date_text = self._only_text(
                card,
                "div.center > div.bottom > span.time",
                error=(
                    f"{channel.source_id} article {article_id} date cardinality failed"
                ),
            )
            published_date = self._parse_date(date_text)
            if published_date is None or published_date > self._source_today(context.now):
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} has invalid date"
                )
            if previous_date is not None and published_date > previous_date:
                raise ListingInvariantError(
                    f"{channel.source_id} research listing is not newest-first"
                )
            previous_date = published_date

            report_label = self._only_text(
                card,
                "div.center > div.bottom > span.btn",
                error=(
                    f"{channel.source_id} article {article_id} report marker failed"
                ),
            )
            original_label = self._only_text(
                card,
                "div.cover > div.tag",
                error=(
                    f"{channel.source_id} article {article_id} original marker failed"
                ),
            )
            research_type = self._only_text(
                card,
                "div.center > div.tags > span.subscribe-item",
                error=(
                    f"{channel.source_id} article {article_id} research type failed"
                ),
            )
            if report_label != "报告" or original_label != "原创":
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} is not an original report"
                )
            document_type = _RESEARCH_DOCUMENT_TYPES.get(research_type)
            if document_type is None:
                raise ListingInvariantError(
                    f"{channel.source_id} unknown research type {research_type!r}"
                )

            tag_nodes = tuple(card.css("div.center > div.tags > span:not(.subscribe-item)"))
            if len(tag_nodes) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} tag cardinality failed"
                )
            tags = tuple(
                part.strip()
                for part in self.clean_text(
                    tag_nodes[0].get_all_text(separator=" ", strip=True)
                ).split("·")
                if part.strip()
            )
            if not tags:
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} has no research tags"
                )

            published_at = datetime.combine(
                published_date,
                datetime.min.time(),
                tzinfo=_CHINA,
            ).isoformat()
            structured = {
                "page_position": page_position,
                "research_type": research_type,
                "report_label": report_label,
                "original_label": original_label,
                "tags": tags,
                "document_type": document_type,
                "document_type_target": ("commentary", "long_feature"),
            }
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="research",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=page_position,
                    content_hash=self.stable_hash(
                        "\n".join(
                            (
                                canonical_url,
                                title,
                                published_at,
                                research_type,
                                repr(tags),
                            )
                        )
                    ),
                    discovery_method=cards.method,
                    structured_data=structured,
                )
            )
            seen_ids.add(article_id)

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
        title = adaptive.css(
            "div.study-base div.center > div.name",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        date = adaptive.css(
            "div.study-base div.tags > span.time",
            identifier=f"{channel.source_id}:detail-date",
            minimum_count=1,
            maximum_count=1,
        )
        options = adaptive.css(
            "div.study-base div.option",
            identifier=f"{channel.source_id}:detail-options",
            minimum_count=2,
            maximum_count=4,
        )
        blocks = adaptive.css(
            "div.study-detail",
            identifier=f"{channel.source_id}:detail-blocks",
            minimum_count=1,
            maximum_count=4,
        )
        if not title.elements or not date.elements or not options.elements or not blocks.elements:
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
        detail_date = self._parse_date(
            date.elements[0].get_all_text(separator=" ", strip=True)
        )
        if detail_date is None or detail_date.isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )

        metadata: dict[str, str] = {}
        for option in options.elements:
            labels = tuple(option.css("span.label"))
            values = tuple(option.css("span.msg"))
            if len(labels) != 1 or len(values) != 1:
                raise DetailFetchError(
                    f"{channel.source_id} detail metadata cardinality failed"
                )
            label = self.clean_text(
                labels[0].get_all_text(separator=" ", strip=True)
            ).rstrip("：:")
            value = self.clean_text(
                values[0].get_all_text(separator=" ", strip=True)
            )
            if not label or not value or label in metadata:
                raise DetailFetchError(
                    f"{channel.source_id} detail metadata failed closed"
                )
            metadata[label] = value
        if metadata.get("版权所有") != "甲子光年":
            raise DetailFetchError(
                f"{channel.source_id} detail copyright marker mismatch"
            )
        report_summary = metadata.get("报告简介", "")
        if not 20 <= len(report_summary) <= 2_000:
            raise DetailFetchError(
                f"{channel.source_id} detail report summary is invalid"
            )

        section_text: dict[str, str] = {}
        for block in blocks.elements:
            headings = tuple(block.css("div.study-block-title"))
            if len(headings) != 1:
                raise DetailFetchError(
                    f"{channel.source_id} detail section heading cardinality failed"
                )
            heading = self.clean_text(
                headings[0].get_all_text(separator=" ", strip=True)
            )
            full_text = self.clean_text(
                block.get_all_text(separator=" ", strip=True)
            )
            content = full_text[len(heading) :].strip() if full_text.startswith(heading) else ""
            if not heading or not content or heading in section_text:
                raise DetailFetchError(
                    f"{channel.source_id} detail section failed closed"
                )
            section_text[heading] = content
        highlights = section_text.get("核心亮点", "")
        if len(highlights) < 120:
            raise DetailFetchError(
                f"{channel.source_id} detail core highlights too short"
            )

        clean_body = self.clean_text(
            f"报告简介：{report_summary} 核心亮点：{highlights}"
        )
        if not 200 <= len(clean_body) <= 50_000:
            raise DetailFetchError(
                f"{channel.source_id} detail body length is invalid"
            )
        research_type = str(index.structured_data.get("research_type") or "")
        document_type = _RESEARCH_DOCUMENT_TYPES.get(research_type)
        if document_type != index.structured_data.get("document_type"):
            raise DetailFetchError(
                f"{channel.source_id} research type/document route mismatch"
            )

        methods = {title.method, date.method, options.method, blocks.method}
        method = "adaptive" if "adaptive" in methods else "exact"
        structured = dict(index.structured_data)
        structured.update(
            {
                "detail_published_at": detail_date.isoformat(),
                "report_summary": report_summary,
                "section_headings": tuple(section_text),
            }
        )
        digest = sha256(f"{index.title}\n{clean_body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=clean_body,
            author="甲子光年智库",
            tags=tuple(index.structured_data.get("tags") or ()),
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={
                "title": "detail:div.study-base div.center>div.name",
                "published_at": "detail:div.study-base div.tags>span.time",
                "body": "detail:div.study-base div.option + div.study-detail",
                "copyright": "detail:div.study-base div.option",
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
        return extract_media_events(
            channel,
            article,
            config=IndustryRuleConfig(processor="rules:jazzyear-research-v1"),
            funding_processor="rules:jazzyear-research-funding-v1",
        )

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower()
            not in {"jazzyear.com", "www.jazzyear.com"}
            or parsed.path != _DETAIL_PATH
        ):
            return ""
        article_id = JazzyearResearchAdapter._query_id(parsed.query)
        if not article_id:
            return ""
        return f"https://www.jazzyear.com/study_info.html?id={article_id}"

    @staticmethod
    def _article_id(url: str) -> str:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower()
            not in {"jazzyear.com", "www.jazzyear.com"}
            or parsed.path != _DETAIL_PATH
        ):
            return ""
        return JazzyearResearchAdapter._query_id(parsed.query)

    @staticmethod
    def _query_id(query: str) -> str:
        try:
            pairs = parse_qsl(
                query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError:
            return ""
        if len(pairs) != 1 or pairs[0][0] != "id" or not pairs[0][1].isdigit():
            return ""
        return pairs[0][1]

    @staticmethod
    def _parse_date(value: str):
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value.strip()):
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def _source_today(now: datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(_CHINA).date()

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = "".join(character for character in expected if character.isalnum())
        right = "".join(character for character in actual if character.isalnum())
        return bool(left and left == right)

    @classmethod
    def _only_text(cls, node: object, selector: str, *, error: str) -> str:
        values = tuple(node.css(selector))
        if len(values) != 1:
            raise ListingInvariantError(error)
        value = cls.clean_text(values[0].get_all_text(separator=" ", strip=True))
        if not value:
            raise ListingInvariantError(error)
        return value

    @staticmethod
    def _reject_interstitial(source_id: str, html: bytes, *, listing: bool) -> None:
        text = html.decode("utf-8", errors="ignore")
        if not _ACCESS_INTERSTITIAL.search(text):
            return
        exception = ListingInvariantError if listing else DetailFetchError
        raise exception(
            f"{source_id} access interstitial detected; no bypass attempted"
        )


__all__ = ["JazzyearResearchAdapter"]
