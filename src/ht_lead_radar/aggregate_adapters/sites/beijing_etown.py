"""Fail-closed adapter for Beijing E-Town major projects."""

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
from ..industry_rules import IndustryRuleConfig, extract_industry_events
from ..models import CleanArticle, SourceArticleIndex, SourceChannel

_ENTRY = "https://kfqgw.beijing.gov.cn/ywdt/gdcyfzgd/zdxm/"
_PATH = re.compile(r"/ywdt/gdcyfzgd/zdxm/20\d{4}/t(?P<id>20\d{6}_\d+)\.html")
_DATE = re.compile(r"(?P<d>20\d{2}[-\u5e74]\d{2}[-\u6708]\d{2})")
_BLOCK = re.compile(
    r"challenge-platform|Just a moment|Access Denied|403 Forbidden|captcha", re.I
)


class BeijingEtownMajorProjectsAdapter(AggregateAdapter):
    adapter_id = "beijing_etown_major_projects"
    channels = (
        SourceChannel(
            source_id="beijing-etown-major-projects",
            name="\u5317\u4eac\u7ecf\u6d4e\u6280\u672f\u5f00\u53d1\u533a\u2014\u91cd\u5927\u9879\u76ee",
            url=_ENTRY,
            source_grade="A",
            event_prior=(
                "factory_or_capacity",
                "new_site_or_entity",
                "technical_milestone",
            ),
            allowed_hosts=("kfqgw.beijing.gov.cn",),
            allowed_path_patterns=(r"/ywdt/gdcyfzgd/zdxm/20\d{4}/t20\d{6}_\d+\.html",),
        ),
    )
    minimum_listing_count = 0
    maximum_listing_count = 500

    def parse_listing(
        self, channel: SourceChannel, html: bytes, context: AdapterContext
    ) -> list[SourceArticleIndex]:
        self._block(channel.source_id, html, True)
        cutoff = (
            None
            if context.capture_full_visible_window
            else self._today(context.now) - timedelta(days=2)
        )
        seen, output, page_html, page_url = set(), [], html, channel.url
        closed = False
        for page in range(1, 31):
            adaptive = AdaptiveSelector(
                page_html, url=page_url, storage_path=context.adaptive_db
            )
            selected = adaptive.css(
                "div.container.clearfix > ul.list > li",
                identifier=f"{channel.source_id}:listing-items",
                minimum_count=1,
                maximum_count=20,
            )
            if not selected.elements:
                raise ListingInvariantError(
                    f"{channel.source_id} listing selector failed closed"
                )
            previous = oldest = None
            for pos, node in enumerate(selected.elements, 1):
                links, dates = tuple(node.css("a")), tuple(node.css("span.date"))
                if len(links) != 1 or len(dates) != 1:
                    raise ListingInvariantError(
                        f"{channel.source_id} listing title/date cardinality failed"
                    )
                title = self.clean_text(
                    str(links[0].attrib.get("title") or "")
                    or links[0].get_all_text(strip=True)
                )
                url = self._canonical(
                    urljoin(page_url, str(links[0].attrib.get("href") or ""))
                )
                article_id, date = (
                    self._article_id(url),
                    self._parse_date(dates[0].get_all_text(strip=True)),
                )
                if not title or not article_id or date is None:
                    raise ListingInvariantError(
                        f"{channel.source_id} malformed listing article"
                    )
                if date > self._today(context.now) or (previous and date > previous):
                    raise ListingInvariantError(
                        f"{channel.source_id} invalid listing date order"
                    )
                previous = oldest = date
                if cutoff is not None and date < cutoff:
                    continue
                if article_id in seen:
                    raise ListingInvariantError(
                        f"{channel.source_id} duplicate article {article_id}"
                    )
                seen.add(article_id)
                output.append(
                    SourceArticleIndex(
                        source_id=channel.source_id,
                        source_article_id=article_id,
                        channel="major_projects",
                        canonical_url=url,
                        title=title,
                        published_at=date.isoformat(),
                        discovered_at=context.now.replace(microsecond=0).isoformat(),
                        cursor_value=f"{date.isoformat()}|{article_id}",
                        listing_page=page_url,
                        listing_position=len(output) + 1,
                        content_hash=self.stable_hash(f"{url}\n{title}\n{date}"),
                        discovery_method=selected.method,
                        structured_data={"page": page, "page_position": pos},
                    )
                )
            if oldest is None:
                raise ListingInvariantError(f"{channel.source_id} empty listing page")
            if (
                context.capture_full_visible_window
                or (cutoff is not None and oldest < cutoff)
                or len(selected.elements) < 20
            ):
                closed = True
                break
            page_url = f"{_ENTRY}index_{page}.html"
            page_html = context.fetch(page_url)
            self._block(channel.source_id, page_html, True)
        if not closed:
            raise ListingInvariantError(f"{channel.source_id} pagination did not close")
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
            "meta[name='ArticleTitle']",
            identifier=f"{channel.source_id}:detail-title",
            maximum_count=1,
        )
        date = a.css(
            "meta[name='PubDate']",
            identifier=f"{channel.source_id}:detail-date",
            maximum_count=1,
        )
        body = a.css(
            "#div_zhengwen > div.view",
            identifier=f"{channel.source_id}:detail-body",
            maximum_count=1,
        )
        if not title.elements or not date.elements or not body.elements:
            raise DetailFetchError(
                f"{channel.source_id} detail selector failed closed for {index.source_article_id}"
            )
        if (
            self._meta(a, "SiteIDCode") != "1100000158"
            or self._meta(a, "ColumnName") != "\u91cd\u5927\u9879\u76ee"
        ):
            raise DetailFetchError(f"{channel.source_id} official page marker mismatch")
        value, when, text = (
            self.clean_text(str(title.elements[0].attrib.get("content") or "")),
            self._parse_date(str(date.elements[0].attrib.get("content") or "")),
            self.clean_text(body.elements[0].get_all_text(separator=" ", strip=True)),
        )
        if not self._title_eq(index.title, value):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for {index.source_article_id}"
            )
        if when is None or when.isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for {index.source_article_id}"
            )
        if len(text) < 120:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for {index.source_article_id}"
            )
        method = (
            "adaptive"
            if "adaptive" in {title.method, date.method, body.method}
            else "exact"
        )
        return CleanArticle(
            index=index,
            clean_body=text,
            author=self._meta(a, "ContentSource"),
            structured_data={
                **index.structured_data,
                "detail_published_at": when.isoformat(),
            },
            extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={
                "title": "meta:ArticleTitle",
                "published_at": "meta:PubDate",
                "body": "detail:#div_zhengwen>div.view",
            },
            content_hash=sha256(f"{index.title}\n{text}".encode()).hexdigest(),
        )

    def rule_events(self, channel, article):
        return extract_industry_events(
            channel,
            article,
            config=IndustryRuleConfig(processor="rules:beijing-etown-v1"),
        )

    @staticmethod
    def _today(now):
        return now.astimezone(ZoneInfo("Asia/Shanghai")).date()

    @staticmethod
    def _article_id(url):
        m = _PATH.fullmatch(urlparse(url).path)
        return m.group("id") if m else ""

    @staticmethod
    def _canonical(url):
        p = urlparse(url)
        return (
            f"https://{p.hostname}{p.path}"
            if p.scheme == "https" and p.hostname == "kfqgw.beijing.gov.cn"
            else ""
        )

    @staticmethod
    def _parse_date(value):
        m = _DATE.search(value)
        return (
            datetime.strptime(
                m.group("d")
                .replace("\u5e74", "-")
                .replace("\u6708", "-")
                .replace("\u65e5", ""),
                "%Y-%m-%d",
            ).date()
            if m
            else None
        )

    @staticmethod
    def _title_eq(a, b):
        return re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b)

    @staticmethod
    def _meta(a, name):
        values = tuple(a.selector.css(f"meta[name='{name}']"))
        return (
            str(values[0].attrib.get("content") or "").strip()
            if len(values) == 1
            else ""
        )

    @staticmethod
    def _block(source, html, listing):
        if _BLOCK.search(html.decode("utf-8", errors="ignore")):
            raise (ListingInvariantError if listing else DetailFetchError)(
                f"{source} access interstitial detected; no bypass"
            )
