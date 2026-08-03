"""Dedicated, fail-closed adapter for Jiqizhixin's public industry feed."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from ..base import (
    AdapterContext,
    AggregateAdapter,
    DetailFetchError,
    ListingInvariantError,
)
from ..entities import canonical_company_name, is_company_like
from ..industry_rules import IndustryRuleConfig, extract_media_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel
from scrapling import Selector


_CHINA = ZoneInfo("Asia/Shanghai")
_PAGE_SIZE = 12
_OVERLAP = timedelta(days=1)
_ARTICLE_PATH = re.compile(r"/articles/(?P<slug>20\d{2}-\d{2}-\d{2}(?:-\d+)?)")
_ACCESS_CONTROL = re.compile(
    r"请完成(?:安全|人机)验证|访问过于频繁|请求过于频繁|"
    r"Access Denied|Forbidden|Just a moment|challenge-platform|"
    r"\u673a\u5668\u4e4b\u5fc3[\u00b7\u30fb]\u6570\u636e\u670d\u52a1|"
    r"\u673a\u5668\u4e4b\u5fc3\u6570\u636e\u670d\u52a1\u5df2\u4e0a\u7ebf|"
    r"\u8fd8\u5728\u8d39\u52b2\u722c\u6570\u636e",
    re.I,
)
_BLOCK_TAGS = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "li", "blockquote", "figcaption"}
)
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "aside", "nav", "footer", "form"}
)
_SUBJECT_BEFORE_ACTION = re.compile(
    r"(?:^|[\s。！？；，])"
    r"(?P<company>[\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,50}?)"
    r"(?:正式)?(?:宣布|计划|拟|已|将|完成|获得|签署|达成|启动|实现)?"
    r"(?:任命|聘任|扩产|投产|建设|新建|开工|中标|获得.{0,12}订单|"
    r"签订.{0,12}合同|战略合作|联合研发|完成交付|通过验收|成立|落户|"
    r"获批|批准上市|临床试验|发布|推出|收购|并购|IPO|上线)"
)
_GENERIC_SUBJECT = re.compile(
    r"^(?:企业|公司|行业|市场|项目|团队|研究|报告|产业|技术|产品)$"
)
_COMMENTARY_TITLE = re.compile(
    r"评论|观点|观察|复盘|透视|研判|为什么|如何|意味着|拐点|泡沫|真相"
)


class _ArticleTextParser(HTMLParser):
    """Extract visible block text from the API's already-scoped article HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._block_depth = 0
        self._skip_depth = 0
        self._buffer: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if normalized in _BLOCK_TAGS:
            if self._block_depth == 0:
                self._buffer = []
            self._block_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or normalized not in _BLOCK_TAGS:
            return
        if self._block_depth:
            self._block_depth -= 1
        if self._block_depth == 0 and self._buffer:
            value = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
            if value:
                self.paragraphs.append(value)
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and self._block_depth and data.strip():
            self._buffer.append(data)


