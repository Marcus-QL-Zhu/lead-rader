"""Dedicated adapter for Investment Network (Pedaily) public news listings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from urllib.parse import urljoin, urlparse

from ..adaptive import AdaptiveSelector
from ..base import (
    AdapterContext,
    AggregateAdapter,
    DetailFetchError,
    ListingInvariantError,
)
from ..entities import canonical_company_name
from ..finance_rules import FundingRuleConfig, extract_funding_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_DETAIL_PATH = re.compile(r"/(?P<month>\d{6})/(?P<article_id>\d+)\.shtml")
_LISTING_TIME = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
_LEGAL_COMPANY = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·（）()]{4,60}"
    r"(?:有限责任公司|股份有限公司|有限公司))"
)
_ALIAS_RELATION = re.compile(
    r"(?P<legal>[\u4e00-\u9fffA-Za-z0-9·（）()]{4,60}"
    r"(?:有限责任公司|股份有限公司|有限公司))"
    r".{0,16}?(?:以下简称|下称)?"
    r"[“「『\"](?P<alias>[^”」』\"]{2,40})[”」』\"]"
)
_QUOTED_COMPANY = re.compile(r"[「『“\"]([^」』”\"]{2,40})[」』”\"]")
_TITLE_ASSERTION = re.compile(
    r"(?:宣布|官宣|成功|已|正式)*(?:完成|获得|获(?!悉)|斩获|启动|开启)"
)
_NON_COMPANY = re.compile(
    r"(?:报告|活动|论坛|大会|政策|规定|成本|需求|盘点|榜单|基金|债券|"
    r"行业|市场|项目|品牌|企业)$"
    r"|^(?:投融资|融资|创新药械投融资|本轮|此轮|本次|此次|同时|消息)"
)
_LEADING_LABEL = re.compile(
    r"^(?:(?:首发|独家|重磅|投资界消息)\s*[｜|丨]\s*)+"
)
_NOISE_PARAGRAPH_CLASSES = frozenset(
    {
        "orginstr",
        "originatips",
        "news-download",
    }
)
_PLACEHOLDER_AUTHORS = frozenset({"关注你关注的"})
_STRATEGIC_MERGER = re.compile(
    r"(?:宣布)?完成与[^。；]{2,60}?的战略合并"
)
_UPCOMING_PRODUCT_RELEASE = re.compile(
    r"(?:还将|将在|即将)[^。；]{0,100}?"
    r"(?:发布|亮相)[^。；]{0,100}?"
    r"(?:新产品|模型|机器人|系统|基础设施)"
)


class PedailyAdapter(AggregateAdapter):
    """Enumerate and parse Pedaily's two public investment channels."""

    adapter_id = "pedaily"
    channels = (
        SourceChannel(
            source_id="pedaily-vcpe-events",
            name="投资界—投资事件",
            url="https://www.pedaily.cn/vcpeevent/",
            source_grade="B",
            event_prior=("funding",),
            allowed_hosts=("news.pedaily.cn",),
            allowed_path_patterns=(r"/\d{6}/\d+\.shtml",),
        ),
        SourceChannel(
            source_id="pedaily-investment-news",
            name="投资界—投融资行业",
            url="https://www.pedaily.cn/i2826/",
            source_grade="B",
            event_prior=("funding",),
            allowed_hosts=("news.pedaily.cn",),
            allowed_path_patterns=(r"/\d{6}/\d+\.shtml",),
        ),
    )
    minimum_listing_count = 10
    maximum_listing_count = 60

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        adaptive = AdaptiveSelector(
            html,
            url=channel.url,
            storage_path=context.adaptive_db,
        )
        selected = adaptive.css(
            "ul#newslist-all > li.h-news",
            identifier=f"{channel.source_id}:listing-item",
            minimum_count=self.minimum_listing_count,
            maximum_count=self.maximum_listing_count,
        )
        if not selected.elements:
            raise ListingInvariantError(
                f"{channel.source_id} article-list selector failed"
            )

        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        discovered_at = context.now.replace(microsecond=0).isoformat()
        previous_time: datetime | None = None
        for position, item in enumerate(selected.elements, start=1):
            links = tuple(item.css("div.txt > h3 > a"))
            if len(links) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} has "
                    f"{len(links)} title links"
                )
            link = links[0]
            title = self.clean_text(
                link.get_all_text(separator=" ", strip=True)
            )
            canonical_url = self._canonical_url(
                urljoin(channel.url, str(link.attrib.get("href") or ""))
            )
            match = _DETAIL_PATH.fullmatch(urlparse(canonical_url).path)
            if not match:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid article URL {canonical_url}"
                )
            article_id = match.group("article_id")
            if article_id in seen:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate article id {article_id}"
                )

            dates = tuple(item.css("div.info > span.date"))
            if len(dates) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} has "
                    f"{len(dates)} dates"
                )
            time_label = self.clean_text(
                dates[0].get_all_text(separator=" ", strip=True)
            )
            published_at, published_time = self._parse_listing_time(
                time_label,
                context,
            )
            if previous_time is not None and published_time > previous_time:
                raise ListingInvariantError(
                    f"{channel.source_id} listing is not newest-first "
                    f"at position {position}"
                )
            previous_time = published_time

            images = tuple(item.css("div.image img"))
            image_alt = (
                self.clean_text(str(images[0].attrib.get("alt") or ""))
                if images
                else ""
            )
            if image_alt and image_alt != title:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} title/image mismatch"
                )
            author = self._first_text(item, "div.info > span.author")
            structured = {
                "listing_author": author,
                "time_label": time_label,
                "special_ids": str(item.attrib.get("data-special") or ""),
                "resource_type_id": str(
                    item.attrib.get("data-restypeid") or ""
                ),
                "industry_id": str(item.attrib.get("data-indid") or ""),
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
                    channel=self._channel_name(channel),
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=position,
                    content_hash=content_hash,
                    discovery_method=selected.method,
                    structured_data=structured,
                )
            )
            seen.add(article_id)
        self.validate_listing(channel, output)
        return output

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        title_selection = adaptive.css(
            "h1#newstitle",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        if not title_selection.elements:
            raise DetailFetchError(
                f"{channel.source_id} detail title selector failed "
                f"for {index.source_article_id}"
            )
        page_title = self.clean_text(
            title_selection.elements[0].get_all_text(
                separator=" ",
                strip=True,
            )
        )
        if page_title != index.title:
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch "
                f"for {index.source_article_id}"
            )

        body_selection = adaptive.css(
            "div#news-content",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not body_selection.elements:
            raise DetailFetchError(
                f"{channel.source_id} detail body selector failed "
                f"for {index.source_article_id}"
            )
        paragraphs = self._body_paragraphs(body_selection.elements[0])
        body = self.clean_text(" ".join(paragraphs))
        if len(body) < 80:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short "
                f"for {index.source_article_id}"
            )

        published = self._detail_published_at(adaptive)
        if published and published[:10] != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} detail/listing date mismatch "
                f"for {index.source_article_id}"
            )
        subject = self._first_text(
            adaptive.selector,
            "header.newsinfo > div.subject",
        )
        author = self._author(adaptive, index)
        raw_legal, alias = self._company_alias_relation(body)
        legal = canonical_company_name(raw_legal)
        title_company = canonical_company_name(
            self._title_company(index.title)
        )
        company = legal or title_company
        mentions = tuple(
            dict.fromkeys(
                item
                for item in (
                    legal,
                    raw_legal,
                    alias,
                    title_company,
                )
                if item
            )
        )
        structured = dict(index.structured_data)
        structured.update(
            {
                "subject": subject,
                "detail_published_at": published,
                "company": company,
                "company_mentions": mentions,
            }
        )
        digest = sha256(
            f"{index.title}\n{body}".encode("utf-8")
        ).hexdigest()
        method = (
            "adaptive"
            if "adaptive"
            in {title_selection.method, body_selection.method}
            else "exact"
        )
        similarity = (
            72
            if method == "adaptive"
            else None
        )
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=similarity,
            evidence_locators={
                "title": "detail:h1#newstitle",
                "body": "detail:div#news-content>p:not(.orginstr)",
                "published_at": (
                    "detail:header.newsinfo time[datetime]"
                ),
                "company": "detail:article body/title explicit mention",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        funding = extract_funding_events(
            channel,
            article,
            config=FundingRuleConfig(
                processor="rules:pedaily-v2",
                company_resolver=self._company_for_event,
            ),
        )
        operational = self._operational_events(
            channel,
            article,
            funding,
        )
        return [*funding, *operational]

    @classmethod
    def _operational_events(
        cls,
        channel: SourceChannel,
        article: CleanArticle,
        funding: list[SemanticEvent],
    ) -> list[SemanticEvent]:
        company = canonical_company_name(
            cls.clean_text(
                str(article.structured_data.get("company") or "")
            )
        )
        mentions = tuple(
            dict.fromkeys(
                cls.clean_text(str(item))
                for item in article.structured_data.get(
                    "company_mentions",
                    (),
                )
                if cls.clean_text(str(item))
            )
        )
        if not cls._valid_company(company):
            return []
        if not mentions:
            mentions = (company,)
        tags = tuple(
            dict.fromkeys(
                tag
                for event in funding
                for tag in event.industry_tags
            )
        ) or ("other",)
        output: list[SemanticEvent] = []
        for sentence in re.split(r"(?<=[。！？])\s*", article.clean_body):
            quote = cls.clean_text(sentence)
            if not quote or not any(
                mention in quote for mention in mentions
            ):
                continue
            if _STRATEGIC_MERGER.search(quote):
                output.append(
                    SemanticEvent(
                        source_id=channel.source_id,
                        source_article_id=article.index.source_article_id,
                        canonical_url=article.index.canonical_url,
                        company_mentions=mentions,
                        canonical_company=company,
                        event_type="merger_acquisition",
                        event_date=article.index.published_at[:10],
                        industry_tags=tags,
                        event_summary=quote[:300],
                        evidence_quotes=(quote[:500],),
                        confidence="high",
                        processor="rules:pedaily-v3",
                        content_hash=article.content_hash,
                        phase="strategy_capital",
                        event_status="completed",
                    )
                )
            if _UPCOMING_PRODUCT_RELEASE.search(quote):
                output.append(
                    SemanticEvent(
                        source_id=channel.source_id,
                        source_article_id=article.index.source_article_id,
                        canonical_url=article.index.canonical_url,
                        company_mentions=mentions,
                        canonical_company=company,
                        event_type="technical_milestone",
                        event_date=article.index.published_at[:10],
                        industry_tags=tags,
                        event_summary=quote[:300],
                        evidence_quotes=(quote[:500],),
                        confidence="high",
                        processor="rules:pedaily-v3",
                        content_hash=article.content_hash,
                        phase="scale_delivery",
                        event_status="target",
                    )
                )
        return output

    @classmethod
    def _company_for_event(
        cls,
        article: CleanArticle,
        sentence: str,
        assertion: re.Match[str],
        last_company: str,
    ) -> tuple[str, tuple[str, ...]]:
        structured_company = canonical_company_name(
            cls.clean_text(
                str(article.structured_data.get("company") or "")
            )
        )
        structured_mentions = tuple(
            cls.clean_text(str(item))
            for item in article.structured_data.get("company_mentions", ())
            if cls.clean_text(str(item))
        )
        if cls._valid_company(structured_company) and (
            structured_company in sentence
            or any(alias in sentence for alias in structured_mentions)
            or sentence == article.index.title
        ):
            return (
                structured_company,
                structured_mentions or (structured_company,),
            )

        prefix = sentence[: assertion.start()]
        quoted = [
            cls.clean_text(match.group(1))
            for match in _QUOTED_COMPANY.finditer(prefix)
            if cls._valid_company(cls.clean_text(match.group(1)))
        ]
        if quoted:
            return quoted[-1], (quoted[-1],)
        legal = list(_LEGAL_COMPANY.finditer(prefix))
        if legal:
            company = canonical_company_name(
                cls.clean_text(legal[-1].group(1))
            )
            return company, (company,)
        title_company = canonical_company_name(
            cls._title_company(article.index.title)
        )
        if title_company and (
            title_company in sentence
            or sentence == article.index.title
        ):
            return title_company, (title_company,)
        if cls._valid_company(last_company) and last_company in sentence:
            return last_company, (last_company,)
        return "", ()

    @staticmethod
    def _body_paragraphs(body: object) -> tuple[str, ...]:
        output: list[str] = []
        for paragraph in body.css("p"):
            classes = set(
                str(paragraph.attrib.get("class") or "").split()
            )
            if classes & _NOISE_PARAGRAPH_CLASSES:
                continue
            text = re.sub(
                r"\s+",
                " ",
                paragraph.get_all_text(separator=" ", strip=True),
            ).strip()
            if text:
                output.append(text)
        return tuple(output)

    @classmethod
    def _author(
        cls,
        adaptive: AdaptiveSelector,
        index: SourceArticleIndex,
    ) -> str:
        detail = cls._first_text(adaptive.selector, "span#newsauthor")
        if detail and detail not in _PLACEHOLDER_AUTHORS:
            return detail
        listing = cls.clean_text(
            str(index.structured_data.get("listing_author") or "")
        )
        return "" if listing in _PLACEHOLDER_AUTHORS else listing

    @classmethod
    def _company_alias_relation(cls, body: str) -> tuple[str, str]:
        match = _ALIAS_RELATION.search(body)
        if not match:
            return "", ""
        legal = cls.clean_text(match.group("legal"))
        alias = cls.clean_text(match.group("alias"))
        if not cls._valid_company(legal) or not cls._valid_company(alias):
            return "", ""
        return legal, alias

    @classmethod
    def _title_company(cls, title: str) -> str:
        normalized = cls.clean_text(title)
        quoted = [
            cls.clean_text(match.group(1))
            for match in _QUOTED_COMPANY.finditer(normalized)
            if cls._valid_company(cls.clean_text(match.group(1)))
        ]
        if quoted:
            return quoted[-1]
        normalized = _LEADING_LABEL.sub("", normalized)
        if "丨" in normalized:
            remainder = normalized.rsplit("丨", 1)[-1].strip()
            if _TITLE_ASSERTION.search(remainder):
                normalized = remainder
        assertion = _TITLE_ASSERTION.search(normalized)
        if not assertion:
            return ""
        candidate = normalized[: assertion.start()].strip(
            " ，,：:｜|丨《》【】"
        )
        return candidate if cls._valid_company(candidate) else ""

    @staticmethod
    def _valid_company(candidate: str) -> bool:
        return bool(
            2 <= len(candidate) <= 60
            and not _NON_COMPANY.search(candidate)
            and not re.search(r"[，,：:；;！？?]", candidate)
        )

    @staticmethod
    def _parse_listing_time(
        value: str,
        context: AdapterContext,
    ) -> tuple[str, datetime]:
        if not _LISTING_TIME.fullmatch(value):
            raise ListingInvariantError(f"invalid Pedaily listing time {value!r}")
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone(timedelta(hours=8))
        )
        now = context.now
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if parsed > now.astimezone(parsed.tzinfo) + timedelta(minutes=5):
            raise ListingInvariantError(
                f"Pedaily listing time is in the future: {value}"
            )
        return parsed.isoformat(), parsed

    @staticmethod
    def _detail_published_at(adaptive: AdaptiveSelector) -> str:
        times = tuple(
            adaptive.selector.css("header.newsinfo time[datetime]")
        )
        if len(times) != 1:
            return ""
        value = str(times[0].attrib.get("datetime") or "").strip()
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return ""
        return value

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or (parsed.hostname or "").lower() != "news.pedaily.cn"
        ):
            return url
        return f"https://news.pedaily.cn{parsed.path}"

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
    def _channel_name(channel: SourceChannel) -> str:
        return (
            "vcpe-events"
            if channel.source_id == "pedaily-vcpe-events"
            else "investment-news"
        )


__all__ = ["PedailyAdapter"]
