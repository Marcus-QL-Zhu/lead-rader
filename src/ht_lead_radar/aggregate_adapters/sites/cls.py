"""Dedicated adapter for the public CLS 7x24 telegraph stream."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from hashlib import md5, sha1, sha256
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
from ..cls_rules import (
    cls_company_from_title,
    extract_cls_supplemental_events,
    merge_cls_events,
)
from ..industry_rules import IndustryRuleConfig, extract_media_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_CHINA = ZoneInfo("Asia/Shanghai")
_API_PATH = "https://www.cls.cn/v1/roll/get_roll_list"
_PAGE_SIZE = 20
_MAX_PAGES = 120
_DETAIL_PATH = re.compile(r"/detail/(\d+)")
_BRACKET_TITLE = re.compile(r"^[【〖\[]([^】〗\]]{4,180})[】〗\]]")
_ACCESS_CONTROL = re.compile(
    r"验证码|访问过于频繁|请登录后|无权访问|forbidden|captcha",
    re.I,
)
_EVENT_WORD = re.compile(
    r"任命|获任|出任|履新|上任|换帅|接任|辞任|离任|扩产|投产|"
    r"新建|生产基地|工厂开工|招标|采购公告|中标|获得.{0,12}订单|"
    r"签订.{0,12}合同|战略合作|联合研发|获得定点|通过验收|完成交付|"
    r"首批交付|成立|落户|研发中心|区域总部|获批|批准上市|临床试验|"
    r"收购|并购|IPO|上市申请|提交招股书|辅导备案|发布|首次|实现|"
    r"收到.{0,60}采购订单|签署.{0,60}合同|投资.{0,80}建设|"
    r"展开.{0,60}合作|(?:获|获得).{0,60}支持|取得.{0,60}注册证|"
    r"获核准|追加投资|预计.{0,12}量产|样品.{0,12}交付|"
    r"量产出货|扩充产能|长期供货协议|现已.{0,12}发布"
)
_INVALID_COMPANY = re.compile(
    r"^(?:财联社|据悉|公告|消息|行业|市场|报告|机构|公司|企业)|"
    r"(?:行业|市场|报告|项目|产品|政策|标准|名单)$"
)


class ClsAdapter(AggregateAdapter):
    """Enumerate one completed China-time day through CLS's public page XHR."""

    adapter_id = "cls"
    channels = (
        SourceChannel(
            source_id="cls-telegraph",
            name="财联社—电报",
            url="https://www.cls.cn/telegraph",
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
                "merger_acquisition",
                "ipo_or_listing",
                "technical_milestone",
            ),
            allowed_hosts=("www.cls.cn",),
            allowed_path_patterns=(r"/detail/\d+",),
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
                f"{channel.source_id} landing page access control detected"
            )
        target_day = context.now.astimezone(_CHINA).date() - timedelta(days=1)
        start = int(datetime.combine(target_day, time.min, tzinfo=_CHINA).timestamp())
        end = int(datetime.combine(target_day, time.max, tzinfo=_CHINA).timestamp())
        cursor = end
        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        terminated = False

        for page_number in range(1, _MAX_PAGES + 1):
            request_url = self._api_url(cursor)
            payload = self._api_payload(
                context.fetch(request_url),
                channel,
                page_number,
            )
            if not payload:
                raise ListingInvariantError(
                    f"{channel.source_id} empty page before closed-day boundary "
                    f"at page {page_number}"
                )
            timestamps = [self._timestamp(item) for item in payload]
            if any(
                value <= 0 or value >= cursor for value in timestamps
            ) or timestamps != sorted(timestamps, reverse=True):
                raise ListingInvariantError(
                    f"{channel.source_id} invalid/non-decreasing cursor page "
                    f"{page_number}"
                )
            for position_in_page, item in enumerate(payload, start=1):
                timestamp = timestamps[position_in_page - 1]
                if timestamp < start:
                    terminated = True
                    continue
                if timestamp > end:
                    raise ListingInvariantError(
                        f"{channel.source_id} API returned item beyond request window"
                    )
                article_id = str(item.get("id") or "").strip()
                if not article_id.isdigit() or article_id in seen:
                    raise ListingInvariantError(
                        f"{channel.source_id} duplicate/invalid id {article_id!r}"
                    )
                canonical_url = f"https://www.cls.cn/detail/{article_id}"
                content = self.clean_text(str(item.get("content") or ""))
                title = self._item_title(item, content)
                published = datetime.fromtimestamp(timestamp, _CHINA).isoformat()
                structured = {
                    "api_page": page_number,
                    "api_request_cursor": cursor,
                    "api_position": position_in_page,
                    "api_endpoint": _API_PATH,
                    "level": str(item.get("level") or ""),
                    "company": self._company_from_title(title),
                    "subjects": tuple(
                        str(subject.get("subject_name") or "")
                        for subject in item.get("subjects") or ()
                        if isinstance(subject, dict)
                        and str(subject.get("subject_name") or "").strip()
                    ),
                }
                output.append(
                    SourceArticleIndex(
                        source_id=channel.source_id,
                        source_article_id=article_id,
                        channel="telegraph",
                        canonical_url=canonical_url,
                        title=title,
                        published_at=published,
                        discovered_at=discovered_at,
                        cursor_value=f"{timestamp}|{article_id}",
                        listing_page=request_url,
                        listing_position=len(output) + 1,
                        content_hash=self.stable_hash(
                            f"{canonical_url}\n{title}\n{content}\n{timestamp}"
                        ),
                        discovery_method="xhr:cls-v1-roll",
                        summary=content,
                        structured_data=structured,
                    )
                )
                seen.add(article_id)
            oldest = timestamps[-1]
            if oldest < start:
                terminated = True
                break
            cursor = oldest

        if not terminated:
            raise ListingInvariantError(
                f"{channel.source_id} pagination did not cross closed-day boundary"
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
                f"{channel.source_id} detail access control detected for "
                f"{index.source_article_id}"
            )
        data = self._next_article(text)
        extraction_method = "embedded-json"
        similarity = None
        author = ""
        if data:
            if str(data.get("id") or "") != index.source_article_id:
                raise DetailFetchError(
                    f"{channel.source_id} detail id mismatch for "
                    f"{index.source_article_id}"
                )
            title = self.clean_text(str(data.get("title") or ""))
            body = self.clean_text(str(data.get("content") or data.get("brief") or ""))
            timestamp = self._integer(data.get("ctime"))
            author_data = data.get("author")
            if isinstance(author_data, dict):
                author = self.clean_text(
                    str(author_data.get("name") or author_data.get("nickname") or "")
                )
            elif isinstance(author_data, str):
                author = self.clean_text(author_data)
        else:
            adaptive = AdaptiveSelector(
                html,
                url=index.canonical_url,
                storage_path=context.adaptive_db,
            )
            selected = adaptive.css(
                "div.telegraph-detail-body",
                identifier=f"{channel.source_id}:detail-body",
                minimum_count=1,
                maximum_count=1,
            )
            if not selected.elements:
                raise DetailFetchError(
                    f"{channel.source_id} detail selector failed for "
                    f"{index.source_article_id}"
                )
            body = self.clean_text(
                selected.elements[0].get_all_text(separator=" ", strip=True)
            )
            title = self._first_text(adaptive.selector, "h1")
            timestamp = self._detail_timestamp(adaptive.selector)
            extraction_method = selected.method
            similarity = selected.similarity_threshold

        if not self._titles_match(index.title, title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        if len(body) < 20:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for "
                f"{index.source_article_id}"
            )
        if not timestamp:
            raise DetailFetchError(
                f"{channel.source_id} detail timestamp missing for "
                f"{index.source_article_id}"
            )
        detail_date = datetime.fromtimestamp(timestamp, _CHINA).date().isoformat()
        if detail_date != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}"
            )
        if index.summary and not self._body_matches_summary(index.summary, body):
            raise DetailFetchError(
                f"{channel.source_id} detail body mismatch for "
                f"{index.source_article_id}"
            )
        digest = sha256(f"{index.title}\n{body}".encode()).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            tags=tuple(
                str(value) for value in index.structured_data.get("subjects", ())
            ),
            structured_data={
                **index.structured_data,
                "detail_published_at": datetime.fromtimestamp(
                    timestamp, _CHINA
                ).isoformat(),
            },
            extraction_method=extraction_method,
            adaptive_similarity=similarity,
            evidence_locators={
                "title": "detail:__NEXT_DATA__.articleDetail.title / h1",
                "published_at": "detail:__NEXT_DATA__.articleDetail.ctime / time",
                "body": (
                    "detail:__NEXT_DATA__.articleDetail.content / "
                    "div.telegraph-detail-body"
                ),
                "company": "listing:title-prefix",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        baseline = extract_media_events(
            channel,
            article,
            config=IndustryRuleConfig(
                processor="rules:cls-v1",
                company_resolver=self._company_for_event,
            ),
            funding_processor="rules:cls-funding-v1",
        )
        supplemental = extract_cls_supplemental_events(channel, article)
        return merge_cls_events(baseline, supplemental)

    def should_fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
    ) -> bool:
        del channel
        # The public roll API supplies the complete telegraph text in
        # summary. Index every item, but spend detail/LLM budget only on
        # items containing one of the deliberately broad recruitment-leading
        # operational cues. This is a routing decision, never evidence.
        text = " ".join(
            (
                index.title,
                index.summary,
                *(str(value) for value in index.structured_data.get("subjects", ())),
            )
        )
        return bool(_EVENT_WORD.search(text))

    @staticmethod
    def _api_url(cursor: int) -> str:
        params = {
            "app": "CailianpressWeb",
            "last_time": cursor,
            "os": "web",
            "refresh_type": 1,
            "rn": _PAGE_SIZE,
            "sv": "8.7.9",
        }
        encoded = urlencode(sorted(params.items()))
        signature = md5(sha1(encoded.encode()).hexdigest().encode()).hexdigest()
        return f"{_API_PATH}?{encoded}&sign={signature}"

    @staticmethod
    def _api_payload(
        raw: bytes,
        channel: SourceChannel,
        page_number: int,
    ) -> list[dict[str, Any]]:
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ListingInvariantError(
                f"{channel.source_id} invalid API JSON at page {page_number}"
            ) from exc
        if not isinstance(document, dict) or str(document.get("errno")) != "0":
            raise ListingInvariantError(
                f"{channel.source_id} API failure at page {page_number}"
            )
        data = document.get("data")
        items = data.get("roll_data") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) > _PAGE_SIZE:
            raise ListingInvariantError(
                f"{channel.source_id} invalid API page size at page {page_number}"
            )
        if not all(isinstance(item, dict) for item in items):
            raise ListingInvariantError(
                f"{channel.source_id} invalid API item at page {page_number}"
            )
        return items

    @staticmethod
    def _timestamp(item: dict[str, Any]) -> int:
        return ClsAdapter._integer(item.get("ctime"))

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _item_title(cls, item: dict[str, Any], content: str) -> str:
        title = cls.clean_text(str(item.get("title") or ""))
        if not title:
            match = _BRACKET_TITLE.match(content)
            title = match.group(1).strip() if match else content[:120].strip()
        if len(title) < 4:
            raise ListingInvariantError("cls-telegraph item title is too short")
        return title

    @staticmethod
    def _next_article(text: str) -> dict[str, Any]:
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            text,
            re.S,
        )
        if not match:
            return {}
        try:
            document = json.loads(match.group(1))
            detail = document["props"]["pageProps"]["articleDetail"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return {}
        return detail if isinstance(detail, dict) else {}

    @classmethod
    def _company_from_title(cls, title: str) -> str:
        supplemental = cls_company_from_title(title)
        if supplemental:
            return supplemental
        candidate = re.split(r"[：:]", title, maxsplit=1)[0]
        candidate = _BRACKET_TITLE.sub(lambda match: match.group(1), candidate)
        candidate = cls.clean_text(candidate).strip("【】〖〗[]")
        event = _EVENT_WORD.search(candidate)
        if event:
            candidate = re.sub(
                r"(?:拟|计划|将|宣布|公告称)$",
                "",
                candidate[: event.start()],
            ).strip()
        return (
            candidate
            if 2 <= len(candidate) <= 50 and not _INVALID_COMPANY.search(candidate)
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
        match = _EVENT_WORD.search(sentence)
        prefix = sentence[: match.start()] if match else ""
        prefix = re.split(r"[。！？；：:,]", prefix)[-1]
        prefix = re.sub(
            r"^(?:【[^】]+】)?(?:财联社\d{1,2}月\d{1,2}日电，?)?",
            "",
            prefix,
        ).strip(" “ ”「」")
        prefix = re.sub(r"(?:拟|计划|将|宣布|公告称)$", "", prefix).strip()
        if 2 <= len(prefix) <= 50 and not _INVALID_COMPANY.search(prefix):
            return prefix, (prefix,)
        return "", ()

    @staticmethod
    def _detail_timestamp(selector: object) -> int:
        nodes = tuple(selector.css("time[data-ctime]"))
        if nodes:
            return ClsAdapter._integer(nodes[0].attrib.get("data-ctime"))
        return 0

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
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"\s+", "", expected)
        right = re.sub(r"\s+", "", actual)
        return bool(
            left and right and (left == right or left in right or right in left)
        )

    @staticmethod
    def _body_matches_summary(summary: str, body: str) -> bool:
        left = re.sub(r"\s+", "", summary)
        right = re.sub(r"\s+", "", body)
        if not left or not right:
            return False
        prefix = left[: min(24, len(left))]
        return prefix in right or right[: min(24, len(right))] in left


__all__ = ["ClsAdapter"]
