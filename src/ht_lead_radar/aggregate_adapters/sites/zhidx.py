"""Dedicated, fail-closed adapter for Zhidx's public financing tag."""

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


_PAGE_SIZE = 10
_OVERLAP_DAYS = 2
_PUBLISHED_FORMAT = "%Y/%m/%d %H:%M"
_DETAIL_DATE_FORMAT = "%Y/%m/%d"
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问过于频繁|Access Denied|403 Forbidden|"
    r"captcha-container|TTGCaptcha)",
    re.I,
)
_LEGAL_COMPANY = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·（）()]{4,60}"
    r"(?:有限责任公司|股份有限公司|有限公司|集团))"
)
_LEGAL_ALIAS = re.compile(
    r"(?P<legal>[\u4e00-\u9fffA-Za-z0-9·（）()]{4,60}"
    r"(?:有限责任公司|股份有限公司|有限公司|集团))"
    r"[（(](?:以下简称|简称)[“\"「]?"
    r"(?P<alias>[^”\"」）)]{2,40})[”\"」]?[）)]"
)
_DESCRIPTIVE_COMPANY = re.compile(
    r"(?:解决方案提供商|设备公司|机器人公司|人工智能公司|AI公司|创企)"
    r"\s*([\u4e00-\u9fffA-Za-z0-9·\-]{2,36}?"
    r"(?:半导体|机器人|科技|智能|AI))"
    r"\s*(?=在|发文|宣布|完成|已|于)"
)
_EVENT_SPLIT = re.compile(
    r"任命|获任|出任|履新|扩产|投产|中标|签订|战略合作|"
    r"发布|收购|并购|融资|启动|IPO|成立|落户|完成交付|通过验收|"
    r"首批交付|实现(?:规模化)?量产"
)
_GENERIC_COMPANY = re.compile(
    r"^(?:小巨人|公司|企业|创企|项目|团队|芯东西|智东西)$|"
    r"^(?:投资方|该公司|该企业|其).*(?:公司|集团)$|"
    r"(?:融资|报告|行业|市场|产品|技术|政策|标准|指南|名单)$"
)
_BODY_NOISE = re.compile(
    r"^(?:▲|责任编辑[：:]|相关阅读[：:]?|更多推荐[：:]?|"
    r"点击(?:图片|链接)|本文(?:来源|作者)[：:]|未经许可)"
)


