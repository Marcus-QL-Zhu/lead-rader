"""Dedicated adapter for Shenzhen SASAC enterprise appointment notices."""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import re
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from ..adaptive import AdaptiveSelector
from ..base import AdapterContext, AggregateAdapter, DetailFetchError, ListingInvariantError
from ..industry_rules import IndustryRuleConfig, extract_industry_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_CHINA = ZoneInfo("Asia/Shanghai")
_DETAIL_PATH = re.compile(r"/zwgk/qt/rsxx/content/post_(\d+)\.html")
_APPOINTMENT_TITLE = re.compile(r"职务任免|任职|免职|退休")
_NON_ENTERPRISE = re.compile(r"公务员|机关|选调|招聘|公示")
_ACCESS_CONTROL = re.compile(
    r"访问过于频繁|安全验证|captcha|challenge-platform|403 Forbidden|Access Denied",
    re.I,
)
_LEGAL_COMPANY = re.compile(
    r"[A-Za-z0-9\u4e00-\u9fff·（）()]{2,64}?"
    r"(?:股份有限公司|有限责任公司|有限公司)"
)


class ShenzhenSasacAppointmentsAdapter(AggregateAdapter):
    """Read regulator-issued changes at Shenzhen municipal enterprises."""

    adapter_id = "shenzhen_sasac_appointments"
    channels = (
        SourceChannel(
            source_id="shenzhen-sasac-appointments",
            name="深圳市国资委—市属企业人事任免",
            url="https://gzw.sz.gov.cn/zwgk/qt/rsxx/",
            source_grade="A",
            event_prior=("executive_change",),
            allowed_hosts=("gzw.sz.gov.cn",),
            allowed_path_patterns=(r"/zwgk/qt/rsxx/content/post_\d+\.html",),
        ),
    )
    # Appointment channels legitimately have no item in the daily overlap.
    minimum_listing_count = 0
    maximum_listing_count = 100

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        text = html.decode("utf-8", errors="replace")
        if _ACCESS_CONTROL.search(text):
            raise ListingInvariantError(
                f"{channel.source_id} access control detected; no bypass"
            )
        adaptive = AdaptiveSelector(
            html,
            url=channel.url,
            storage_path=context.adaptive_db,
        )
        selection = adaptive.css(
            "ul.tzgg_content li a[href]",
            identifier=f"{channel.source_id}:listing-items",
            minimum_count=1,
            maximum_count=100,
        )
        if not selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} listing selector failed closed"
            )
        source_today = context.now.astimezone(_CHINA).date()
        window_start = source_today - timedelta(days=3)
        window_end = source_today - timedelta(days=1)
        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        for element in selection.elements:
            href = str(element.attrib.get("href") or "").strip()
            canonical_url = urljoin(channel.url, href)
            match = _DETAIL_PATH.fullmatch(
                re.sub(r"^https?://[^/]+", "", canonical_url)
            )
            if not match:
                continue
            title_node = element.css("p")
            date_node = element.css("span")
            title = self.clean_text(
                (title_node[0] if title_node else element).get_all_text(
                    separator=" ", strip=True
                )
            )
            date_text = self.clean_text(
                date_node[0].get_all_text(separator=" ", strip=True)
                if date_node
                else ""
            )
            published = self._date(date_text)
            if (
                published is None
                or not _APPOINTMENT_TITLE.search(title)
                or _NON_ENTERPRISE.search(title)
            ):
                continue
            if not context.capture_full_visible_window and not (
                window_start <= published <= window_end
            ):
                continue
            article_id = match.group(1)
            if article_id in seen:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate article {article_id}"
                )
            seen.add(article_id)
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="government-enterprise-appointments",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published.isoformat(),
                    discovered_at=discovered_at,
                    cursor_value=f"{published.isoformat()}|{article_id}",
                    listing_page=channel.url,
                    listing_position=len(output) + 1,
                    content_hash=self.stable_hash(
                        f"{canonical_url}\n{title}\n{published.isoformat()}"
                    ),
                    discovery_method=f"css:{selection.method}",
                    structured_data={
                        "document_type": "single_company_flash",
                        "official_column": "人事信息",
                    },
                )
            )
        self.validate_listing(channel, output)
        return output

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        text = html.decode("utf-8", errors="replace")
        if _ACCESS_CONTROL.search(text):
            raise DetailFetchError(
                f"{channel.source_id} detail access control detected; no bypass"
            )
        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        title_selection = adaptive.css(
            "div.xl_wrap div.title",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        info_selection = adaptive.css(
            "div.xl_wrap div.title_info",
            identifier=f"{channel.source_id}:detail-info",
            minimum_count=1,
            maximum_count=1,
        )
        body_selection = adaptive.css(
            "div.xl_main.articleBox",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not (
            title_selection.elements
            and info_selection.elements
            and body_selection.elements
        ):
            raise DetailFetchError(
                f"{channel.source_id} detail selector failed closed for "
                f"{index.source_article_id}"
            )
        if self._meta(adaptive, "SiteIDCode") != "4403000052":
            raise DetailFetchError(
                f"{channel.source_id} official site marker missing for "
                f"{index.source_article_id}"
            )
        if self._meta(adaptive, "ColumnName") != "人事信息":
            raise DetailFetchError(
                f"{channel.source_id} detail column mismatch for "
                f"{index.source_article_id}"
            )
        title = self.clean_text(
            title_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        if not self._titles_match(index.title, title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        info = self.clean_text(
            info_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        detail_date = self._date(info)
        if detail_date is None or detail_date.isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )
        body = self.clean_text(
            body_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        if len(body) < 30 or not re.search(r"经研究决定|任|免去|不再担任", body):
            raise DetailFetchError(
                f"{channel.source_id} detail body invariant failed for "
                f"{index.source_article_id}"
            )
        methods = {
            title_selection.method,
            info_selection.method,
            body_selection.method,
        }
        extraction_method = "adaptive" if "adaptive" in methods else "exact"
        return CleanArticle(
            index=index,
            clean_body=body,
            structured_data={
                **index.structured_data,
                "official_site_id": "4403000052",
                "document_type": "single_company_flash",
            },
            extraction_method=extraction_method,
            adaptive_similarity=72 if extraction_method == "adaptive" else None,
            evidence_locators={
                "title": "detail:div.xl_wrap div.title",
                "published_at": "detail:div.xl_wrap div.title_info",
                "body": "detail:div.xl_main.articleBox",
                "official_site": "meta:SiteIDCode",
                "official_column": "meta:ColumnName",
            },
            fetch_status="ok",
            content_hash=sha256(f"{title}\n{body}".encode("utf-8")).hexdigest(),
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        return extract_industry_events(
            channel,
            article,
            config=IndustryRuleConfig(
                processor="rules:shenzhen-sasac-appointments-v1",
                event_types=("executive_change",),
                company_resolver=self._appointment_company,
            ),
        )

    @staticmethod
    def _appointment_company(
        article: CleanArticle,
        sentence: str,
        event_type: str,
    ) -> tuple[str, tuple[str, ...]]:
        del event_type
        matches = list(_LEGAL_COMPANY.finditer(sentence))
        if not matches:
            matches = list(_LEGAL_COMPANY.finditer(article.clean_body))
        if not matches:
            return "", ()
        company = matches[-1].group(0)
        company = re.sub(
            r"^(?:(?:委派|推荐|建议)[\u4e00-\u9fff·]{2,8}(?:任|担任|不再担任)|"
            r"免去[\u4e00-\u9fff·]{2,8}的)",
            "",
            company,
        )
        return company, (company,)

    @staticmethod
    def _date(value: str) -> date | None:
        match = re.search(r"(20\d{2})[-年](\d{1,2})[-月](\d{1,2})", value)
        if not match:
            return None
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            return None

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"\s+", "", expected)
        right = re.sub(r"\s+", "", actual)
        return bool(left and right and (left == right or left in right or right in left))

    @staticmethod
    def _meta(adaptive: AdaptiveSelector, name: str) -> str:
        rows = adaptive.selector.css(f'meta[name="{name}"]')
        return str(rows[0].attrib.get("content") or "").strip() if rows else ""


__all__ = ["ShenzhenSasacAppointmentsAdapter"]
