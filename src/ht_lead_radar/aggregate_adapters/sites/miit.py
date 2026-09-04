"""Dedicated, fail-closed adapter for MIIT Science Department files."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
import re
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

from ..adaptive import AdaptiveSelector
from ..base import (
    AdapterContext,
    AggregateAdapter,
    DetailFetchError,
    ListingInvariantError,
)
from ..industry_rules import IndustryRuleConfig, extract_industry_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_PAGE_SIZE = 24
_MAX_PAGES = 100
_OVERLAP_DAYS = 2
_ENTRY_URL = (
    'https://www.miit.gov.cn/api-gateway/jpaas-publish-server/front/page/'
    'build/unit?parseType=buildstatic'
    '&webId=8d828e408d90447786ddbe128d495e9e'
    '&tplSetId=209741b2109044b5b7695700b2bec37e'
    '&pageType=column'
    '&tagId=%E5%BD%93%E5%89%8D%E6%A0%8F%E7%9B%AE_list'
    '&editType=null'
    '&pageId=7df23bf39e2d42b793ebfcc3319015b7'
)
_PAGINATION_PATH = "/api-gateway/jpaas-publish-server/front/page/build/unit"
_ARTICLE_PATH = re.compile(
    r"/jgsj/kjs/wjfb/art/(?P<year>20\d{2})/"
    r"art_(?P<id>[0-9a-f]{32})\.html"
)
_ACCESS_INTERSTITIAL = re.compile(
    r"(?:challenge-platform|/cdn-cgi/challenge|Just a moment|"
    r"安全验证|访问验证|访问过于频繁|Access Denied|403 Forbidden|"
    r"captcha-container|TTGCaptcha)",
    re.I,
)
_CURRENT_POLICY_TITLE = re.compile(
    r"关于(?:(?:联合)?组织)?开展.+(?:工作|行动|试点|遴选).*(?:通知|公告)$|"
    r"关于(?:印发|公布|发布).+(?:通知|公告)$|"
    r"^(?:中华人民共和国)?工业和信息化部公告",
)
_STARTED_POLICY_TITLE = re.compile(
    r"关于(?:(?:联合)?组织)?开展.+(?:工作|行动|试点|遴选).*(?:通知|公告)$"
)
_CURRENT_POLICY_QUOTE = re.compile(
    r"(?:现|已经|已|决定|批准|正式)?"
    r"(?:印发|公布|发布|启动|开展|组织开展|成立)"
)
_NON_CURRENT_POLICY_QUOTE = re.compile(
    r'不(?:再|会)?(?:启动|开展|印发|公布|发布)|'
    r'未(?:启动|开展|印发|公布|发布)|'
    r"此前|曾于|去年|上年度|回顾|"
    r"拟(?:于|将)?|计划(?:于|将)?|将于|另行通知|"
    r"组织遴选.{0,40}(?:并)?发布|完成.{0,30}后.{0,30}发布"
)
_BODY_NOISE = re.compile(
    r"^(?:【打印本页】|【关闭窗口】|责任编辑[：:]|"
    r"相关阅读[：:]?|网站地图|主办单位[：:])"
)
_INDUSTRY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("semiconductor", re.compile(r"芯片|半导体|晶圆|光刻|封装")),
    (
        "embodied_intelligence",
        re.compile(r"机器人|具身智能|灵巧手|运动控制|机器视觉"),
    ),
    ("commercial_space", re.compile(r"航天|卫星|火箭|太空")),
    ("fusion_energy", re.compile(r"核聚变|聚变|托卡马克|等离子体")),
    ("brain_computer_interface", re.compile(r"脑机接口|类脑|神经调控")),
    (
        "advanced_manufacturing",
        re.compile(r"装备|材料|量产|产能|工业|制造|中试"),
    ),
    (
        "artificial_intelligence",
        re.compile(r"人工智能|大模型|AI\b|智能体|元宇宙"),
    ),
    ("biotech", re.compile(r"医药|医疗|生物制造|诊断|临床")),
)


class MiitAdapter(AggregateAdapter):
    """Enumerate MIIT's complete closed two-natural-day file window."""

    adapter_id = "miit"
    channels = (
        SourceChannel(
            source_id="miit-science-files",
            name="工业和信息化部科技司—文件发布",
            url=_ENTRY_URL,
            source_grade="A",
            event_prior=("policy_or_standard",),
            allowed_hosts=("www.miit.gov.cn", "wap.miit.gov.cn"),
            allowed_path_patterns=(
                r"/jgsj/kjs/wjfb/art/20\d{2}/art_[0-9a-f]{32}\.html",
            ),
        ),
    )
    # A government channel can legitimately publish nothing in the closed window.
    minimum_listing_count = 0
    maximum_listing_count = 500

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        self._reject_interstitial(channel.source_id, html, listing=True)
        source_today = self._source_today(context.now)
        window_start = source_today - timedelta(days=_OVERLAP_DAYS)
        window_end = source_today - timedelta(days=1)
        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen_ids: set[str] = set()
        previous_date = None
        first_metadata: dict[str, object] | None = None
        closed = False

        for page_number in range(1, _MAX_PAGES + 1):
            if page_number == 1:
                page_html = self._api_fragment(
                    channel.source_id,
                    html,
                    page_number,
                )
                page_url = channel.url
            else:
                if first_metadata is None:
                    raise ListingInvariantError(
                        f"{channel.source_id} pagination metadata missing"
                    )
                page_url = self._pagination_url(
                    channel,
                    first_metadata,
                    page_number,
                )
                payload = context.fetch(page_url)
                self._reject_interstitial(
                    channel.source_id,
                    payload,
                    listing=True,
                )
                page_html = self._api_fragment(
                    channel.source_id,
                    payload,
                    page_number,
                )

            parsed, metadata = self._parse_listing_page(
                channel,
                html=page_html,
                page_url=page_url,
                page_number=page_number,
                context=context,
                discovered_at=discovered_at,
                window_start=window_start,
                window_end=window_end,
            )
            if first_metadata is None:
                first_metadata = metadata
            self._validate_page_metadata(
                channel.source_id,
                metadata,
                first_metadata,
                page_number,
                len(parsed),
            )

            for item in parsed:
                item_date = self._date(item.published_at)
                if previous_date is not None and item_date > previous_date:
                    raise ListingInvariantError(
                        f"{channel.source_id} archive is not newest-first at "
                        f"page {page_number} position "
                        f"{item.structured_data['page_position']}"
                    )
                previous_date = item_date
                if item.source_article_id in seen_ids:
                    raise ListingInvariantError(
                        f"{channel.source_id} duplicate article "
                        f"{item.source_article_id} across pages"
                    )
                seen_ids.add(item.source_article_id)
                if window_start <= item_date <= window_end:
                    output.append(
                        SourceArticleIndex(
                            **{
                                **item.to_dict(),
                                "listing_position": len(output) + 1,
                            }
                        )
                    )

            oldest = self._date(parsed[-1].published_at)
            total_pages = int(first_metadata["total_pages"])
            if oldest < window_start or page_number == total_pages:
                closed = True
                break

        if not closed:
            raise ListingInvariantError(
                f"{channel.source_id} exceeded {_MAX_PAGES} pages before "
                "closing the natural-day window"
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
            "h1#con_title",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        date_selection = adaptive.css(
            "span#con_time",
            identifier=f"{channel.source_id}:detail-date",
            minimum_count=1,
            maximum_count=1,
        )
        body_selection = adaptive.css(
            "div#con_con.ccontent",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if (
            not title_selection.elements
            or not date_selection.elements
            or not body_selection.elements
        ):
            raise DetailFetchError(
                f"{channel.source_id} detail selector failed closed for "
                f"{index.source_article_id}"
            )

        if self._meta(adaptive.selector, "SiteIDCode") != "bm07000001":
            raise DetailFetchError(
                f"{channel.source_id} official site marker missing for "
                f"{index.source_article_id}"
            )
        if self._meta(adaptive.selector, "ColumnName") != "文件发布":
            raise DetailFetchError(
                f"{channel.source_id} detail column mismatch for "
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
        date_label = self.clean_text(
            date_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        detail_date = self._detail_date(date_label)
        if detail_date is None:
            raise DetailFetchError(
                f"{channel.source_id} detail date missing for "
                f"{index.source_article_id}"
            )
        if detail_date.isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )
        if detail_date > self._source_today(context.now):
            raise DetailFetchError(
                f"{channel.source_id} future-dated detail "
                f"{index.source_article_id}"
            )

        body = self._clean_body(body_selection.elements[0])
        if len(body) < 120:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for "
                f"{index.source_article_id}"
            )
        source_department = self._source_department(adaptive.selector)
        if source_department != "科技司":
            raise DetailFetchError(
                f"{channel.source_id} unexpected detail source for "
                f"{index.source_article_id}"
            )

        methods = {
            title_selection.method,
            date_selection.method,
            body_selection.method,
        }
        method = "adaptive" if "adaptive" in methods else "exact"
        tags = self._source_tags(f"{index.title} {body}")
        structured = dict(index.structured_data)
        structured.update(
            {
                "detail_published_at": detail_date.isoformat(),
                "source_department": source_department,
                "company": "工业和信息化部",
                "company_mentions": ("工业和信息化部",),
                "issuing_authority": "工业和信息化部",
            }
        )
        digest = sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=source_department,
            tags=tags,
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=72 if method == "adaptive" else None,
            evidence_locators={
                "title": "detail:h1#con_title",
                "published_at": "detail:span#con_time",
                "body": "detail:div#con_con.ccontent",
                "author": "detail:div.cinfo.center source label",
                "issuing_authority": "official host and article title",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        shared = extract_industry_events(
            channel,
            article,
            config=IndustryRuleConfig(
                processor="rules:miit-v1",
                event_types=("policy_or_standard",),
                company_resolver=self._resolve_authority,
            ),
        )
        events: dict[tuple[str, str], SemanticEvent] = {}
        for event in shared:
            quote = event.evidence_quotes[0] if event.evidence_quotes else ""
            if not self._current_policy_quote(quote):
                continue
            evidence = event.evidence_quotes
            if "工业和信息化部" not in quote:
                evidence = (article.index.title, *evidence)
            normalized = SemanticEvent(
                **{
                    **event.to_dict(),
                    "company_mentions": ("工业和信息化部",),
                    "canonical_company": "工业和信息化部",
                    "evidence_quotes": evidence,
                    "processor": "rules:miit-v1",
                }
            )
            events[(normalized.event_type, normalized.event_status)] = normalized

        title = article.index.title
        if _CURRENT_POLICY_TITLE.search(title):
            status = (
                "started"
                if _STARTED_POLICY_TITLE.search(title)
                else "completed"
            )
            key = ("policy_or_standard", status)
            if key not in events:
                events[key] = SemanticEvent(
                    source_id=channel.source_id,
                    source_article_id=article.index.source_article_id,
                    canonical_url=article.index.canonical_url,
                    company_mentions=("工业和信息化部",),
                    canonical_company="工业和信息化部",
                    event_type="policy_or_standard",
                    event_date=article.index.published_at[:10],
                    industry_tags=self._industry_tags(article),
                    event_summary=title[:300],
                    evidence_quotes=(title,),
                    confidence="high",
                    processor="rules:miit-v1",
                    content_hash=article.content_hash,
                    phase="strategy_capital",
                    event_status=status,
                )
        return list(events.values())

    def _parse_listing_page(
        self,
        channel: SourceChannel,
        *,
        html: bytes,
        page_url: str,
        page_number: int,
        context: AdapterContext,
        discovered_at: str,
        window_start,
        window_end,
    ) -> tuple[list[SourceArticleIndex], dict[str, object]]:
        adaptive = AdaptiveSelector(
            html,
            url=page_url,
            storage_path=context.adaptive_db,
        )
        item_selection = adaptive.css(
            "div#当前栏目_list div.page-content > ul > li.cf",
            identifier=f"{channel.source_id}:listing-item",
            minimum_count=1,
            maximum_count=_PAGE_SIZE,
        )
        pagination_selection = adaptive.css(
            "div#当前栏目_list > div.pagination",
            identifier=f"{channel.source_id}:listing-pagination",
            minimum_count=1,
            maximum_count=1,
        )
        if not item_selection.elements or not pagination_selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} page {page_number} selector failed closed"
            )
        metadata = self._pagination_metadata(
            channel.source_id,
            pagination_selection.elements[0],
            page_number,
        )

        output: list[SourceArticleIndex] = []
        previous_date = None
        for page_position, item in enumerate(item_selection.elements, start=1):
            links = tuple(item.css("a.fl"))
            dates = tuple(item.css("span.fr"))
            if len(links) != 1 or len(dates) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} page {page_number} item "
                    f"{page_position} title/date cardinality failed"
                )
            link = links[0]
            title = self.clean_text(
                str(link.attrib.get("title") or "")
                or link.get_all_text(separator=" ", strip=True)
            )
            canonical_url = self._canonical_url(
                urljoin(page_url, str(link.attrib.get("href") or ""))
            )
            article_id = self._article_id(canonical_url)
            if not article_id:
                raise ListingInvariantError(
                    f"{channel.source_id} rejected article URL {canonical_url}"
                )
            published_at = self.clean_text(
                dates[0].get_all_text(separator=" ", strip=True)
            )
            published_date = self._valid_date(published_at)
            if published_date is None:
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} has invalid date"
                )
            if published_date > self._source_today(context.now):
                raise ListingInvariantError(
                    f"{channel.source_id} article {article_id} is future dated"
                )
            if previous_date is not None and published_date > previous_date:
                raise ListingInvariantError(
                    f"{channel.source_id} page {page_number} is not "
                    f"newest-first at position {page_position}"
                )
            previous_date = published_date
            structured = {
                "page": page_number,
                "page_position": page_position,
                "archive_total_count": metadata["total_count"],
                "archive_page_count": metadata["total_pages"],
                "closed_window_start": window_start.isoformat(),
                "closed_window_end": window_end.isoformat(),
                "company": "工业和信息化部",
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
                    channel="science-files",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published_at,
                    discovered_at=discovered_at,
                    cursor_value=f"{published_at}|{article_id}",
                    listing_page=page_url,
                    listing_position=page_position,
                    content_hash=content_hash,
                    discovery_method=item_selection.method,
                    structured_data=structured,
                )
            )
        return output, metadata

    @staticmethod
    def _pagination_metadata(
        source_id: str,
        element: object,
        page_number: int,
    ) -> dict[str, object]:
        try:
            rows = int(element.attrib.get("rows") or "")
            count = int(element.attrib.get("count") or "")
        except (TypeError, ValueError) as exc:
            raise ListingInvariantError(
                f"{source_id} pagination count metadata invalid"
            ) from exc
        if rows != _PAGE_SIZE or count < 1:
            raise ListingInvariantError(
                f"{source_id} pagination rows/count invariant failed"
            )
        total_pages = math.ceil(count / rows)
        if not 1 <= total_pages <= _MAX_PAGES:
            raise ListingInvariantError(
                f"{source_id} pagination page count {total_pages} invalid"
            )
        current_labels = [
            re.sub(
                r"\s+",
                " ",
                item.get_all_text(separator=" ", strip=True),
            ).strip()
            for item in element.css("span.layui-laypage-curr > em")
        ]
        current_labels = [label for label in current_labels if label]
        page_label = str(element.attrib.get("pageno") or "")
        if page_label != str(page_number):
            raise ListingInvariantError(
                f"{source_id} pagination page-number attribute mismatch"
            )
        if current_labels and current_labels[-1] != str(page_number):
            raise ListingInvariantError(
                f"{source_id} pagination current-page marker mismatch"
            )
        unit_url = str(element.attrib.get("uniturl") or "")
        if unit_url != _PAGINATION_PATH:
            raise ListingInvariantError(
                f"{source_id} pagination endpoint rejected"
            )
        raw_query = str(element.attrib.get("querydata") or "")
        try:
            query_data = ast.literal_eval(raw_query)
        except (SyntaxError, ValueError) as exc:
            raise ListingInvariantError(
                f"{source_id} pagination query metadata invalid"
            ) from exc
        if (
            not isinstance(query_data, dict)
            or query_data.get("parseType") != "buildstatic"
            or query_data.get("pageType") != "column"
            or query_data.get("tagId") != "当前栏目_list"
            or not all(
                query_data.get(key)
                for key in ("webId", "pageId", "tplSetId")
            )
        ):
            raise ListingInvariantError(
                f"{source_id} pagination query invariant failed"
            )
        return {
            "rows": rows,
            "total_count": count,
            "total_pages": total_pages,
            "unit_url": unit_url,
            "query_data": query_data,
        }

    @staticmethod
    def _validate_page_metadata(
        source_id: str,
        metadata: dict[str, object],
        first: dict[str, object],
        page_number: int,
        actual_count: int,
    ) -> None:
        for key in ("rows", "total_count", "total_pages", "unit_url"):
            if metadata[key] != first[key]:
                raise ListingInvariantError(
                    f"{source_id} page {page_number} pagination {key} changed"
                )
        rows = int(first["rows"])
        total_count = int(first["total_count"])
        total_pages = int(first["total_pages"])
        expected_count = (
            rows
            if page_number < total_pages
            else total_count - rows * (total_pages - 1)
        )
        if actual_count != expected_count:
            raise ListingInvariantError(
                f"{source_id} page {page_number} row count {actual_count} "
                f"does not match expected {expected_count}"
            )

    @staticmethod
    def _pagination_url(
        channel: SourceChannel,
        metadata: dict[str, object],
        page_number: int,
    ) -> str:
        if not 2 <= page_number <= int(metadata["total_pages"]):
            raise ListingInvariantError(
                f"{channel.source_id} requested invalid page {page_number}"
            )
        query_data = dict(metadata["query_data"])
        query_data["paramJson"] = json.dumps(
            {"pageNo": page_number, "pageSize": str(metadata["rows"])},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        base = urljoin(channel.url, str(metadata["unit_url"]))
        return f"{base}?{urlencode(query_data)}"

    @staticmethod
    def _api_fragment(
        source_id: str,
        payload: bytes,
        page_number: int,
    ) -> bytes:
        try:
            parsed = json.loads(payload.decode("utf-8"))
            fragment = parsed["data"]["html"]
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ListingInvariantError(
                f"{source_id} page {page_number} API response invalid"
            ) from exc
        if parsed.get("success") is not True or str(parsed.get("code")) != "200":
            raise ListingInvariantError(
                f"{source_id} page {page_number} API status invalid"
            )
        if not isinstance(fragment, str) or "当前栏目_list" not in fragment:
            raise ListingInvariantError(
                f"{source_id} page {page_number} API HTML missing"
            )
        return fragment.encode("utf-8")

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() not in {
            "www.miit.gov.cn",
            "wap.miit.gov.cn",
        }:
            return url
        match = _ARTICLE_PATH.fullmatch(parsed.path)
        if not match:
            return url
        return (
            "https://www.miit.gov.cn/jgsj/kjs/wjfb/art/"
            f"{match.group('year')}/art_{match.group('id')}.html"
        )

    @staticmethod
    def _article_id(url: str) -> str:
        match = _ARTICLE_PATH.fullmatch(urlparse(url).path)
        return match.group("id") if match else ""

    @staticmethod
    def _valid_date(value: str):
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
            return None
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None

    @staticmethod
    def _detail_date(value: str):
        match = re.fullmatch(
            r"发布时间[：:]\s*(20\d{2}-\d{2}-\d{2})"
            r"(?:\s+\d{2}:\d{2})?",
            value,
        )
        if not match:
            return None
        try:
            return datetime.fromisoformat(match.group(1)).date()
        except ValueError:
            return None

    @staticmethod
    def _date(value: str):
        return datetime.fromisoformat(value[:10]).date()

    @staticmethod
    def _source_today(now: datetime):
        if now.tzinfo is None:
            return now.date()
        return now.astimezone(ZoneInfo("Asia/Shanghai")).date()

    @classmethod
    def _clean_body(cls, element: object) -> str:
        blocks: list[str] = []
        seen: set[str] = set()
        for block in element.css("p, h2, h3, li, table"):
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
    def _meta(selector: object, name: str) -> str:
        elements = tuple(selector.css(f"meta[name='{name}']"))
        if len(elements) != 1:
            return ""
        return str(elements[0].attrib.get("content") or "").strip()

    @staticmethod
    def _source_department(selector: object) -> str:
        for element in selector.css("div.cinfo.center > span"):
            text = re.sub(
                r"\s+",
                " ",
                element.get_all_text(separator=" ", strip=True),
            ).strip()
            match = re.fullmatch(r"来源[：:]\s*(.+)", text)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _source_tags(value: str) -> tuple[str, ...]:
        labels = (
            "人工智能",
            "机器人",
            "具身智能",
            "半导体",
            "脑机接口",
            "商业航天",
            "核聚变",
            "标准",
            "揭榜挂帅",
            "物联网",
        )
        aliases = {
            "商业航天": r"航天|卫星|火箭",
            "核聚变": r"核聚变|托卡马克",
        }
        return tuple(
            label
            for label in labels
            if re.search(aliases.get(label, re.escape(label)), value)
        )

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s|丨｜​]+", "", expected)
        right = re.sub(r"[\s|丨｜​]+", "", actual)
        return bool(left and right and (left == right or left in right or right in left))

    @staticmethod
    def _resolve_authority(
        _article: CleanArticle,
        _sentence: str,
        _event_type: str,
    ) -> tuple[str, tuple[str, ...]]:
        return "工业和信息化部", ("工业和信息化部",)

    @staticmethod
    def _current_policy_quote(quote: str) -> bool:
        return bool(
            quote
            and _CURRENT_POLICY_QUOTE.search(quote)
            and not _NON_CURRENT_POLICY_QUOTE.search(quote)
        )

    @staticmethod
    def _industry_tags(article: CleanArticle) -> tuple[str, ...]:
        text = f"{article.index.title} {article.clean_body}"
        return tuple(
            tag for tag, pattern in _INDUSTRY_PATTERNS if pattern.search(text)
        ) or ("other",)

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


__all__ = ["MiitAdapter"]