class JiqizhixinAdapter(AggregateAdapter):
    """Capture the complete one-day public window of industry articles."""

    adapter_id = "jiqizhixin"
    channels = (
        SourceChannel(
            source_id="jiqizhixin-industry-analysis",
            name="机器之心—产业资讯",
            url="https://www.jiqizhixin.com/industry",
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
            allowed_hosts=("www.jiqizhixin.com", "jiqizhixin.com"),
            allowed_path_patterns=(r"/articles/20\d{2}-\d{2}-\d{2}(?:-\d+)?",),
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
        self._reject_access_control(channel.source_id, html, listing=True)
        # The public listing occasionally becomes a bot/marketing page or a
        # materially different DOM.  Do not invoke Scrapling's fuzzy
        # relocation on this endpoint: a large page with a missing selector
        # can make relocation scan the whole tree and monopolize the single
        # server CPU.  An exact parse fails closed and lets the coordinator
        # record a source-level error for the next run.
        selector = Selector(
            html,
            url=channel.url,
            encoding="utf-8",
            adaptive=False,
        )
        containers = tuple(selector.css("div.u-block__body.js-u-item.is-active"))
        if len(containers) != 1:
            raise ListingInvariantError(
                f"{channel.source_id} listing selector failed closed"
            )
        items = tuple(
            containers[0].css(
                ":scope > article.article-item__container"
            )
        )
        if len(items) != _PAGE_SIZE:
            raise ListingInvariantError(
                f"{channel.source_id} listing item count {len(items)} "
                f"does not equal {_PAGE_SIZE}"
            )

        discovered_at = context.now.replace(microsecond=0).isoformat()
        source_now = context.now.astimezone(_CHINA)
        parsed: list[SourceArticleIndex] = []
        seen: set[str] = set()
        previous: datetime | None = None
        for position, item in enumerate(items, start=1):
            links = tuple(item.css("a.article-item__title"))
            times = tuple(item.css("time.js-time-ago[datetime]"))
            authors = tuple(item.css("div.article-item__author > a.article-item__name"))
            summaries = tuple(item.css("p.article-item__summary"))
            if len(links) != 1 or len(times) != 1 or len(authors) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} cardinality failed"
                )
            if len(summaries) > 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} has duplicate summaries"
                )

            link = links[0]
            canonical_url = self._canonical_url(
                urljoin(channel.url, str(link.attrib.get("href") or ""))
            )
            article_id = self._article_id(canonical_url)
            if not article_id or article_id in seen:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid/duplicate article URL {canonical_url}"
                )
            title = self.clean_text(link.get_all_text(separator=" ", strip=True))
            time_label = str(times[0].attrib.get("datetime") or "").strip()
            published = self._parse_listing_time(time_label)
            if published is None:
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} has invalid date"
                )
            if published > source_now:
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} is future dated"
                )
            if previous is not None and published > previous:
                raise ListingInvariantError(
                    f"{channel.source_id} listing is not newest-first at {position}"
                )
            previous = published

            author = self.clean_text(
                authors[0].get_all_text(separator=" ", strip=True)
            )
            if not author:
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} has no author"
                )
            summary = (
                self.clean_text(summaries[0].get_all_text(separator=" ", strip=True))
                if summaries
                else ""
            )
            published_at = published.isoformat()
            structured = {
                "author": author,
                "listing_datetime": time_label,
                "source_section": "industry",
                "listing_position_on_page": position,
            }
            parsed.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="industry-analysis",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=position,
                    content_hash=self.stable_hash(
                        "\n".join((canonical_url, title, summary, published_at, author))
                    ),
                    discovery_method="exact",
                    summary=summary,
                    structured_data=structured,
                )
            )
            seen.add(article_id)

        if context.capture_full_visible_window:
            self.validate_listing(channel, parsed)
            return parsed

        cutoff = source_now - _OVERLAP
        if self._index_time(parsed[-1]) >= cutoff:
            raise ListingInvariantError(
                f"{channel.source_id} first public page does not close the "
                "one-day overlap window"
            )
        output: list[SourceArticleIndex] = []
        for item in parsed:
            if self._index_time(item) < cutoff:
                continue
            output.append(
                SourceArticleIndex(
                    **{**item.to_dict(), "listing_position": len(output) + 1}
                )
            )
        self.validate_listing(channel, output)
        return output

    def fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        context: AdapterContext,
    ) -> bytes:
        del channel
        endpoint = (
            "https://www.jiqizhixin.com/api/article_library/articles/"
            f"{index.source_article_id}"
        )
        decision = {
            "source_article_id": index.source_article_id,
            "path": "api:article_library/articles/:slug",
            "endpoint": endpoint,
            "outcome": "requested",
        }
        context.decision_state[index.source_article_id] = decision
        if context.record_decision is not None:
            context.record_decision(index.source_article_id, decision)
        return context.fetch(endpoint)

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        del context
        self._reject_access_control(channel.source_id, html, listing=False)
        try:
            document = json.loads(html.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DetailFetchError(
                f"{channel.source_id} detail is not valid UTF-8 JSON for "
                f"{index.source_article_id}"
            ) from exc
        if not isinstance(document, dict):
            raise DetailFetchError(
                f"{channel.source_id} detail root is not an object for "
                f"{index.source_article_id}"
            )

        title = self.clean_text(str(document.get("title") or ""))
        if not self._titles_match(index.title, title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        published = self._parse_detail_time(str(document.get("published_at") or ""))
        if published is None or published != self._index_time(index):
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )

        author_node = document.get("author")
        if not isinstance(author_node, dict):
            raise DetailFetchError(
                f"{channel.source_id} detail author missing for "
                f"{index.source_article_id}"
            )
        author = self.clean_text(str(author_node.get("name") or ""))
        author_id = self.clean_text(str(author_node.get("id") or ""))
        if not author or not author_id:
            raise DetailFetchError(
                f"{channel.source_id} detail author identity incomplete for "
                f"{index.source_article_id}"
            )

        content = document.get("content")
        if not isinstance(content, str):
            raise DetailFetchError(
                f"{channel.source_id} detail content missing for "
                f"{index.source_article_id}"
            )
        body, paragraph_count = self._clean_api_content(content)
        if paragraph_count < 3 or not 300 <= len(body) <= 100_000:
            raise DetailFetchError(
                f"{channel.source_id} detail body failed length/structure "
                f"invariants for {index.source_article_id}"
            )

        seo = document.get("seo")
        keyword_values = seo.get("keywords", []) if isinstance(seo, dict) else []
        if not isinstance(keyword_values, list):
            raise DetailFetchError(
                f"{channel.source_id} detail keywords are not a list for "
                f"{index.source_article_id}"
            )
        tags = tuple(
            dict.fromkeys(
                self.clean_text(str(value))
                for value in keyword_values
                if self.clean_text(str(value))
            )
        )
        document_type = ""
        if len(body) >= 800 and _COMMENTARY_TITLE.search(title):
            document_type = "commentary"
        elif len(body) >= 1_200 and paragraph_count >= 8:
            document_type = "long_feature"
        structured = {
            **index.structured_data,
            "author": author,
            "author_id": author_id,
            "api_endpoint": "api/article_library/articles/:slug",
            "detail_published_at": published.isoformat(),
            "paragraph_count": paragraph_count,
            "source_section": "industry",
        }
        if document_type:
            structured["document_type"] = document_type
        digest = sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            tags=tags,
            structured_data=structured,
            extraction_method="api-exact",
            evidence_locators={
                "title": "api:title",
                "published_at": "api:published_at",
                "body": "api:content visible block text",
                "author": "api:author.name+author.id",
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
                processor="rules:jiqizhixin-v1",
                company_resolver=self._company_for_event,
            ),
            funding_processor="rules:jiqizhixin-funding-v1",
        )

    @classmethod
    def _company_for_event(
        cls,
        article: CleanArticle,
        sentence: str,
        event_type: str,
    ) -> tuple[str, tuple[str, ...]]:
        del article, event_type
        candidates = [
            canonical_company_name(match.group("company"))
            for match in _SUBJECT_BEFORE_ACTION.finditer(sentence)
        ]
        for candidate in reversed(candidates):
            if is_company_like(candidate) and not _GENERIC_SUBJECT.fullmatch(candidate):
                return candidate, (candidate,)
        return "", ()

    @staticmethod
    def _reject_access_control(
        source_id: str,
        payload: bytes,
        *,
        listing: bool,
    ) -> None:
        text = payload.decode("utf-8", errors="replace")
        if _ACCESS_CONTROL.search(text):
            kind = "listing" if listing else "detail"
            error = ListingInvariantError if listing else DetailFetchError
            raise error(f"{source_id} {kind} access control detected; no bypass")

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if host not in {"jiqizhixin.com", "www.jiqizhixin.com"}:
            return value
        return f"https://www.jiqizhixin.com{parsed.path.rstrip('/')}"

    @staticmethod
    def _article_id(value: str) -> str:
        match = _ARTICLE_PATH.fullmatch(urlparse(value).path)
        return match.group("slug") if match else ""

    @staticmethod
    def _parse_listing_time(value: str) -> datetime | None:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            return None
        if parsed.utcoffset() != timedelta(hours=8):
            return None
        return parsed.astimezone(_CHINA)

    @staticmethod
    def _parse_detail_time(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=_CHINA
            )
        except ValueError:
            return None

    @staticmethod
    def _index_time(index: SourceArticleIndex) -> datetime:
        return datetime.fromisoformat(index.published_at).astimezone(_CHINA)

    @classmethod
    def _clean_api_content(cls, value: str) -> tuple[str, int]:
        parser = _ArticleTextParser()
        try:
            parser.feed(value)
            parser.close()
        except (ValueError, AssertionError) as exc:
            raise DetailFetchError("jiqizhixin malformed article HTML") from exc
        paragraphs = tuple(
            cls.clean_text(paragraph)
            for paragraph in parser.paragraphs
            if cls.clean_text(paragraph)
        )
        return "\n".join(paragraphs), len(paragraphs)

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s\u200b]+", "", expected)
        right = re.sub(r"[\s\u200b]+", "", actual)
        return bool(left and right and (left == right or left in right or right in left))


__all__ = ["JiqizhixinAdapter"]
