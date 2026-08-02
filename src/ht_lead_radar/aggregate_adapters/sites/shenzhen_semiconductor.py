"""Fail-closed adapter for Shenzhen Semiconductor Association public news."""

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
from ..models import CleanArticle, SourceArticleIndex, SourceChannel

_ENTRY = "https://www.szsia.com/?cat=21"
_CATEGORIES = ((21, "association"), (22, "industry"), (20, "member"), (34, "notices"))
_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_BLOCK = re.compile(
    r"challenge-platform|Just a moment|Access Denied|403 Forbidden|captcha", re.I
)


class ShenzhenSemiconductorAssociationAdapter(AggregateAdapter):
    adapter_id = "shenzhen_semiconductor_association"
    channels = (
        SourceChannel(
            source_id="shenzhen-semiconductor-association",
            name="\u6df1\u5733\u5e02\u534a\u5bfc\u4f53\u884c\u4e1a\u534f\u4f1a\u2014\u52a8\u6001\u8d44\u8baf",
            url=_ENTRY,
            source_grade="B",
            event_prior=(
                "funding",
                "partnership",
                "technical_milestone",
                "new_site_or_entity",
            ),
            allowed_hosts=("www.szsia.com", "szsia.com"),
            allowed_path_patterns=(r"/",),
        ),
    )
    minimum_listing_count = 0
    maximum_listing_count = 500

    def parse_listing(
        self, channel: SourceChannel, html: bytes, context: AdapterContext
    ) -> list[SourceArticleIndex]:
        cutoff = (
            None
            if context.capture_full_visible_window
            else self._today(context.now) - timedelta(days=2)
        )
        output = []
        seen = {}
        for category, name in _CATEGORIES:
            page, page_url, page_html = (
                1,
                self._listing_url(category, 1),
                (
                    html
                    if category == 21
                    else context.fetch(self._listing_url(category, 1))
                ),
            )
            while True:
                self._block(channel.source_id, page_html, True)
                a = AdaptiveSelector(
                    page_html, url=page_url, storage_path=context.adaptive_db
                )
                selected = a.css(
                    "div.mainBody div.wp-block-columns.hdlist > div.wp-block-column",
                    identifier=f"{channel.source_id}:listing-items",
                    minimum_count=1,
                    maximum_count=50,
                )
                if not selected.elements:
                    raise ListingInvariantError(
                        f"{channel.source_id} listing selector failed closed"
                    )
                for pos, node in enumerate(selected.elements, 1):
                    links, dates = (
                        tuple(node.css("div.block-imgL > div.txt > h2 > a")),
                        tuple(node.css("div.block-imgL > div.txt > div.time")),
                    )
                    if len(links) != 1 or len(dates) != 1:
                        raise ListingInvariantError(
                            f"{channel.source_id} listing title/date cardinality failed"
                        )
                    title = self.clean_text(
                        links[0].get_all_text(separator=" ", strip=True)
                    )
                    url = self._canonical_article(
                        urljoin(page_url, str(links[0].attrib.get("href") or ""))
                    )
                    article_id = self._article_id(url)
                    when = self._parse_date(
                        dates[0].get_all_text(separator=" ", strip=True)
                    )
                    if not title or not article_id or when is None:
                        raise ListingInvariantError(
                            f"{channel.source_id} malformed listing article"
                        )
                    if when > self._today(context.now):
                        raise ListingInvariantError(
                            f"{channel.source_id} article is future dated"
                        )
                    if cutoff is not None and when < cutoff:
                        continue
                    item = SourceArticleIndex(
                        source_id=channel.source_id,
                        source_article_id=article_id,
                        channel=name,
                        canonical_url=url,
                        title=title,
                        published_at=when.isoformat(),
                        discovered_at=context.now.replace(microsecond=0).isoformat(),
                        cursor_value=f"{when.isoformat()}|{article_id}",
                        listing_page=page_url,
                        listing_position=len(output) + 1,
                        content_hash=self.stable_hash(f"{url}\n{title}\n{when}"),
                        discovery_method=selected.method,
                        structured_data={
                            "category_id": category,
                            "category": name,
                            "page": page,
                            "page_position": pos,
                        },
                    )
                    old = seen.get(article_id)
                    if old:
                        if (old.canonical_url, old.title, old.published_at) != (
                            url,
                            title,
                            when.isoformat(),
                        ):
                            raise ListingInvariantError(
                                f"{channel.source_id} conflicting duplicate article {article_id}"
                            )
                        continue
                    seen[article_id] = item
                    output.append(item)
                nexts = tuple(
                    a.selector.css("nav.navigation.pagination a.next.page-numbers")
                )
                if len(nexts) > 1:
                    raise ListingInvariantError(
                        f"{channel.source_id} ambiguous pagination"
                    )
                if context.capture_full_visible_window or not nexts:
                    break
                page += 1
                if page > 20:
                    raise ListingInvariantError(
                        f"{channel.source_id} pagination did not close"
                    )
                page_url = self._canonical_listing(
                    urljoin(page_url, str(nexts[0].attrib.get("href") or ""))
                )
                if not page_url:
                    raise ListingInvariantError(
                        f"{channel.source_id} invalid next-page URL"
                    )
                page_html = context.fetch(page_url)
        self.validate_listing(channel, output)
        return output

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        self._block(channel.source_id, html, False)
        a = AdaptiveSelector(
            html, url=index.canonical_url, storage_path=context.adaptive_db
        )
        title = a.css(
            "div.mainBody > div.wp-block-columns.xmfd > div.wp-block-column > h1",
            identifier=f"{channel.source_id}:detail-title",
            maximum_count=1,
        )
        date = a.css(
            "div.mainBody > div.wp-block-columns.xmfd div.time.text-center",
            identifier=f"{channel.source_id}:detail-date",
            maximum_count=1,
        )
        body = a.css(
            "div.mainBody > div.wp-block-columns.xmfd > div.wp-block-column > div.content",
            identifier=f"{channel.source_id}:detail-body",
            maximum_count=1,
        )
        if not title.elements or not date.elements or not body.elements:
            raise DetailFetchError(
                f"{channel.source_id} detail selector failed closed for {index.source_article_id}"
            )
        title_text = self.clean_text(
            title.elements[0].get_all_text(separator=" ", strip=True)
        )
        date_text = self.clean_text(
            date.elements[0].get_all_text(separator=" ", strip=True)
        )
        when = self._parse_date(date_text)
        text = self.clean_text(body.elements[0].get_all_text(separator=" ", strip=True))
        marker = "\u6df1\u5733\u5e02\u534a\u5bfc\u4f53\u884c\u4e1a\u534f\u4f1a"
        if marker not in date_text or marker not in a.selector.get_all_text(
            separator=" ", strip=True
        ):
            raise DetailFetchError(
                f"{channel.source_id} official association marker missing"
            )
        if not self._title_eq(index.title, title_text):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for {index.source_article_id}"
            )
        if when is None or when.isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for {index.source_article_id}"
            )
        if len(text) < 120:
            listing_body = index.title.strip()
            if len(listing_body) < 4:
                raise DetailFetchError(
                    f"{channel.source_id} detail body too short for {index.source_article_id}"
                )
            return CleanArticle(
                index=index,
                clean_body=listing_body,
                author=marker,
                structured_data={
                    **index.structured_data,
                    "detail_published_at": when.isoformat(),
                    "detail_fallback": "detail_contains_only_non_textual_media",
                },
                extraction_method="listing-headline-fallback",
                evidence_locators={
                    "title": "listing:.hdlist h2>a",
                    "published_at": "listing:.hdlist .time",
                    "body": "listing:.hdlist h2>a",
                },
                fetch_status="listing_complete",
                failure_reason="detail_contains_only_non_textual_media",
                content_hash=sha256(
                    f"{index.title}\n{listing_body}".encode()
                ).hexdigest(),
            )
        method = (
            "adaptive"
            if "adaptive" in {title.method, date.method, body.method}
            else "exact"
        )
        return CleanArticle(
            index=index,
            clean_body=text,
            author=marker,
            structured_data={
                **index.structured_data,
                "detail_published_at": when.isoformat(),
            },
            extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={
                "title": "detail:div.mainBody .xmfd h1",
                "published_at": "detail:div.mainBody .xmfd .time.text-center",
                "body": "detail:div.mainBody .xmfd .content",
            },
            content_hash=sha256(f"{index.title}\n{text}".encode()).hexdigest(),
        )

    def rule_events(self, channel, article):
        return extract_media_events(
            channel,
            article,
            config=IndustryRuleConfig(processor="rules:szsia-v1"),
            funding_processor="rules:szsia-funding-v1",
        )

    @staticmethod
    def _today(now):
        return now.astimezone(ZoneInfo("Asia/Shanghai")).date()

    @staticmethod
    def _listing_url(category, page):
        return "https://www.szsia.com/?" + urlencode(
            {"cat": category, **({"paged": page} if page > 1 else {})}
        )

    @staticmethod
    def _article_id(url):
        values = parse_qs(urlparse(url).query)
        value = values.get("p", [])
        return (
            value[0]
            if len(value) == 1 and value[0].isdigit() and int(value[0]) > 0
            else ""
        )

    @staticmethod
    def _canonical_article(url):
        p = urlparse(url)
        values = parse_qs(p.query)
        value = values.get("p", [])
        return (
            f"https://www.szsia.com/?p={value[0]}"
            if p.scheme == "https"
            and p.hostname in {"www.szsia.com", "szsia.com"}
            and p.path == "/"
            and len(values) == 1
            and len(value) == 1
            and value[0].isdigit()
            else ""
        )

    @staticmethod
    def _canonical_listing(url):
        p = urlparse(url)
        values = parse_qs(p.query)
        if (
            p.scheme != "https"
            or p.hostname not in {"www.szsia.com", "szsia.com"}
            or p.path != "/"
            or "cat" not in values
            or set(values) - {"cat", "paged"}
        ):
            return ""
        return "https://www.szsia.com/?" + urlencode(
            {key: values[key][0] for key in sorted(values)}
        )

    @staticmethod
    def _parse_date(value):
        m = _DATE.search(value)
        return datetime.strptime(m.group(0), "%Y-%m-%d").date() if m else None

    @staticmethod
    def _title_eq(a, b):
        return re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b)

    @staticmethod
    def _block(source, html, listing):
        if _BLOCK.search(html.decode("utf-8", errors="ignore")):
            raise (ListingInvariantError if listing else DetailFetchError)(
                f"{source} access interstitial detected; no bypass"
            )
