"""Fail-closed adapter for the Suzhou Robot Industry Association homepage."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from ..adaptive import AdaptiveSelector
from ..base import (
    AggregateAdapter,
    DetailFetchError,
    ListingInvariantError,
)
from ..industry_rules import IndustryRuleConfig, extract_media_events
from ..models import CleanArticle, SourceArticleIndex, SourceChannel


_CHINA = ZoneInfo("Asia/Shanghai")
_NEWS_TAB_NAMES = ("\u884c\u4e1a\u8d44\u8baf", "\u534f\u4f1a\u52a8\u6001", "\u534f\u4f1a\u6d3b\u52a8")
_ACCESS_INTERSTITIAL = re.compile(
    r"challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"\u9a8c\u8bc1\u7801|\u8bbf\u95ee\u9a8c\u8bc1|\u8bbf\u95ee\u8fc7\u4e8e\u9891\u7e41|"
    r"Access Denied|403 Forbidden|captcha|\u73af\u5883\u5f02\u5e38|\u64cd\u4f5c\u9891\u7e41",
    re.I,
)
_WECHAT_PATH = re.compile(r"/s/[A-Za-z0-9_-]{12,128}$")
_NATIVE_PATH = re.compile(r"/(policy|localPolicy)/(\d+)\.html$")
_DATE = re.compile(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})")
_BODY_NOISE = re.compile(
    r"^(?:\u8d23\u4efb\u7f16\u8f91|\u6765\u6e90[:\uff1a]|\u9605\u8bfb\u539f\u6587|"
    r"\u63a8\u8350\u9605\u8bfb|\u5f80\u671f\u63a8\u8350|\u514d\u8d23\u58f0\u660e|"
    r"\u626b\u7801\u5173\u6ce8|\u70b9\u51fb\u5173\u6ce8|\u7248\u6743\u58f0\u660e|\u5206\u4eab|\u6536\u85cf)$"
)


class SuzhouRobotAssociationAdapter(AggregateAdapter):
    """Index every visible article card from the finite homepage window."""

    adapter_id = "suzhou_robot_association"
    channels = (
        SourceChannel(
            source_id="suzhou-robot-association",
            name="\u82cf\u5dde\u5e02\u673a\u5668\u4eba\u4ea7\u4e1a\u534f\u4f1a\u2014\u65b0\u95fb\u516c\u544a",
            url="https://robotsz.org.cn/",
            source_grade="B",
            event_prior=(
                "funding", "factory_or_capacity", "partnership",
                "policy_or_standard", "technical_milestone", "new_site_or_entity",
                "major_order", "customer_validation",
            ),
            allowed_hosts=("robotsz.org.cn", "mp.weixin.qq.com"),
            allowed_path_patterns=(
                r"/(?:policy|localPolicy)/\d+\.html",
                r"/s/[A-Za-z0-9_-]{12,128}",
            ),
        ),
    )
    minimum_listing_count = 1
    maximum_listing_count = 80

    def parse_listing(self, channel, html, context):
        self._reject_interstitial(channel.source_id, html, listing=True)
        adaptive = AdaptiveSelector(html, url=channel.url, storage_path=context.adaptive_db)
        tabs = adaptive.css(
            "div.i-news .tabs > a",
            identifier=f"{channel.source_id}:listing-tabs",
            minimum_count=3, maximum_count=3,
        )
        sections = adaptive.css(
            "div.i-newsCon > div.item",
            identifier=f"{channel.source_id}:listing-news-sections",
            minimum_count=3, maximum_count=3,
        )
        policy_panel = adaptive.css(
            "div.i-plylist",
            identifier=f"{channel.source_id}:listing-policy-section",
            minimum_count=1, maximum_count=1,
        )
        if not (tabs.elements and sections.elements and policy_panel.elements):
            raise ListingInvariantError(f"{channel.source_id} homepage selector failed closed")
        tab_names = tuple(self.clean_text(x.get_all_text(separator=" ", strip=True)) for x in tabs.elements)
        if tab_names != _NEWS_TAB_NAMES:
            raise ListingInvariantError(f"{channel.source_id} unexpected homepage news tabs {tab_names!r}")

        output = []
        seen_ids, seen_urls = set(), set()
        found_at = context.now.replace(microsecond=0).isoformat()
        for section_no, section in enumerate(sections.elements, start=1):
            previous = None
            cards = tuple(section.css("ul > li"))
            for card_no, card in enumerate(cards, start=1):
                item = self._listing_card(
                    channel, card, tab_names[section_no - 1], section_no, card_no,
                    channel.url, found_at, self._method(tabs.method, sections.method),
                )
                previous = self._validate_section_date(channel, item, previous)
                self._append_unique(channel, output, seen_ids, seen_urls, item)

        policy_cards = tuple(policy_panel.elements[0].css("ul > li"))
        if not policy_cards:
            raise ListingInvariantError(f"{channel.source_id} policy panel unexpectedly empty")
        previous = None
        for card_no, card in enumerate(policy_cards, start=1):
            item = self._listing_card(
                channel, card, "\u653f\u7b56\u4fe1\u606f", 4, card_no,
                channel.url, found_at,
                self._method(tabs.method, sections.method, policy_panel.method),
            )
            if urlparse(item.canonical_url).hostname != "robotsz.org.cn":
                raise ListingInvariantError(f"{channel.source_id} policy panel has a non-native URL")
            previous = self._validate_section_date(channel, item, previous)
            self._append_unique(channel, output, seen_ids, seen_urls, item)

        numbered = [SourceArticleIndex(**{**item.to_dict(), "listing_position": n}) for n, item in enumerate(output, 1)]
        self.validate_listing(channel, numbered)
        return numbered

    def parse_detail(self, channel, index, html, context):
        host = (urlparse(index.canonical_url).hostname or "").lower()
        if host == "mp.weixin.qq.com" and self._is_interstitial(html):
            return self._listing_fallback(
                index,
                reason="wechat_detail_access_interstitial",
            )
        self._reject_interstitial(channel.source_id, html, listing=False)
        if host == "mp.weixin.qq.com":
            if index.source_article_id != self._wechat_id(index.canonical_url):
                raise DetailFetchError(f"{channel.source_id} WeChat URL/id mismatch for {index.source_article_id}")
            return self._extract_detail(
                channel, index, html, context,
                "#activity-name", "#publish_time", "div#js_content.rich_media_content",
                self._first_text_from_html(html, index.canonical_url, context, "#js_name"),
                "wechat",
            )
        if host == "robotsz.org.cn":
            if index.source_article_id != self._native_id(index.canonical_url):
                raise DetailFetchError(f"{channel.source_id} native URL/id mismatch for {index.source_article_id}")
            try:
                return self._extract_detail(
                    channel, index, html, context,
                    "div.content.content-news > div.newstit",
                    "div.content.content-news > div.newstm span:first-child",
                    "div.content.content-news > div.news-article",
                    "\u82cf\u5dde\u5e02\u673a\u5668\u4eba\u4ea7\u4e1a\u534f\u4f1a",
                    "native",
                )
            except DetailFetchError as exc:
                if "detail body too short" in str(exc):
                    return self._listing_fallback(
                        index,
                        reason="native_detail_contains_no_extractable_text",
                    )
                raise
        raise DetailFetchError(f"{channel.source_id} detail host rejected for {index.source_article_id}")

    def rule_events(self, channel, article):
        return extract_media_events(
            channel, article,
            config=IndustryRuleConfig(processor="rules:suzhou-robot-association-v1"),
            funding_processor="rules:suzhou-robot-association-funding-v1",
        )

    def _extract_detail(self, channel, index, html, context, title_css, date_css, body_css, author, prefix):
        adaptive = AdaptiveSelector(html, url=index.canonical_url, storage_path=context.adaptive_db)
        title = adaptive.css(title_css, identifier=f"{channel.source_id}:{prefix}-title", minimum_count=1, maximum_count=1)
        date = adaptive.css(date_css, identifier=f"{channel.source_id}:{prefix}-date", minimum_count=1, maximum_count=1)
        body = adaptive.css(body_css, identifier=f"{channel.source_id}:{prefix}-body", minimum_count=1, maximum_count=1)
        if not (title.elements and date.elements and body.elements):
            raise DetailFetchError(f"{channel.source_id} detail selector failed closed for {index.source_article_id}")
        title_text = self.clean_text(title.elements[0].get_all_text(separator=" ", strip=True))
        if not self._titles_match(index.title, title_text):
            raise DetailFetchError(f"{channel.source_id} detail title mismatch for {index.source_article_id}")
        detail_date = self._date(self.clean_text(date.elements[0].get_all_text(separator=" ", strip=True)))
        if detail_date is None or detail_date.isoformat() != index.published_at[:10]:
            raise DetailFetchError(f"{channel.source_id} listing/detail date mismatch for {index.source_article_id}")
        clean_body = self._clean_body(body.elements[0])
        if len(clean_body) < 80:
            raise DetailFetchError(f"{channel.source_id} detail body too short for {index.source_article_id}")
        method = "adaptive" if "adaptive" in {title.method, date.method, body.method} else "exact"
        structured = dict(index.structured_data)
        structured["detail_published_at"] = detail_date.isoformat()
        return CleanArticle(
            index=index, clean_body=clean_body, author=author,
            structured_data=structured, extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={"title": f"{prefix}:title", "published_at": f"{prefix}:date", "body": f"{prefix}:article-body"},
            fetch_status="ok", content_hash=sha256(f"{index.title}\n{clean_body}".encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _listing_fallback(index, *, reason):
        """Preserve an exact homepage headline when the linked body is unavailable.

        This is deliberately not a reconstructed article body.  The listing title
        and date remain auditable in the archived homepage response, and the
        extraction method makes the reduced evidence boundary explicit.
        """

        body = index.title.strip()
        if len(body) < 4:
            raise DetailFetchError(
                f"{index.source_id} listing headline too short for "
                f"{index.source_article_id}"
            )
        return CleanArticle(
            index=index,
            clean_body=body,
            author="\u82cf\u5dde\u5e02\u673a\u5668\u4eba\u4ea7\u4e1a\u534f\u4f1a",
            structured_data={
                **index.structured_data,
                "detail_fallback": reason,
            },
            extraction_method="listing-headline-fallback",
            evidence_locators={
                "title": "listing:homepage-card-title",
                "published_at": "listing:homepage-card-date",
                "body": "listing:homepage-card-title",
            },
            fetch_status="listing_complete",
            failure_reason=reason,
            content_hash=sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest(),
        )

    def _listing_card(self, channel, card, category, section_no, card_no, listing_page, found_at, method):
        links = tuple(card.css("a"))
        titles = tuple(card.css("h4, div.tit"))
        dates = tuple(card.css("span.time"))
        if len(links) != 1 or len(titles) != 1 or len(dates) != 1:
            raise ListingInvariantError(f"{channel.source_id} {category} card {card_no} has invalid title/date/link cardinality")
        url = self._canonical_url(urljoin(listing_page, str(links[0].attrib.get("href") or "")))
        article_id = self._article_id(url)
        title = self.clean_text(titles[0].get_all_text(separator=" ", strip=True))
        date_label = self.clean_text(dates[0].get_all_text(separator=" ", strip=True))
        published = self._date(date_label)
        if not article_id or not title or published is None:
            raise ListingInvariantError(f"{channel.source_id} {category} card {card_no} has invalid URL, title, or date")
        published_at = datetime.combine(published, datetime.min.time(), tzinfo=_CHINA).isoformat()
        structured = {"homepage_category": category, "homepage_section": section_no, "homepage_item": card_no, "listing_date_label": date_label}
        return SourceArticleIndex(
            source_id=channel.source_id, source_article_id=article_id, channel=category,
            canonical_url=url, title=title, published_at=published_at, discovered_at=found_at,
            cursor_value=f"{published_at}|{article_id}", listing_page=listing_page,
            listing_position=0,
            content_hash=self.stable_hash("\n".join((url, title, published_at, repr(sorted(structured.items()))))),
            discovery_method=method, structured_data=structured,
        )

    @staticmethod
    def _validate_section_date(channel, item, previous):
        current = datetime.fromisoformat(item.published_at)
        if previous is not None and current > previous:
            raise ListingInvariantError(f"{channel.source_id} {item.channel} is not newest-first")
        return current

    @staticmethod
    def _append_unique(channel, output, seen_ids, seen_urls, item):
        if item.source_article_id in seen_ids or item.canonical_url in seen_urls:
            raise ListingInvariantError(f"{channel.source_id} duplicate homepage article {item.canonical_url}")
        seen_ids.add(item.source_article_id)
        seen_urls.add(item.canonical_url)
        output.append(item)

    @staticmethod
    def _method(*methods):
        return "adaptive" if "adaptive" in methods else "exact"

    @staticmethod
    def _canonical_url(value):
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            return value
        if host == "mp.weixin.qq.com" and _WECHAT_PATH.fullmatch(parsed.path):
            return f"https://mp.weixin.qq.com{parsed.path}"
        native = _NATIVE_PATH.fullmatch(parsed.path)
        if host == "robotsz.org.cn" and native:
            return f"https://robotsz.org.cn/{native.group(1)}/{native.group(2)}.html"
        return value

    @staticmethod
    def _article_id(url):
        return SuzhouRobotAssociationAdapter._wechat_id(url) or SuzhouRobotAssociationAdapter._native_id(url)

    @staticmethod
    def _wechat_id(url):
        parsed = urlparse(url)
        if parsed.hostname != "mp.weixin.qq.com" or not _WECHAT_PATH.fullmatch(parsed.path):
            return ""
        return f"wechat-{sha256(url.encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def _native_id(url):
        parsed = urlparse(url)
        match = _NATIVE_PATH.fullmatch(parsed.path)
        if parsed.hostname != "robotsz.org.cn" or not match:
            return ""
        return f"{match.group(1)}-{match.group(2)}"

    @staticmethod
    def _date(value):
        match = _DATE.search(value)
        if not match:
            return None
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except ValueError:
            return None

    @classmethod
    def _clean_body(cls, element):
        blocks, seen = [], set()
        for block in element.xpath(".//p | .//h2 | .//h3 | .//h4 | .//li | .//blockquote"):
            text = cls.clean_text(block.get_all_text(separator=" ", strip=True))
            if not text or text in seen or _BODY_NOISE.search(text):
                continue
            seen.add(text)
            blocks.append(text)
        return cls.clean_text(" ".join(blocks)) if blocks else ""

    @staticmethod
    def _titles_match(expected, actual):
        left = re.sub(r"[\s\u200b\uff5c|]", "", expected)
        right = re.sub(r"[\s\u200b\uff5c|]", "", actual)
        return bool(left and right and (left == right or left in right or right in left))

    @staticmethod
    def _first_text_from_html(html, url, context, selector):
        adaptive = AdaptiveSelector(html, url=url, storage_path=context.adaptive_db)
        elements = tuple(adaptive.selector.css(selector))
        return SuzhouRobotAssociationAdapter.clean_text(elements[0].get_all_text(separator=" ", strip=True)) if elements else ""

    @staticmethod
    def _reject_interstitial(source_id, html, *, listing):
        if not SuzhouRobotAssociationAdapter._is_interstitial(html):
            return
        error = ListingInvariantError if listing else DetailFetchError
        raise error(f"{source_id} access interstitial detected; no bypass attempted")

    @staticmethod
    def _is_interstitial(html):
        return bool(
            _ACCESS_INTERSTITIAL.search(html.decode("utf-8", errors="ignore"))
        )


__all__ = ["SuzhouRobotAssociationAdapter"]
