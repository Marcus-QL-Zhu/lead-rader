"""Dedicated adapters for Cyzone's public financing and latest-news lists."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from urllib.parse import urljoin, urlparse

from ..adaptive import AdaptiveSelector
from ..base import (
    AdapterContext,
    AggregateAdapter,
    DetailFetchError,
    ListingInvariantError,
)
from ..entities import canonical_company_name, is_company_like
from ..finance_rules import FundingRuleConfig, extract_funding_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_ARTICLE_PATH = re.compile(r"/article/(\d+)\.html")
_THUMB_DATE = re.compile(r"/(?P<year>20\d{2})/(?P<month>\d{2})(?P<day>\d{2})/")
_FINANCING_TITLE_COMPANY = re.compile(
    r"^融资\s*[丨｜|]\s*(?:[^，,:：]{0,24}?企业)?"
    r"(?P<company>[\u4e00-\u9fffA-Za-z0-9·（）()\- ]{2,40}?)"
    r"(?:完成|获得|获|斩获|宣布|官宣)"
)
_GENERIC_TAGS = frozenset(
    {
        "融资",
        "快鲤鱼",
        "创业邦",
        "原创",
        "科技",
        "创投",
        "投资",
        "产业",
    }
)
_DETAIL_CTA = re.compile(
    r"(?:查看更多项目信息，?请前往[「“]?睿兽分析[」”]?。?|"
    r"本文为创业邦原创，?未经授权不得转载。?).*$",
    re.S,
)
_EXPLICIT_DEVELOPER_FUNDING_SUBJECT = re.compile(
    r"(?:开发商|研发商)"
    r"(?P<company>[\u4e00-\u9fffA-Za-z0-9·（）()\- ]{2,40}?)"
    r"(?:也|将|即将|顺势)"
)


class CyzoneAdapter(AggregateAdapter):
    """Enumerate and parse the two public Cyzone article feeds."""

    adapter_id = "cyzone"
    channels = (
        SourceChannel(
            source_id="cyzone-financing",
            name="创业邦—融资",
            url="https://capital.cyzone.cn/",
            source_grade="B",
            event_prior=("funding",),
            allowed_hosts=("www.cyzone.cn",),
            allowed_path_patterns=(r"/article/\d+\.html",),
        ),
        SourceChannel(
            source_id="cyzone-latest",
            name="创业邦—最新资讯",
            url="https://www.cyzone.cn/",
            source_grade="B",
            event_prior=("funding",),
            allowed_hosts=("www.cyzone.cn",),
            allowed_path_patterns=(r"/article/\d+\.html",),
        ),
    )
    minimum_listing_count = 10
    maximum_listing_count = 100

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
        if channel.source_id == "cyzone-financing":
            selector = "div.article-item"
            minimum_count = 10
        elif channel.source_id == "cyzone-latest":
            selector = "div#pane-recommend div.article-item[data-id]"
            minimum_count = 20
        else:
            raise ListingInvariantError(
                f"unsupported Cyzone channel {channel.source_id}"
            )
        selection = adaptive.css(
            selector,
            identifier=f"{channel.source_id}:listing-item",
            minimum_count=minimum_count,
            maximum_count=self.maximum_listing_count,
        )
        if not selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} article-item selector failed"
            )

        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        discovered_at = context.now.replace(microsecond=0).isoformat()
        for position, item in enumerate(selection.elements, start=1):
            links = tuple(item.css("a.item-title"))
            if len(links) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} has {len(links)} title links"
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
            if article_id in seen:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate article id {article_id}"
                )

            title = self.clean_text(link.get_all_text(separator=" ", strip=True))
            summary = self._first_text(item, "div.item-desc")
            tags = tuple(
                dict.fromkeys(
                    self.clean_text(node.get_all_text(separator=" ", strip=True))
                    for node in item.css("div.tags a[rel='tag']")
                    if self.clean_text(node.get_all_text(separator=" ", strip=True))
                )
            )
            time_label = self._first_text(item, "span.time")
            thumb_url = self._thumbnail_url(item, channel)
            published_at = (
                self._date_from_thumbnail(thumb_url)
                if channel.source_id == "cyzone-financing"
                else self._parse_listing_time(time_label, context)
            )
            if not published_at:
                raise ListingInvariantError(
                    f"{channel.source_id} item {article_id} has no valid date"
                )
            if self._date_value(published_at) > context.now.date():
                raise ListingInvariantError(
                    f"{channel.source_id} item {article_id} is future dated"
                )

            company = self._company_from_listing(title, tags)
            structured = {
                "time_label": time_label,
                "tags": tags,
                "thumbnail_url": thumb_url,
                "company": company,
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
                    channel=(
                        "financing"
                        if channel.source_id == "cyzone-financing"
                        else "latest"
                    ),
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=channel.url,
                    listing_position=position,
                    content_hash=content_hash,
                    discovery_method=selection.method,
                    summary=summary,
                    structured_data=structured,
                )
            )
            seen.add(article_id)
        self.validate_listing(channel, output)
        return output

    def fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        context: AdapterContext,
    ) -> bytes:
        """Prefer Cyzone's public application API over brittle article DOM."""

        del channel
        decision: dict[str, str] = {
            "source_article_id": index.source_article_id,
            "primary_path": "api:app_content/show",
            "fallback_path": "html:canonical_url",
        }
        if context.post_json is not None:
            try:
                payload = context.post_json(
                    "https://api1.cyzone.cn/v2/content/app_content/show",
                    {"content_id": int(index.source_article_id)},
                )
                decoded = json.loads(payload.decode("utf-8"))
                data = decoded.get("data") if isinstance(decoded, dict) else None
                if not isinstance(data, dict):
                    raise ValueError("API data is not an object")
                returned_id = str(data.get("content_id") or "").strip()
                if returned_id != index.source_article_id:
                    raise ValueError(
                        f"API content_id mismatch: {returned_id!r}"
                    )
                if not str(data.get("content") or "").strip():
                    raise ValueError("API content is empty")
                decision["outcome"] = "api_accepted"
                context.decision_state[index.source_article_id] = decision
                if context.record_decision is not None:
                    context.record_decision(index.source_article_id, decision)
                return payload
            except (OSError, ValueError, TypeError, UnicodeDecodeError) as exc:
                decision["outcome"] = "html_fallback"
                decision["api_failure"] = f"{type(exc).__name__}: {exc}"
        else:
            decision["outcome"] = "html_only_no_post_transport"
        context.decision_state[index.source_article_id] = decision
        if context.record_decision is not None:
            context.record_decision(index.source_article_id, decision)
        return context.fetch(index.canonical_url)

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        api_article = self._parse_api_detail(channel, index, html, context)
        if api_article is not None:
            return api_article

        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        selected = adaptive.css(
            "div.g-art-content",
            identifier=f"{channel.source_id}:article-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not selected.elements:
            raise DetailFetchError(
                f"{channel.source_id} body selector failed for "
                f"{index.source_article_id}"
            )
        body = self.clean_text(
            selected.elements[0].get_all_text(separator=" ", strip=True)
        )
        body = self.clean_text(_DETAIL_CTA.sub("", body))
        if len(body) < 80:
            raise DetailFetchError(
                f"{channel.source_id} body too short for {index.source_article_id}"
            )

        title = self._first_text(adaptive.selector, "h1.art-title")
        if not self._titles_match(index.title, title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        detail_date = self._detail_date(adaptive)
        if detail_date and self._date_value(detail_date) > context.now.date():
            detail_date = ""

        author = self._first_text(
            adaptive.selector,
            "div.art-help div.author-date a.author",
        )
        tags = tuple(
            dict.fromkeys(
                self.clean_text(node.get_all_text(separator=" ", strip=True))
                for node in adaptive.selector.css("div.tag-group a")
                if self.clean_text(node.get_all_text(separator=" ", strip=True))
            )
        )
        structured = dict(index.structured_data)
        structured.update(
            {
                "detail_published_at": detail_date,
                "detail_tags": tags,
                "acquisition_decision": context.decision_state.get(
                    index.source_article_id, {}
                ),
                "published_at_provenance": (
                    "detail_html" if detail_date else "listing"
                ),
            }
        )
        if detail_date and detail_date != index.published_at[:10]:
            structured["date_ambiguity"] = (
                f"listing={index.published_at[:10]};detail={detail_date}"
            )
        digest = sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            tags=tags,
            structured_data=structured,
            extraction_method=selected.method,
            adaptive_similarity=selected.similarity_threshold,
            evidence_locators={
                "title": "detail:h1.art-title",
                "published_at": (
                    "detail:div.art-help div.author-date"
                    if detail_date
                    else "listing:time-or-thumbnail"
                ),
                "body": "detail:div.g-art-content",
                "author": "detail:div.art-help div.author-date a.author",
                "tags": "detail:div.tag-group a",
                "company": "listing:tags-or-title",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def _parse_api_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        payload: bytes,
        context: AdapterContext,
    ) -> CleanArticle | None:
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        data = decoded.get("data") if isinstance(decoded, dict) else None
        if not isinstance(data, dict):
            return None
        returned_id = str(data.get("content_id") or "").strip()
        if returned_id != index.source_article_id:
            raise DetailFetchError(
                f"{channel.source_id} API content_id mismatch for "
                f"{index.source_article_id}: {returned_id!r}"
            )
        content = str(data.get("content") or "").strip()
        if not content:
            return None
        adaptive = AdaptiveSelector(
            content,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        body = self.clean_text(
            adaptive.selector.get_all_text(separator=" ", strip=True)
        )
        body = self.clean_text(_DETAIL_CTA.sub("", body))
        if len(body) < 80:
            raise DetailFetchError(
                f"{channel.source_id} API body too short for {index.source_article_id}"
            )
        title = self.clean_text(str(data.get("title") or index.title))
        if not self._titles_match(index.title, title):
            raise DetailFetchError(
                f"{channel.source_id} API title mismatch for {index.source_article_id}"
            )
        api_date = self._api_date(data.get("published_at"))
        if api_date and self._date_value(api_date) > context.now.date():
            raise DetailFetchError(
                f"{channel.source_id} API detail is future dated for "
                f"{index.source_article_id}: {api_date}"
            )
        resolved_index = replace(index, published_at=api_date) if api_date else index
        raw_tags = data.get("tags")
        tags = tuple(
            dict.fromkeys(
                value.strip()
                for value in (
                    str(raw_tags or "").split(",")
                    if not isinstance(raw_tags, list)
                    else map(str, raw_tags)
                )
                if value.strip()
            )
        )
        structured = dict(index.structured_data)
        structured.update(
            {
                "api_content_id": str(data.get("content_id") or index.source_article_id),
                "api_published_at": api_date,
                "published_at_provenance": "api:published_at",
                "api_category": str(data.get("category") or ""),
                "acquisition_decision": context.decision_state.get(
                    index.source_article_id, {}
                ),
            }
        )
        if api_date and api_date != index.published_at[:10]:
            structured["date_ambiguity"] = (
                f"listing={index.published_at[:10]};api={api_date}"
            )
        digest = sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=resolved_index,
            clean_body=body,
            author=self.clean_text(
                str(data.get("author_name") or data.get("author") or "")
            ),
            tags=tags,
            structured_data=structured,
            extraction_method="api:app_content/show",
            evidence_locators={
                "title": "api:data.title",
                "published_at": "api:data.published_at",
                "body": "api:data.content",
                "author": "api:data.author_name|author",
                "tags": "api:data.tags",
                "company": "listing:tags-or-title",
            },
            fetch_status="structured_complete",
            content_hash=digest,
        )

    @classmethod
    def _api_date(cls, value: object) -> str:
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and value.strip().isdigit()
        ):
            timestamp = int(value)
            if timestamp > 10_000_000_000:
                timestamp //= 1000
            return datetime.fromtimestamp(
                timestamp,
                tz=timezone(timedelta(hours=8)),
            ).date().isoformat()
        text = str(value or "").strip()
        match = re.search(r"20\d{2}-\d{2}-\d{2}", text)
        return match.group(0) if match else ""

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        events = extract_funding_events(
            channel,
            article,
            config=FundingRuleConfig(processor="rules:cyzone-v1"),
        )
        corrected: list[SemanticEvent] = []
        for event in events:
            quote = event.evidence_quotes[0] if event.evidence_quotes else ""
            if event.canonical_company in quote:
                corrected.append(event)
                continue
            match = _EXPLICIT_DEVELOPER_FUNDING_SUBJECT.search(quote)
            company = canonical_company_name(
                match.group("company") if match else ""
            )
            if company and is_company_like(company):
                corrected.append(
                    replace(
                        event,
                        canonical_company=company,
                        company_mentions=(company,),
                    )
                )
            else:
                corrected.append(event)
        return corrected

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"cyzone.cn", "www.cyzone.cn"}:
            return url
        return f"https://www.cyzone.cn{parsed.path.rstrip('/')}"

    @classmethod
    def _thumbnail_url(cls, item: object, channel: SourceChannel) -> str:
        if channel.source_id != "cyzone-financing":
            return ""
        images = tuple(item.css("a.pic-a img"))
        if images:
            return cls._absolute_media_url(
                str(images[0].attrib.get("src") or ""),
                channel.url,
            )
        pictures = tuple(item.css("a.pic-a"))
        if not pictures:
            return ""
        style = str(pictures[0].attrib.get("style") or "")
        match = re.search(r"url\(['\"]?([^'\")]+)", style)
        return cls._absolute_media_url(match.group(1), channel.url) if match else ""

    @staticmethod
    def _absolute_media_url(value: str, base_url: str) -> str:
        normalized = value.strip()
        if normalized.startswith("//"):
            return f"https:{normalized}"
        return urljoin(base_url, normalized)

    @staticmethod
    def _date_from_thumbnail(url: str) -> str:
        match = _THUMB_DATE.search(url)
        if not match:
            return ""
        value = f"{match.group('year')}-{match.group('month')}-{match.group('day')}"
        try:
            return datetime.fromisoformat(value).date().isoformat()
        except ValueError:
            return ""

    @staticmethod
    def _parse_listing_time(value: str, context: AdapterContext) -> str:
        normalized = re.sub(r"\s+", " ", value).strip()
        full = re.fullmatch(r"(20\d{2})-(\d{2})-(\d{2})", normalized)
        if full:
            try:
                return datetime.fromisoformat(normalized).date().isoformat()
            except ValueError:
                return ""
        month_day = re.fullmatch(r"(\d{2})-(\d{2})", normalized)
        if month_day:
            try:
                candidate = context.now.date().replace(
                    month=int(month_day.group(1)),
                    day=int(month_day.group(2)),
                )
            except ValueError:
                return ""
            if candidate > context.now.date() + timedelta(days=1):
                try:
                    candidate = candidate.replace(year=candidate.year - 1)
                except ValueError:
                    return ""
            return candidate.isoformat()
        today = re.fullmatch(r"今天(?:\s+\d{1,2}:\d{2})?", normalized)
        if today:
            return context.now.date().isoformat()
        yesterday = re.fullmatch(r"昨天(?:\s+\d{1,2}:\d{2})?", normalized)
        if yesterday:
            return (context.now - timedelta(days=1)).date().isoformat()
        if re.fullmatch(r"\d+\s*(?:分钟前|小时前)", normalized):
            return context.now.date().isoformat()
        return ""

    @staticmethod
    def _detail_date(adaptive: AdaptiveSelector) -> str:
        containers = tuple(adaptive.selector.css("div.art-help div.author-date"))
        if not containers:
            return ""
        text = re.sub(
            r"\s+",
            " ",
            containers[0].get_all_text(separator=" ", strip=True),
        )
        match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
        if not match:
            return ""
        try:
            return datetime.fromisoformat(match.group(1)).date().isoformat()
        except ValueError:
            return ""

    @staticmethod
    def _date_value(value: str):
        return datetime.fromisoformat(value[:10]).date()

    @classmethod
    def _company_from_listing(
        cls,
        title: str,
        tags: tuple[str, ...],
    ) -> str:
        title_match = _FINANCING_TITLE_COMPANY.search(title)
        if title_match:
            candidate = title_match.group("company").strip()
            if 2 <= len(candidate) <= 40:
                return candidate
        if "融资" not in tags:
            return ""
        for tag in tags:
            candidate = tag.strip()
            if candidate not in _GENERIC_TAGS and 2 <= len(candidate) <= 40:
                return candidate
        return ""

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"\s+", "", expected)
        right = re.sub(r"\s+", "", actual)
        return bool(
            left and right and (left == right or left in right or right in left)
        )

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


__all__ = ["CyzoneAdapter"]