class ZhidxAdapter(AggregateAdapter):
    """Enumerate the closed public overlap on Zhidx's financing tag."""

    adapter_id = "zhidx"
    channels = (
        SourceChannel(
            source_id="zhidx-financing",
            name="智东西—融资与产业",
            url="https://zhidx.com/p/tag/%E8%9E%8D%E8%B5%84",
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
            allowed_hosts=("zhidx.com", "www.zhidx.com"),
            allowed_path_patterns=(r"/p/\d+\.html",),
        ),
    )
    minimum_listing_count = 0
    maximum_listing_count = _PAGE_SIZE

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
        selection = adaptive.css(
            "ul.info-list > li",
            identifier=f"{channel.source_id}:listing-item",
            minimum_count=_PAGE_SIZE,
            maximum_count=_PAGE_SIZE,
        )
        if not selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} listing selector failed closed"
            )

        discovered_at = context.now.replace(microsecond=0).isoformat()
        parsed_items: list[SourceArticleIndex] = []
        seen_ids: set[str] = set()
        previous_published: datetime | None = None
        for page_position, item in enumerate(selection.elements, start=1):
            links = tuple(item.css("div.tag-info-left-title > a"))
            dates = tuple(item.css("div.iril-related-time"))
            if len(links) != 1 or len(dates) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {page_position} title/date "
                    "cardinality failed"
                )
            link = links[0]
            title = self.clean_text(
                link.get_all_text(separator=" ", strip=True)
            )
            canonical_url = self._canonical_url(
                urljoin(channel.url, str(link.attrib.get("href") or ""))
            )
            article_id = self._article_id(canonical_url)
            if not article_id or article_id in seen_ids:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid/duplicate article URL "
                    f"{canonical_url}"
                )
            time_label = self.clean_text(
                dates[0].get_all_text(separator=" ", strip=True)
            )
            published = self._parse_published(time_label)
            if published is None:
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} has invalid date"
                )
            if published > self._source_now(context.now):
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} is future dated"
                )
            if previous_published is not None and published > previous_published:
                raise ListingInvariantError(
                    f"{channel.source_id} listing is not newest-first at "
                    f"position {page_position}"
                )
            previous_published = published
            published_at = published.isoformat()
            structured = {
                "tag": "融资",
                "page": 1,
                "page_position": page_position,
                "time_label": time_label,
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
            parsed_items.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="financing-tag",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=page_position,
                    content_hash=content_hash,
                    discovery_method=selection.method,
                    structured_data=structured,
                )
            )
            seen_ids.add(article_id)

        cutoff = self._source_now(context.now).date() - timedelta(
            days=_OVERLAP_DAYS
        )
        if not any(self._published_date(item) < cutoff for item in parsed_items):
            raise ListingInvariantError(
                f"{channel.source_id} first public page does not close the "
                f"{_OVERLAP_DAYS}-day overlap window"
            )
        output: list[SourceArticleIndex] = []
        for item in parsed_items:
            if self._published_date(item) < cutoff:
                continue
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
            "div.post-title",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        body_selection = adaptive.css(
            "div.post-content",
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
        body = self._clean_body(body_selection.elements[0])
        if len(body) < 80:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for "
                f"{index.source_article_id}"
            )

        detail_date = self._first_text(
            adaptive.selector,
            "div.post-related span.time",
        )
        parsed_detail_date = self._parse_detail_date(detail_date)
        if parsed_detail_date is None:
            raise DetailFetchError(
                f"{channel.source_id} detail date missing for "
                f"{index.source_article_id}"
            )
        if parsed_detail_date.isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )
        if parsed_detail_date > self._source_now(context.now).date():
            raise DetailFetchError(
                f"{channel.source_id} future-dated detail "
                f"{index.source_article_id}"
            )

        author = self._first_text(
            adaptive.selector,
            "div.author-info div.author-name > a",
        )
        tags = tuple(
            dict.fromkeys(
                self.clean_text(
                    element.get_all_text(separator=" ", strip=True)
                )
                for element in adaptive.selector.css("div.post-tag > a")
                if self.clean_text(
                    element.get_all_text(separator=" ", strip=True)
                )
            )
        )
        company, company_mentions = self._article_company(index.title, body)
        structured = dict(index.structured_data)
        structured.update(
            {
                "detail_published_at": parsed_detail_date.isoformat(),
                "company": company,
                "company_mentions": company_mentions,
            }
        )
        method = (
            "adaptive"
            if "adaptive"
            in {title_selection.method, body_selection.method}
            else "exact"
        )
        digest = sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            tags=tags,
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={
                "title": "detail:div.post-title",
                "published_at": "detail:div.post-related span.time",
                "body": "detail:div.post-content",
                "author": "detail:div.author-info div.author-name>a",
                "tags": "detail:div.post-tag>a",
                "company": "detail:article body explicit company mention",
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
            config=IndustryRuleConfig(
                processor="rules:zhidx-v1",
                company_resolver=self._resolve_company,
            ),
            funding_processor="rules:zhidx-funding-v1",
        )
        return [
            event for event in events
            if self._event_is_current_company_fact(event)
        ]

    @staticmethod
    def _event_is_current_company_fact(event: SemanticEvent) -> bool:
        quote = event.evidence_quotes[0] if event.evidence_quotes else ""
        if (
            re.search(r"从20\d{2}年", quote)
            and event.event_date[:4] not in quote
        ) or re.search(r"总部位于|主要从事|成立于", quote):
            return False
        return bool(
            event.canonical_company in quote
            or re.search(r"该公司|该企业|该机构|其", quote)
        )

    @classmethod
    def _resolve_company(
        cls,
        article: CleanArticle,
        sentence: str,
        _event_type: str,
    ) -> tuple[str, tuple[str, ...]]:
        primary = str(article.structured_data.get("company") or "").strip()
        mentions = tuple(
            str(item).strip()
            for item in article.structured_data.get("company_mentions") or ()
            if str(item).strip()
        )
        if primary and re.search(r"该公司|该企业|该机构|其", sentence):
            return primary, mentions or (primary,)
        explicit = cls._company_from_text(sentence)
        if explicit:
            if explicit in mentions and primary:
                return primary, mentions
            return explicit, (explicit,)
        if primary:
            return primary, mentions or (primary,)
        fallback = cls._company_from_title(article.index.title)
        return (fallback, (fallback,)) if fallback else ("", ())

    @classmethod
    def _article_company(
        cls,
        title: str,
        body: str,
    ) -> tuple[str, tuple[str, ...]]:
        title_company = cls._company_from_title(title)
        if title_company:
            return title_company, (title_company,)
        relation = _LEGAL_ALIAS.search(body)
        if relation:
            legal = relation.group("legal").strip()
            alias = relation.group("alias").strip()
            if cls._valid_company(alias):
                return alias, tuple(dict.fromkeys((alias, legal)))
            return legal, (legal,)
        descriptive = _DESCRIPTIVE_COMPANY.search(body)
        if descriptive:
            company = descriptive.group(1).strip()
            if cls._valid_company(company):
                return company, (company,)
        legal = _LEGAL_COMPANY.search(body)
        if legal and cls._valid_company(legal.group(1).strip()):
            company = legal.group(1).strip()
            return company, (company,)
        company = cls._company_from_title(title)
        return (company, (company,)) if company else ("", ())

    @classmethod
    def _company_from_text(cls, value: str) -> str:
        descriptive = _DESCRIPTIVE_COMPANY.search(value)
        if descriptive and cls._valid_company(descriptive.group(1).strip()):
            return descriptive.group(1).strip()
        legal = _LEGAL_COMPANY.search(value)
        if legal and cls._valid_company(legal.group(1).strip()):
            return legal.group(1).strip()
        return cls._company_from_title(value)

    @classmethod
    def _company_from_title(cls, value: str) -> str:
        prefix = _EVENT_SPLIT.split(value, maxsplit=1)[0]
        prefix = re.split(r"[。！？；：:,，]", prefix)[-1]
        prefix = re.sub(
            r"^(?:今日|近日|日前|据悉|消息称|公告显示)\s*",
            "",
            prefix,
        ).strip(" “「『【】」』\"")
        return prefix if cls._valid_company(prefix) else ""

    @staticmethod
    def _valid_company(value: str) -> bool:
        return bool(
            2 <= len(value) <= 50
            and not _GENERIC_COMPANY.search(value)
            and not re.search(r"[\s“”「」『』【】]", value)
        )

    @classmethod
    def _clean_body(cls, element: object) -> str:
        blocks: list[str] = []
        seen: set[str] = set()
        for block in element.xpath(
            ".//p | .//h2 | .//h3 | .//blockquote | .//li"
        ):
            text = cls.clean_text(
                block.get_all_text(separator=" ", strip=True)
            )
            if not text or text in seen or _BODY_NOISE.search(text):
                continue
            seen.add(text)
            blocks.append(text)
        if not blocks:
            return cls.clean_text(
                element.get_all_text(separator=" ", strip=True)
            )
        return cls.clean_text(" ".join(blocks))

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() not in {
            "zhidx.com",
            "www.zhidx.com",
        }:
            return url
        match = re.fullmatch(r"/p/(\d+)\.html/?", parsed.path)
        if not match:
            return url
        return f"https://zhidx.com/p/{match.group(1)}.html"

    @staticmethod
    def _article_id(url: str) -> str:
        match = re.fullmatch(r"/p/(\d+)\.html", urlparse(url).path)
        return match.group(1) if match else ""

    @staticmethod
    def _parse_published(value: str) -> datetime | None:
        try:
            parsed = datetime.strptime(value.strip(), _PUBLISHED_FORMAT)
        except ValueError:
            return None
        return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))

    @staticmethod
    def _parse_detail_date(value: str):
        try:
            return datetime.strptime(
                value.strip(),
                _DETAIL_DATE_FORMAT,
            ).date()
        except ValueError:
            return None

    @staticmethod
    def _source_now(now: datetime) -> datetime:
        if now.tzinfo is None:
            return now.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return now.astimezone(ZoneInfo("Asia/Shanghai"))

    @staticmethod
    def _published_date(item: SourceArticleIndex):
        return datetime.fromisoformat(item.published_at).date()

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
    def _reject_interstitial(
        source_id: str,
        html: bytes,
        *,
        listing: bool,
    ) -> None:
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


__all__ = ["ZhidxAdapter"]
