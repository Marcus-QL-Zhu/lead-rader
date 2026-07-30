"""Dedicated adapter for the public Securities Times flash stream."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
from typing import Any
from urllib.parse import urlencode
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
_API_PATH = "https://ewap.stcn.com/api/transform"
_API_OPERATION = "news-fast_info_list"
_PAGE_SIZE = 20
_MAX_PAGES = 120
_DETAIL_PATH = re.compile(r"/article/detail/(\d+)\.html")
_ACCESS_CONTROL = re.compile(
    r"访问过于频繁|请登录后(?:查看|继续)|无权访问|access denied|"
    r"forbidden|challenge-platform|captcha-challenge",
    re.I,
)
_EVENT_WORD = re.compile(
    r"任命|获任|出任|履新|上任|换帅|接任|辞任|离任|扩产|投产|产能|"
    r"新建|生产基地|工厂开工|招标|采购公告|采购项目|中标|"
    r"获得.{0,20}订单|收到.{0,60}采购订单|签订.{0,60}合同|"
    r"战略合作|签署.{0,36}合作|展开.{0,36}合作|联合研发|获得定点|"
    r"通过验收|完成交付|首批交付|客户验证|成立|落户|研发中心|"
    r"区域总部|(?:获|获得).{0,48}支持|获批|核准|批准上市|"
    r"临床试验|(?:收到|取得).{0,60}(?:注册证|受理通知书)|"
    r"追加投资|投资.{0,80}建设|收购|并购|取得.{0,20}控股权|"
    r"资产重组|IPO|上市申请|提交招股书|辅导备案|挂牌上市|"
    r"发布|首款|首次|实现.{0,16}(?:量产|突破|验证)|"
    r"预计.{0,20}量产|样品.{0,20}交付|量产出货|扩充产能|"
    r"长期供货协议|现已.{0,20}发布"
)
_PURE_MARKET_NOISE = re.compile(
    r"指数|股指|期货|现货黄金|现货白银|原油|美元指数|汇率|离岸人民币|"
    r"沪指|深证成指|创业板指|科创50|北证50|日经225|KOSPI|恒生指数|"
    r"美股三大指数|A股三大指数|两市|沪深两市|港股|欧股|日韩股市|"
    r"亚太股市|国债收益率|国债期货|SHIBOR|LPR|融资余额|涨停|跌停|"
    r"收涨|收跌|高开|低开|盘中|收盘|开盘|涨超|跌超|"
    r"涨幅|跌幅|刷新历史新高"
)
_INVALID_COMPANY = re.compile(
    r"^(?:人民财讯|证券时报|据悉|公告|消息|行业|市场|报告|机构|"
    r"公司|企业|全资子公司|控股子公司|相关公司)|"
    r"(?:行业|市场|报告|项目|产品|政策|标准|名单|动态|观察|统计)$"
)
_STOCK_CODE = re.compile(r"\s*[（(]\d{5,6}(?:\.[A-Z]{2})?[）)]\s*", re.I)
_DIGEST_SUBJECT = re.compile(
    r"(?:^|】|\s)(?P<company>\*?ST[\u4e00-\u9fffA-Za-z0-9]{1,20}|"
    r"[\u4e00-\u9fffA-Za-z0-9]{2,30})[：:]"
)
_DIGEST_SUPPLEMENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "merger_acquisition",
        re.compile(
            r"拟(?:以.{0,24})?购买.{0,50}(?:股权|股份)|"
            r"拟公开挂牌转让.{0,40}全部股权"
        ),
    ),
    ("major_order", re.compile(r"签署.{0,30}合同")),
    (
        "factory_or_capacity",
        re.compile(r"拟.{0,20}投建.{0,50}(?:项目|基地)|新建\d+艘.{0,24}船"),
    ),
    (
        "new_site_or_entity",
        re.compile(r"拟.{0,24}设立.{0,16}(?:控股子公司|子公司|合资公司)"),
    ),
)
_DIGEST_PHASES = {
    "merger_acquisition": "strategy_capital",
    "major_order": "scale_delivery",
    "factory_or_capacity": "build_organize",
    "new_site_or_entity": "build_organize",
}


class StcnAdapter(AggregateAdapter):
    """Enumerate the previous completed China-time day through STCN's public API."""

    adapter_id = "stcn"
    channels = (
        SourceChannel(
            source_id="stcn-flash",
            name="证券时报—人民财讯",
            url="https://www.stcn.com/article/list/kx.html",
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
            allowed_hosts=("www.stcn.com",),
            allowed_path_patterns=(r"/article/detail/\d+\.html",),
        ),
    )
    minimum_listing_count = 1
    maximum_listing_count = 2_000

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        landing = html.decode("utf-8", errors="replace")
        if _ACCESS_CONTROL.search(landing):
            raise ListingInvariantError(
                f"{channel.source_id} landing page access control detected; no bypass"
            )
        adaptive = AdaptiveSelector(
            html,
            url=channel.url,
            storage_path=context.adaptive_db,
        )
        endpoint_selection = adaptive.css(
            "ul.infinite-list[data-url]",
            identifier=f"{channel.source_id}:listing-endpoint",
            minimum_count=1,
            maximum_count=1,
        )
        if not endpoint_selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} listing endpoint selector failed"
            )
        legacy_endpoint = str(
            endpoint_selection.elements[0].attrib.get("data-url") or ""
        ).strip()
        if legacy_endpoint != "/article/list.html?type=kx":
            raise ListingInvariantError(
                f"{channel.source_id} unexpected public listing endpoint "
                f"{legacy_endpoint!r}"
            )

        target_day = context.now.astimezone(_CHINA).date() - timedelta(days=1)
        target = target_day.isoformat()
        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        max_id = 1
        first_id = -1
        previous_oldest = 0
        expected_first_id = 0
        terminated = False

        for page_number in range(1, _MAX_PAGES + 1):
            request_url = self._api_url(
                page=page_number,
                max_id=max_id,
                first_id=first_id,
                target_day=target,
            )
            document, payload = self._api_payload(
                context.fetch(request_url),
                channel,
                page_number,
            )
            if not payload:
                self._validate_terminal_page(
                    document,
                    channel,
                    page_number,
                    max_id=max_id,
                    first_id=first_id,
                )
                terminated = True
                break

            timestamps = [self._integer(item.get("time")) for item in payload]
            if (
                any(value <= 0 for value in timestamps)
                or timestamps != sorted(timestamps, reverse=True)
                or (
                    previous_oldest
                    and timestamps[0] >= previous_oldest
                )
            ):
                raise ListingInvariantError(
                    f"{channel.source_id} invalid/non-decreasing API page "
                    f"{page_number}"
                )
            returned_max = self._integer(document.get("max_id"))
            returned_first = self._integer(document.get("first_id"))
            if returned_max != timestamps[-1] or returned_max >= max_id > 1:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid max_id cursor at page "
                    f"{page_number}"
                )
            if page_number == 1:
                expected_first_id = timestamps[0]
            if returned_first != expected_first_id:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid first_id cursor at page "
                    f"{page_number}"
                )

            for page_position, item in enumerate(payload, start=1):
                timestamp = timestamps[page_position - 1]
                published = datetime.fromtimestamp(timestamp, _CHINA)
                if published.date() != target_day:
                    raise ListingInvariantError(
                        f"{channel.source_id} API returned item outside "
                        f"closed day at page {page_number}"
                    )
                article_id = str(item.get("item_id") or "").strip()
                if not article_id.isdigit() or article_id in seen:
                    raise ListingInvariantError(
                        f"{channel.source_id} duplicate/invalid id {article_id!r}"
                    )
                if (
                    str(item.get("jump_type") or "") != "fast_info"
                    or str(item.get("jump_index") or "") != article_id
                ):
                    raise ListingInvariantError(
                        f"{channel.source_id} invalid detail routing for "
                        f"{article_id}"
                    )
                title = self.clean_text(str(item.get("wap_title") or ""))
                summary = self.clean_text(str(item.get("wap_content") or ""))
                if len(summary) < 10:
                    raise ListingInvariantError(
                        f"{channel.source_id} API summary too short for "
                        f"{article_id}"
                    )
                canonical_url = (
                    f"https://www.stcn.com/article/detail/{article_id}.html"
                )
                tags = self._tags(item)
                stocks = self._stocks(item)
                structured = {
                    "api_endpoint": _API_PATH,
                    "api_operation": _API_OPERATION,
                    "api_page": page_number,
                    "api_position": page_position,
                    "api_max_id": returned_max,
                    "api_first_id": returned_first,
                    "closed_day": target,
                    "landing_selector_method": endpoint_selection.method,
                    "is_red": int(bool(item.get("is_red"))),
                    "style_type": str(item.get("style_type") or ""),
                    "tags": tags,
                    "stocks": stocks,
                    "company": self._company_from_title(title),
                }
                published_at = published.isoformat()
                output.append(
                    SourceArticleIndex(
                        source_id=channel.source_id,
                        source_article_id=article_id,
                        channel="people-finance-flash",
                        canonical_url=canonical_url,
                        title=title,
                        published_at=published_at,
                        discovered_at=discovered_at,
                        cursor_value=f"{timestamp}|{article_id}",
                        listing_page=request_url,
                        listing_position=len(output) + 1,
                        content_hash=self.stable_hash(
                            "\n".join(
                                (
                                    canonical_url,
                                    title,
                                    summary,
                                    published_at,
                                    repr(tags),
                                    repr(stocks),
                                )
                            )
                        ),
                        discovery_method=(
                            "xhr:stcn-mobile-transform:"
                            f"{endpoint_selection.method}"
                        ),
                        summary=summary,
                        structured_data=structured,
                    )
                )
                seen.add(article_id)
            previous_oldest = timestamps[-1]
            max_id = returned_max
            first_id = returned_first

        if not terminated:
            raise ListingInvariantError(
                f"{channel.source_id} pagination did not reach terminal page"
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
            return self._listing_fallback(
                index,
                reason="detail_access_control",
            )
        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        title_selection = adaptive.css(
            "div.detail-title",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        info_selection = adaptive.css(
            "div.detail-info",
            identifier=f"{channel.source_id}:detail-info",
            minimum_count=1,
            maximum_count=1,
        )
        body_selection = adaptive.css(
            "div.detail-content",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        id_selection = adaptive.css(
            "div.like-btn[data-id]",
            identifier=f"{channel.source_id}:detail-id",
            minimum_count=1,
            maximum_count=1,
        )
        if not all(
            (
                title_selection.elements,
                info_selection.elements,
                body_selection.elements,
                id_selection.elements,
            )
        ):
            raise DetailFetchError(
                f"{channel.source_id} detail selector failed for "
                f"{index.source_article_id}"
            )
        title = self.clean_text(
            title_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        info = self.clean_text(
            info_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        body = self.clean_text(
            body_selection.elements[0].get_all_text(separator=" ", strip=True)
        )
        detail_id = str(
            id_selection.elements[0].attrib.get("data-id") or ""
        ).strip()
        if detail_id != index.source_article_id:
            raise DetailFetchError(
                f"{channel.source_id} detail id mismatch for "
                f"{index.source_article_id}"
            )
        if not self._titles_match(index.title, title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        detail_minute = self._detail_minute(info)
        expected_minute = datetime.fromisoformat(index.published_at).astimezone(
            _CHINA
        ).strftime("%Y-%m-%d %H:%M")
        if detail_minute != expected_minute:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )
        if len(body) < 20:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for "
                f"{index.source_article_id}"
            )
        if not self._body_matches_summary(index.summary, body):
            raise DetailFetchError(
                f"{channel.source_id} detail body mismatch for "
                f"{index.source_article_id}"
            )
        methods = {
            title_selection.method,
            info_selection.method,
            body_selection.method,
            id_selection.method,
        }
        extraction_method = "adaptive" if "adaptive" in methods else "exact"
        similarity = 72 if extraction_method == "adaptive" else None
        author_match = re.search(r"作者[：:]\s*([^\s]+)", info)
        author = author_match.group(1).strip() if author_match else ""
        digest = sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            tags=tuple(
                str(value)
                for value in index.structured_data.get("tags", ())
                if str(value).strip()
            ),
            structured_data={
                **index.structured_data,
                "detail_published_minute": detail_minute,
            },
            extraction_method=extraction_method,
            adaptive_similarity=similarity,
            evidence_locators={
                "title": "detail:div.detail-title",
                "published_at": "detail:div.detail-info",
                "body": "detail:div.detail-content",
                "article_id": "detail:div.like-btn[data-id]",
                "company": "listing:wap_title-prefix",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        extraction_article = article
        if article.index.title.startswith("公告精选"):
            extraction_article = replace(
                article,
                index=replace(article.index, title="", summary=""),
            )
        events = extract_media_events(
            channel,
            extraction_article,
            config=IndustryRuleConfig(
                processor="rules:stcn-v1",
                company_resolver=self._company_for_event,
            ),
            funding_processor="rules:stcn-funding-v1",
        )
        if article.index.title.startswith("公告精选"):
            events.extend(self._digest_supplemental_events(channel, article))
        output: dict[tuple[str, str, str, str], SemanticEvent] = {}
        for event in events:
            key = (
                event.canonical_company,
                event.event_type,
                event.funding_round,
                event.event_status,
            )
            output.setdefault(key, event)
        return list(output.values())

    @classmethod
    def _digest_supplemental_events(
        cls,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        output: list[SemanticEvent] = []
        sentences = (
            value.strip()
            for value in re.split(r"(?<=[。！？；])", article.clean_body)
            if value.strip()
        )
        for sentence in sentences:
            subject = _DIGEST_SUBJECT.search(sentence)
            if not subject:
                continue
            company = subject.group("company")
            evidence = sentence[:500]
            for event_type, pattern in _DIGEST_SUPPLEMENTS:
                if not pattern.search(sentence):
                    continue
                output.append(
                    SemanticEvent(
                        source_id=channel.source_id,
                        source_article_id=article.index.source_article_id,
                        canonical_url=article.index.canonical_url,
                        company_mentions=(company,),
                        canonical_company=company,
                        event_type=event_type,
                        event_date=article.index.published_at[:10],
                        industry_tags=("other",),
                        event_summary=evidence[:300],
                        evidence_quotes=(evidence,),
                        confidence="high",
                        processor="rules:stcn-digest-v1",
                        content_hash=article.content_hash,
                        phase=_DIGEST_PHASES[event_type],
                        event_status=(
                            "started"
                            if re.search(r"拟|新建", sentence)
                            else "completed"
                        ),
                    )
                )
        return output

    def should_fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
    ) -> bool:
        del channel
        # The public API supplies the complete flash text and tags. Persist every
        # index, then spend detail/LLM budget only when both a broad operational
        # cue and a broad hard-tech cue are present. This is routing, not evidence.
        text = " ".join(
            (
                index.title,
                index.summary,
                *(str(value) for value in index.structured_data.get("tags", ())),
            )
        )
        if _PURE_MARKET_NOISE.search(index.title) and not _EVENT_WORD.search(text):
            return False
        return bool(_EVENT_WORD.search(text))

    @staticmethod
    def _api_url(
        *,
        page: int,
        max_id: int,
        first_id: int,
        target_day: str,
    ) -> str:
        other = {
            "type": "1",
            "max_id": max_id,
            "page": page,
            "first_id": first_id,
            "post_times": 1 if page == 1 else -1,
            "tab": "kx",
            "start_time": target_day,
            "end_time": target_day,
        }
        query = urlencode(
            {
                "path": _API_OPERATION,
                "other_param": json.dumps(
                    other,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
        return f"{_API_PATH}?{query}"

    @staticmethod
    def _api_payload(
        raw: bytes,
        channel: SourceChannel,
        page_number: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ListingInvariantError(
                f"{channel.source_id} invalid API JSON at page {page_number}"
            ) from exc
        if not isinstance(document, dict) or str(document.get("status")) != "1":
            raise ListingInvariantError(
                f"{channel.source_id} API failure at page {page_number}"
            )
        response_page = StcnAdapter._integer(document.get("page"))
        if response_page and response_page != page_number:
            raise ListingInvariantError(
                f"{channel.source_id} API page mismatch at page {page_number}"
            )
        items = document.get("data")
        if items is None:
            return document, []
        if not isinstance(items, list) or len(items) > _PAGE_SIZE:
            raise ListingInvariantError(
                f"{channel.source_id} invalid API page size at page {page_number}"
            )
        if not all(isinstance(item, dict) for item in items):
            raise ListingInvariantError(
                f"{channel.source_id} invalid API item at page {page_number}"
            )
        return document, items

    @staticmethod
    def _validate_terminal_page(
        document: dict[str, Any],
        channel: SourceChannel,
        page_number: int,
        *,
        max_id: int,
        first_id: int,
    ) -> None:
        returned_max = StcnAdapter._integer(document.get("max_id"))
        returned_first = StcnAdapter._integer(document.get("first_id"))
        if (
            page_number == 1
            or (returned_max and returned_max != max_id)
            or (returned_first and returned_first != first_id)
        ):
            raise ListingInvariantError(
                f"{channel.source_id} invalid terminal page {page_number}"
            )

    @staticmethod
    def _integer(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _tags(item: dict[str, Any]) -> tuple[str, ...]:
        raw = item.get("tags")
        if not isinstance(raw, list):
            return ()
        return tuple(
            dict.fromkeys(
                str(tag.get("tag_name") or "").strip()
                for tag in raw
                if isinstance(tag, dict)
                and str(tag.get("tag_name") or "").strip()
            )
        )

    @staticmethod
    def _stocks(item: dict[str, Any]) -> tuple[str, ...]:
        raw = item.get("stocks")
        if not isinstance(raw, list):
            return ()
        return tuple(
            dict.fromkeys(
                str(stock.get("stock_name") or "").strip()
                for stock in raw
                if isinstance(stock, dict)
                and str(stock.get("stock_name") or "").strip()
            )
        )

    @classmethod
    def _company_from_title(cls, title: str) -> str:
        candidate = re.split(r"[：:]", title, maxsplit=1)[0]
        event = _EVENT_WORD.search(candidate)
        if event:
            candidate = candidate[: event.start()]
        candidate = re.sub(r"^[【〖\[].*?[】〗\]]", "", candidate)
        candidate = re.sub(r"(?:拟|计划|将|宣布|公告称)$", "", candidate)
        candidate = _STOCK_CODE.sub("", candidate)
        candidate = cls.clean_text(candidate).strip("【】〖〗[] ，,")
        return (
            candidate
            if 2 <= len(candidate) <= 50
            and not _INVALID_COMPANY.search(candidate)
            else ""
        )

    @classmethod
    def _company_for_event(
        cls,
        article: CleanArticle,
        sentence: str,
        _event_type: str,
    ) -> tuple[str, tuple[str, ...]]:
        structured = cls.clean_text(
            str(article.index.structured_data.get("company") or "")
        )
        if structured and structured in sentence:
            return structured, (structured,)
        title = article.index.title
        if (
            not structured
            and sentence.startswith(title)
            and not _EVENT_WORD.search(title)
        ):
            sentence = sentence[len(title) :].lstrip()
        event = _EVENT_WORD.search(sentence)
        prefix = sentence[: event.start()] if event else ""
        colon_candidates = re.findall(r"([^。！？；：:]{2,70})[：:]", prefix)
        if colon_candidates:
            candidate = colon_candidates[-1]
        else:
            candidate = re.split(r"[。！？；，,]", prefix)[-1]
        candidate = re.sub(
            r"^(?:【[^】]+】)?(?:人民财讯\d{1,2}月\d{1,2}日电，?)?",
            "",
            candidate,
        )
        candidate = re.sub(r"(?:拟|计划|将|宣布|公告称)$", "", candidate)
        candidate = _STOCK_CODE.sub("", candidate)
        candidate = cls.clean_text(candidate).strip(" “ ”「」【】")
        if (
            2 <= len(candidate) <= 50
            and not _INVALID_COMPANY.search(candidate)
        ):
            return candidate, (candidate,)
        return "", ()

    @classmethod
    def _listing_fallback(
        cls,
        index: SourceArticleIndex,
        *,
        reason: str,
    ) -> CleanArticle:
        body = cls.clean_text(index.summary)
        if len(body) < 20:
            body = cls.clean_text(f"{index.title} {index.summary}")
        if len(body) < 20:
            raise DetailFetchError(
                f"{index.source_id} listing fallback too short for "
                f"{index.source_article_id}"
            )
        digest = sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            tags=tuple(
                str(value)
                for value in index.structured_data.get("tags", ())
                if str(value).strip()
            ),
            structured_data={
                **index.structured_data,
                "detail_fallback": reason,
            },
            extraction_method="listing-api-fallback",
            evidence_locators={
                "title": "listing:wap_title",
                "published_at": "listing:time",
                "body": "listing:wap_content",
                "company": "listing:wap_title-prefix",
            },
            fetch_status="listing_fallback",
            failure_reason=reason,
            content_hash=digest,
        )

    @staticmethod
    def _detail_minute(value: str) -> str:
        match = re.search(
            r"(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})",
            value,
        )
        return f"{match.group(1)} {match.group(2)}" if match else ""

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"[\s\u200b]+", "", expected)
        right = re.sub(r"[\s\u200b]+", "", actual)
        return bool(
            left and right and (left == right or left in right or right in left)
        )

    @staticmethod
    def _body_matches_summary(summary: str, body: str) -> bool:
        left = re.sub(r"[\s\u200b]+", "", summary)
        right = re.sub(r"[\s\u200b]+", "", body)
        if not left or not right:
            return False
        prefix = left[: min(36, len(left))]
        return prefix in right or right[: min(36, len(right))] in left


__all__ = ["StcnAdapter"]
