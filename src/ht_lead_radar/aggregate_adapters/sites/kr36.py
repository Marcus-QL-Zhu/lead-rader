"""Dedicated adapter for the public 36Kr financing-flash listing."""

from __future__ import annotations

from datetime import timedelta, timezone
from hashlib import sha256
import re
from urllib.parse import urljoin, urlparse

from ..adaptive import AdaptiveSelector
from ..body_scope import clean_semantic_body_scope
from ..base import (
    AdapterContext,
    AggregateAdapter,
    DetailFetchError,
    ListingInvariantError,
)
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_COMPLETED_ASSERTION = re.compile(
    r"(?:\u5b8c\u6210|\u83b7\u5f97|\u83b7(?!\u6089)|"
    r"\u65a9\u83b7|\u5ba3\u5e03\u5b8c\u6210|"
    r"\u5b98\u5ba3\u5b8c\u6210)"
    r".{0,120}\u878d\u8d44"
)
_STARTED_ASSERTION = re.compile(
    r"(?:\u542f\u52a8|\u5f00\u542f|\u5f00\u59cb)"
    r".{0,80}(?:\u878d\u8d44|[A-Z](?:\+{1,2})?\u8f6e)"
    r"|[A-Z](?:\+{1,2})?\u8f6e(?:\uff08[^\uff09]{0,24}\uff09)?"
    r".{0,20}(?:\u5df2)?(?:\u63d0\u524d)?"
    r"(?:\u5f00\u59cb|\u542f\u52a8|\u5f00\u542f)"
)
_FUNDING_ASSERTION = re.compile(
    rf"(?:{_COMPLETED_ASSERTION.pattern})|(?:{_STARTED_ASSERTION.pattern})"
)
_ROUND = re.compile(
    r"(Pre-IPO(?:\u8f6e)?|Pre-[A-Z](?:\+{1,2})?(?:\u8f6e)?|"
    r"(?<![A-Z])[A-Z](?:\+{1,2})?\u8f6e|"
    r"\u5929\u4f7f(?:\+{1,2})?\u8f6e|\u79cd\u5b50(?:\+{1,2})?\u8f6e|"
    r"\u6218\u7565\u878d\u8d44)"
)
_AMOUNT = re.compile(
    r"((?:\u8fd1|\u8d85|\u903e|\u6570)?\s*"
    r"\d+(?:\.\d+)?\s*(?:\u4e07|\u5343\u4e07|\u4ebf)\s*"
    r"(?:\u5143|\u7f8e\u5143|\u4eba\u6c11\u5e01)"
    r"|(?:\u8fd1|\u8d85|\u903e)?\s*"
    r"(?:\u6570\u5343\u4e07\u5143|\u6570\u4ebf\u5143|"
    r"\u5343\u4e07\u7ea7|\u4ebf\u5143\u7ea7|"
    r"\u5343\u4e07\u5143|\u4ebf\u5143))"
)
_QUOTED_COMPANY = re.compile(
    r'[\u300c\u300e\u201c"\u3010\u3016]'
    r'([^\u300d\u300f\u201d"\u3011\u3017]{2,40})'
    r'[\u300d\u300f\u201d"\u3011\u3017]'
)
_PREFIX_COMPANY = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9\u00b7\uff08\uff09()\- ]{2,40}?)"
    r"(?:\u5ba3\u5e03|\u5b98\u5ba3)?"
    r"(?:\u5b8c\u6210|\u83b7|\u65a9\u83b7)"
)
_LEGAL_COMPANY = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9\u00b7\uff08\uff09()]{4,60}"
    r"(?:\u6709\u9650\u8d23\u4efb\u516c\u53f8|"
    r"\u80a1\u4efd\u6709\u9650\u516c\u53f8|\u6709\u9650\u516c\u53f8))"
)

_INDUSTRY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "semiconductor",
        re.compile(r"芯片|半导体|晶圆|光刻|封装|激光器|IDM|集成电路", re.I),
    ),
    (
        "embodied_intelligence",
        re.compile(r"机器人|具身智能|灵巧手|触觉|运动控制|机器视觉", re.I),
    ),
    (
        "commercial_space",
        re.compile(r"航天|卫星|火箭|发射|太空|SpaceX", re.I),
    ),
    (
        "fusion_energy",
        re.compile(r"核聚变|聚变|超导|托卡马克|等离子体", re.I),
    ),
    (
        "brain_computer_interface",
        re.compile(r"脑机接口|神经意图|神经调控|类脑|SNN", re.I),
    ),
    (
        "advanced_manufacturing",
        re.compile(r"装备|材料|传感器|量产|产能|工业|制造|涂层", re.I),
    ),
    (
        "artificial_intelligence",
        re.compile(r"人工智能|大模型|AI\b|Agent|模型|数据", re.I),
    ),
    (
        "biotech",
        re.compile(r"医药|医疗|临床|药物|生物科技|诊断", re.I),
    ),
)


class Kr36Adapter(AggregateAdapter):
    adapter_id = "kr36"
    channels = (
        SourceChannel(
            source_id="36kr-financing-flash",
            name="36氪—融资快报",
            url="https://pitchhub.36kr.com/financing-flash",
            source_grade="B",
            event_prior=("funding",),
            allowed_hosts=("36kr.com",),
            allowed_path_patterns=(r"/p/\d+", r"/newsflashes/\d+"),
        ),
    )
    minimum_listing_count = 5
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
        selection = adaptive.css(
            "div.item-title",
            identifier=f"{channel.source_id}:listing-item-title",
            minimum_count=self.minimum_listing_count,
            maximum_count=self.maximum_listing_count,
        )
        if not selection.elements:
            raise ListingInvariantError(
                f"{channel.source_id} article-title selector failed"
            )
        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        discovered_at = context.now.replace(microsecond=0).isoformat()
        for position, title_box in enumerate(selection.elements, start=1):
            links = tuple(title_box.css("a.title"))
            if len(links) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} has {len(links)} title links"
                )
            link = links[0]
            href = str(link.attrib.get("href") or "").strip()
            canonical_url = self._canonical_url(urljoin(channel.url, href))
            article_id = urlparse(canonical_url).path.rstrip("/").split("/")[-1]
            if not article_id.isdigit():
                raise ListingInvariantError(
                    f"{channel.source_id} invalid article id from {canonical_url}"
                )
            if article_id in seen:
                raise ListingInvariantError(
                    f"{channel.source_id} duplicate article id {article_id}"
                )
            item = title_box.parent
            summary = self._first_text(item, "div.item-desc")
            time_label = self._first_text(item, "span.time")
            published_at = self._parse_time(time_label, context)
            item_type = self._first_text(title_box, "span.type")
            project = self._project_data(item)
            title = self.clean_text(link.get_all_text(separator=" ", strip=True))
            structured = {
                "item_type": item_type,
                "time_label": time_label,
                **project,
            }
            stable_structured = {
                key: value
                for key, value in structured.items()
                if key != "time_label"
            }
            content_hash = self.stable_hash(
                "\n".join(
                    (
                        canonical_url,
                        title,
                        summary,
                        repr(sorted(stable_structured.items())),
                    )
                )
            )
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="financing-flash",
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

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        adaptive = AdaptiveSelector(
            html,
            url=index.canonical_url,
            storage_path=context.adaptive_db,
        )
        is_flash = "/newsflashes/" in urlparse(index.canonical_url).path
        exact = "div.newsflash-item" if is_flash else "div.common-width.content"
        identifier = (
            f"{channel.source_id}:newsflash-body"
            if is_flash
            else f"{channel.source_id}:article-body"
        )
        selected = adaptive.css(
            exact,
            identifier=identifier,
            minimum_count=1,
            maximum_count=2,
        )
        body = ""
        if selected.elements:
            body = max(
                (
                    self.clean_text(
                        element.get_all_text(separator=" ", strip=True)
                    )
                    for element in selected.elements
                ),
                key=len,
            )
        if is_flash:
            descriptions = adaptive.selector.css('meta[name="description"]')
            if descriptions:
                meta_body = self.clean_text(
                    str(descriptions[0].attrib.get("content") or "")
                )
                if len(meta_body) >= 40:
                    body = meta_body
        body = self._remove_share_noise(body)
        captcha_page = b"TTGCaptcha" in html or b"verify_center" in html
        fetch_status = "ok"
        failure_reason = ""
        if len(body) < 40:
            body = self.clean_text(f"{index.title} {index.summary} {body}")
            event_complete = self._listing_event_complete(index)
            negative_complete = self._listing_negative_complete(index)
            fetch_status = (
                "structured_complete"
                if event_complete
                else "listing_complete"
                if negative_complete
                else "listing_fallback"
            )
            failure_reason = (
                "detail_captcha_structured_listing_used"
                if captcha_page and fetch_status == "structured_complete"
                else "detail_captcha_complete_negative_listing_used"
                if captcha_page and fetch_status == "listing_complete"
                else "detail_captcha"
                if captcha_page
                else "detail_too_short"
            )
        if len(body) < 20:
            raise DetailFetchError(
                f"{channel.source_id} detail and listing text too short "
                f"for {index.source_article_id}"
            )
        headings = adaptive.selector.css("h1")
        page_title = (
            self.clean_text(headings[0].get_all_text(separator=" ", strip=True))
            if headings
            else ""
        )
        title_present = (
            index.title == page_title
            or index.title in page_title
            or page_title in index.title
            or index.title[:18] in body
            or any(
                token in body
                for token in (
                    str(index.structured_data.get("company") or ""),
                    index.title[:8],
                )
                if token
            )
        )
        if not title_present:
            raise DetailFetchError(
                f"{channel.source_id} detail does not match listing title "
                f"{index.source_article_id}"
            )
        author = self._extract_author(adaptive, is_flash)
        structured = dict(index.structured_data)
        structured["detail_kind"] = "newsflash" if is_flash else "article"
        digest = sha256(
            f"{index.title}\n{body}".encode("utf-8")
        ).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            structured_data=structured,
            extraction_method=selected.method if selected.elements else "listing-fallback",
            adaptive_similarity=selected.similarity_threshold,
            evidence_locators={
                "title": "listing:div.item-title>a.title",
                "body": (
                    "detail:meta[name=description]"
                    if is_flash
                    else "detail:div.common-width.content"
                ),
                "company": "listing:div.project-card-wrp div.right-top div.title",
            },
            fetch_status=fetch_status,
            failure_reason=failure_reason,
            content_hash=digest,
        )

    def fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        context: AdapterContext,
    ) -> bytes:
        """Use 36Kr's content-bearing ``www`` route before the CAPTCHA route.

        The bare-domain article route can return ByteDance's interactive
        ``TTGCaptcha`` to server clients while the official ``www`` route
        returns the same public article. The stored canonical URL remains
        unchanged; this is only a bounded acquisition-path switch.
        """

        del channel
        parsed = urlparse(index.canonical_url)
        www_url = f"https://www.36kr.com{parsed.path}"
        decision = {
            "source_article_id": index.source_article_id,
            "primary_path": "html:www.36kr.com",
            "fallback_path": "html:36kr.com",
        }
        try:
            primary = context.fetch(www_url)
        except (OSError, ValueError) as exc:
            decision["primary_failure"] = f"{type(exc).__name__}: {exc}"
            try:
                fallback = context.fetch(index.canonical_url)
            except (OSError, ValueError) as fallback_exc:
                decision["fallback_failure"] = (
                    f"{type(fallback_exc).__name__}: {fallback_exc}"
                )
                decision["outcome"] = "both_routes_failed"
                self._record_fetch_decision(context, index, decision)
                raise
            else:
                decision["outcome"] = "bare_domain_after_www_error"
                self._record_fetch_decision(context, index, decision)
                return fallback
        if self._has_detail_payload(primary):
            decision["outcome"] = "www_accepted"
            self._record_fetch_decision(context, index, decision)
            return primary
        decision["primary_rejection"] = (
            "captcha" if self._is_captcha(primary) else "missing_detail_markers"
        )
        try:
            fallback = context.fetch(index.canonical_url)
        except (OSError, ValueError) as fallback_exc:
            decision["fallback_failure"] = (
                f"{type(fallback_exc).__name__}: {fallback_exc}"
            )
            decision["outcome"] = "bare_route_failed_after_www_rejection"
            self._record_fetch_decision(context, index, decision)
            raise
        decision["outcome"] = (
            "bare_domain_accepted"
            if not self._is_captcha(fallback)
            else "both_routes_captcha"
        )
        self._record_fetch_decision(context, index, decision)
        return fallback

    @staticmethod
    def _is_captcha(payload: bytes) -> bool:
        return b"TTGCaptcha" in payload or b"verify_center" in payload

    @classmethod
    def _has_detail_payload(cls, payload: bytes) -> bool:
        if cls._is_captcha(payload):
            return False
        return any(
            marker in payload
            for marker in (
                b"articleDetailContent",
                b"newsflash-item",
                b"common-width content",
            )
        )

    @staticmethod
    def _record_fetch_decision(
        context: AdapterContext,
        index: SourceArticleIndex,
        decision: dict[str, str],
    ) -> None:
        context.decision_state[index.source_article_id] = decision
        if context.record_decision is not None:
            context.record_decision(index.source_article_id, decision)

    @classmethod
    def _listing_event_complete(cls, index: SourceArticleIndex) -> bool:
        """Require a complete current funding assertion with a resolvable subject."""

        if len(index.summary.strip()) < 8:
            return False
        text = cls.clean_text(f"{index.title} {index.summary}")
        provisional = CleanArticle(
            index=index,
            clean_body=text,
            content_hash=cls.stable_hash(text),
        )
        prior_company = ""
        for sentence in cls._sentences(text):
            if cls._historical_background(sentence):
                continue
            for assertion in _FUNDING_ASSERTION.finditer(sentence):
                company = cls._company_for_event(
                    provisional,
                    sentence,
                    assertion,
                    prior_company,
                )
                if company:
                    return True
        return False

    @classmethod
    def _listing_negative_complete(cls, index: SourceArticleIndex) -> bool:
        text = cls.clean_text(f"{index.title} {index.summary}")
        explicit_non_event = re.search(
            r"\u878d\u8d44\u6210\u672c|\u878d\u8d44\u878d\u5238|"
            r"\u4e2a\u8d37|\u623f\u8d37|\u94f6\u884c|\u5229\u7387|\u503a\u5238|"
            r"\u76d1\u7ba1|\u89c4\u5b9a|\u653f\u7b56|\u6307\u5357|\u529e\u6cd5|"
            r"\u5f81\u6c42\u610f\u89c1|\u5c06\u4e8e.{0,20}\u5b9e\u65bd",
            text,
        )
        title_explicit_non_event = re.search(
            r"\u878d\u8d44\u6210\u672c|\u878d\u8d44\u878d\u5238|"
            r"\u4e2a\u8d37|\u623f\u8d37|\u76d1\u7ba1|\u89c4\u5b9a|"
            r"\u653f\u7b56|\u6307\u5357|\u529e\u6cd5|\u5f81\u6c42\u610f\u89c1",
            index.title,
        )
        incomplete_marker = re.search(
            r"\u8be6\u60c5.{0,12}(?:\u67e5\u770b|\u89c1|\u9605\u8bfb)\u6b63\u6587|"
            r"\u672a\u62ab\u9732|\u5f85\u62ab\u9732|\u66f4\u591a\u4fe1\u606f|"
            r"\u8fce\u6765\u65b0\u8fdb\u5c55|"
            r"\u5c06\u5728.{0,20}(?:\u6b63\u6587)?\u62ab\u9732|"
            r"(?:\u878d\u8d44|\u4ea4\u6613)\u7ec6\u8282.{0,20}\u62ab\u9732|"
            r"\u6b63\u6587\u62ab\u9732",
            text,
        )
        return bool(
            len(index.summary.strip()) >= 20
            and explicit_non_event
            and (index.title in index.summary or title_explicit_non_event)
            and not incomplete_marker
        )

    @classmethod
    def _company_for_listing_assertion(
        cls,
        sentence: str,
        assertion: re.Match[str],
    ) -> str:
        prefix = sentence[: assertion.start()]
        segment = re.split(r"[\u3002\uff01\uff1f\uff1b\uff1a:,]", prefix)[-1]
        segment = re.sub(
            r"^(?:36\u6c2a\u83b7\u6089|\u636e\u6089|\u8fd1\u65e5|\u65e5\u524d|\d{1,2}\u6708\d{1,2}\u65e5)\s*",
            "",
            segment.strip(),
        )
        segment = re.sub(
            r"(?:\u5df2|\u6b63\u5f0f|\u6210\u529f|\u5ba3\u5e03|\u5b98\u5ba3)\s*$",
            "",
            segment,
        ).strip(" \u2018\u2019\u201c\u201d\u300c\u300d")
        if cls._valid_company_candidate(segment):
            return segment
        return ""

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        parts = (
            (article.index.title, 1),
            (article.index.summary, 2),
            (clean_semantic_body_scope(article.clean_body), 3),
        )
        text = self.clean_text(" ".join(value for value, _ in parts))
        tags = tuple(
            tag for tag, rule in _INDUSTRY_RULES if rule.search(text)
        ) or ("other",)
        structured_company = str(
            article.index.structured_data.get("company") or ""
        ).strip()
        primary_company = self._primary_company(article)
        _, owned_alias = self._owned_company_relation(article.clean_body)
        last_company = primary_company
        candidates: dict[
            tuple[str, str, str],
            tuple[SemanticEvent, int],
        ] = {}
        for part, source_priority in parts:
            for sentence in self._sentences(self.clean_text(part)):
                if self._historical_background(sentence):
                    continue
                assertions = [
                    *((match.start(), "completed", match)
                      for match in _COMPLETED_ASSERTION.finditer(sentence)),
                    *((match.start(), "started", match)
                      for match in _STARTED_ASSERTION.finditer(sentence)),
                ]
                assertions.sort(key=lambda item: item[0])
                for _, status, assertion in assertions:
                    company = self._company_for_event(
                        article,
                        sentence,
                        assertion,
                        last_company,
                    )
                    if not company:
                        continue
                    if (
                        owned_alias
                        and company == owned_alias
                        and primary_company
                    ):
                        company = primary_company
                    last_company = company
                    round_name = self._nearest_round(sentence, assertion, status)
                    current_amount, cumulative_amount = self._funding_amounts(
                        sentence,
                        assertion,
                    )
                    quote = sentence[:500]
                    phase = (
                        "build_organize"
                        if status == "completed"
                        else "strategy_capital"
                    )
                    event = SemanticEvent(
                        source_id=channel.source_id,
                        source_article_id=article.index.source_article_id,
                        canonical_url=article.index.canonical_url,
                        company_mentions=tuple(
                            dict.fromkeys(
                                item
                                for item in (
                                    company,
                                    structured_company,
                                    owned_alias,
                                )
                                if item
                            )
                        ),
                        canonical_company=company,
                        event_type="funding",
                        event_date=article.index.published_at[:10],
                        industry_tags=tags,
                        funding_round=round_name,
                        funding_amount=current_amount,
                        cumulative_funding_amount=cumulative_amount,
                        event_summary=quote[:300],
                        evidence_quotes=(quote,),
                        confidence=(
                            "high"
                            if structured_company
                            and company == structured_company
                            else "medium"
                        ),
                        processor="rules:kr36-v2",
                        content_hash=article.content_hash,
                        phase=phase,
                        event_status=status,
                    )
                    key = (company, round_name, status)
                    previous = candidates.get(key)
                    quality = (source_priority, *self._event_quality(event))
                    if previous is None or quality > (
                        previous[1],
                        *self._event_quality(previous[0]),
                    ):
                        candidates[key] = (event, source_priority)
        resolved = self._resolve_event_conflicts(candidates)
        return [event for event, _ in resolved.values()]
    @staticmethod
    def _sentences(text: str) -> list[str]:
        return [
            item.strip()
            for item in re.split(r"(?<=[\u3002\uff01\uff1f\uff1b])", text)
            if item.strip()
        ]

    @staticmethod
    def _historical_background(sentence: str) -> bool:
        return bool(
            re.search(
                r"\u8be5\u8f6e\u878d\u8d44\u4e4b\u524d|"
                r"\u6b64\u524d.{0,20}\u5df2\u4e8e|"
                r"\u66fe\u7ecf.{0,20}\u5206\u522b\u4e8e|"
                r"\u5386\u53f2\u4e0a.{0,20}\u5b8c\u6210",
                sentence,
            )
        )

    @staticmethod
    def _owned_company_relation(body: str) -> tuple[str, str]:
        match = re.search(
            r"(?P<legal>[\u4e00-\u9fffA-Za-z0-9\u00b7"
            r"\uff08\uff09()]{4,60}(?:\u6709\u9650\u8d23\u4efb"
            r"\u516c\u53f8|\u80a1\u4efd\u6709\u9650\u516c\u53f8|"
            r"\u6709\u9650\u516c\u53f8)).{0,40}\u65d7\u4e0b"
            r".{0,40}[\u201c\u300c](?P<alias>[^\u201d\u300d]{2,40})"
            r"[\u201d\u300d]",
            body,
        )
        if not match:
            return "", ""
        return (
            match.group("legal").strip(),
            match.group("alias").strip(),
        )

    @classmethod
    def _primary_company(cls, article: CleanArticle) -> str:
        legal, _ = cls._owned_company_relation(article.clean_body)
        if legal:
            return legal
        structured = str(
            article.index.structured_data.get("company") or ""
        ).strip()
        if cls._valid_company_candidate(structured):
            return structured
        title = article.index.title
        prefix = _PREFIX_COMPANY.search(title)
        if prefix:
            candidate = prefix.group(1).strip(" \uff0c,\uff1a:")
            if cls._valid_company_candidate(candidate) or (
                2 <= len(candidate) <= 40
                and candidate.endswith("\u54c1\u724c")
                and candidate in article.clean_body
            ):
                return candidate
        for match in reversed(list(_QUOTED_COMPANY.finditer(title))):
            candidate = match.group(1).strip()
            if cls._valid_company_candidate(candidate):
                return candidate
        aliases = re.findall(
            r"(?:\u4ee5\u4e0b\u7b80\u79f0|\u4e0b\u79f0)"
            r"[\u201c\u300c]([^\u201d\u300d]{2,32})"
            r"[\u201d\u300d]",
            article.clean_body,
        )
        for candidate in aliases:
            if cls._valid_company_candidate(candidate.strip()):
                return candidate.strip()
        return ""
    @classmethod
    def _company_for_event(
        cls,
        article: CleanArticle,
        sentence: str,
        assertion: re.Match[str],
        prior_company: str,
    ) -> str:
        structured = str(
            article.index.structured_data.get("company") or ""
        ).strip()
        if prior_company and (
            prior_company in sentence
            or (
                structured
                and structured != prior_company
                and prior_company.endswith("\u6709\u9650\u516c\u53f8")
            )
        ):
            return prior_company
        if 2 <= len(structured) <= 40 and structured in sentence:
            return structured
        prefix = sentence[: assertion.start()]
        quoted = [
            match.group(1).strip()
            for match in _QUOTED_COMPANY.finditer(prefix)
            if cls._valid_company_candidate(match.group(1).strip())
        ]
        if quoted:
            return quoted[-1]
        legal = list(_LEGAL_COMPANY.finditer(prefix))
        if legal:
            return legal[-1].group(1)
        if prior_company:
            return prior_company
        segment = re.split(r"[\u3002\uff01\uff1f\uff1b\uff1a:,]", prefix)[-1]
        segment = re.sub(
            r"^(?:36\u6c2a\u83b7\u6089|"
            r"\u636e\u6089|\u8fd1\u65e5|\u65e5\u524d|"
            r"\d{1,2}\u6708\d{1,2}\u65e5)\s*",
            "",
            segment.strip(),
        )
        segment = re.sub(
            r"(?:\u5df2|\u6b63\u5f0f|\u6210\u529f|"
            r"\u5ba3\u5e03|\u5b98\u5ba3)\s*$",
            "",
            segment,
        ).strip(" \u201c\u201d\u300c\u300d")
        if cls._valid_company_candidate(segment):
            return segment
        return ""

    @staticmethod
    def _valid_company_candidate(candidate: str) -> bool:
        return bool(
            2 <= len(candidate) <= 40
            and not re.search(
                r"\u672c\u8f6e\u878d\u8d44|"
                r"\u878d\u8d44\u6210\u672c|"
                r"\u6295\u878d\u8d44$|"
                r"(?:\u4f01\u4e1a|\u54c1\u724c|\u9879\u76ee|"
                r"\u9886\u57df)(?:\u5df2\u4e8e.*)?$|"
                r"^(?:\u540c\u6b65|\u672c\u6b21|\u6b64\u6b21|"
                r"\u540c\u65f6|\u6295\u878d\u8d44|\u878d\u8d44|"
                r"\u503c\u5f97\u4e00\u63d0|"
                r"\u539f\u5b9a\d+\u6708)",
                candidate,
            )
        )

    @staticmethod
    def _nearest_round(
        sentence: str,
        assertion: re.Match[str],
        status: str,
    ) -> str:
        matches = list(_ROUND.finditer(sentence))
        if not matches:
            return ""
        if status == "started":
            within = [
                item
                for item in matches
                if assertion.start() <= item.start() <= assertion.end()
            ]
            if within:
                return within[-1].group(1)
        midpoint = (assertion.start() + assertion.end()) / 2
        return min(
            matches,
            key=lambda item: abs(((item.start() + item.end()) / 2) - midpoint),
        ).group(1)

    @staticmethod
    def _funding_amounts(
        sentence: str,
        assertion: re.Match[str],
    ) -> tuple[str, str]:
        current: list[tuple[float, str]] = []
        cumulative: list[tuple[float, str]] = []
        midpoint = (assertion.start() + assertion.end()) / 2
        for match in _AMOUNT.finditer(sentence):
            before = sentence[max(0, match.start() - 18) : match.start()]
            after = sentence[match.end() : match.end() + 12]
            if (
                re.search(
                    r"(?:\u4f30\u503c|\u6295\u524d|\u6295\u540e)"
                    r".{0,6}$",
                    before,
                )
                or re.match(
                    r"(?:\u7684)?(?:\u6295\u524d|\u6295\u540e)?"
                    r"\u4f30\u503c",
                    after,
                )
                or re.match(
                    r".{0,4}(?:\u8ba2\u5355|\u8425\u6536|"
                    r"\u5229\u6da6|\u4ea4\u4ed8|\u5c0f\u65f6)",
                    after,
                )
            ):
                continue
            distance = abs(((match.start() + match.end()) / 2) - midpoint)
            target = (
                cumulative
                if re.search(
                    r"\u7d2f\u8ba1(?:\u878d\u8d44)?"
                    r"(?:\u989d|\u91d1\u989d)?|"
                    r"(?:\d+\u8f6e|\u591a\u8f6e).{0,16}$",
                    before,
                )
                else current
            )
            target.append((distance, match.group(1)))
        current_value = min(current)[1] if current else ""
        cumulative_value = min(cumulative)[1] if cumulative else ""
        return current_value, cumulative_value

    @classmethod
    def _resolve_event_conflicts(
        cls,
        candidates: dict[
            tuple[str, str, str],
            tuple[SemanticEvent, int],
        ],
    ) -> dict[
        tuple[str, str, str],
        tuple[SemanticEvent, int],
    ]:
        resolved = dict(candidates)
        for key, value in tuple(candidates.items()):
            company, round_name, status = key
            event, priority = value
            opposite = (
                company,
                round_name,
                "started" if status == "completed" else "completed",
            )
            other = resolved.get(opposite)
            if other and other[1] > priority:
                resolved.pop(key, None)
                continue
            if (
                not round_name
                and any(
                    candidate_company == company
                    and candidate_status == status
                    and candidate_round
                    for (
                        candidate_company,
                        candidate_round,
                        candidate_status,
                    ) in resolved
                )
            ):
                resolved.pop(key, None)
        return resolved

    @staticmethod
    def _event_quality(event: SemanticEvent) -> tuple[int, int, int]:
        return (
            int(bool(event.funding_amount)),
            int(bool(event.cumulative_funding_amount)),
            len(event.evidence_quotes[0]) if event.evidence_quotes else 0,
        )
    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"36kr.com", "www.36kr.com"}:
            return url
        return f"https://36kr.com{parsed.path.rstrip('/')}"

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

    @classmethod
    def _project_data(cls, item: object) -> dict[str, str]:
        company = cls._first_text(
            item, "div.project-card-wrp div.right-top div.title"
        )
        round_name = cls._first_text(
            item, "div.project-card-wrp div.tag.fin-tag"
        )
        description = cls._first_text(
            item, "div.project-card-wrp div.right-bottom"
        )
        return {
            "company": company,
            "project_round": round_name,
            "project_description": description,
        }

    @staticmethod
    def _parse_time(value: str, context: AdapterContext) -> str:
        normalized = value.strip()
        local_now = context.now.astimezone(timezone(timedelta(hours=8)))
        full = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", normalized)
        if full:
            return normalized
        hours = re.fullmatch(r"(\d+)\s*小时前", normalized)
        if hours:
            parsed = local_now - timedelta(hours=int(hours.group(1)))
            return parsed.replace(microsecond=0).isoformat()
        minutes = re.fullmatch(r"(\d+)\s*分钟前", normalized)
        if minutes:
            parsed = local_now - timedelta(minutes=int(minutes.group(1)))
            return parsed.replace(microsecond=0).isoformat()
        if normalized == "昨天":
            return (local_now - timedelta(days=1)).date().isoformat()
        if normalized == "前天":
            return (local_now - timedelta(days=2)).date().isoformat()
        return ""

    @staticmethod
    def _remove_share_noise(value: str) -> str:
        output = re.sub(
            r"分享到\s*打开微信.*?分享按钮",
            " ",
            value,
            flags=re.S,
        )
        return re.sub(r"\s+", " ", output).strip()

    @staticmethod
    def _extract_author(adaptive: AdaptiveSelector, is_flash: bool) -> str:
        if is_flash:
            return ""
        candidates = adaptive.selector.css("span.author-name")
        return (
            re.sub(
                r"\s+",
                " ",
                candidates[0].get_all_text(separator=" ", strip=True),
            ).strip()
            if candidates
            else ""
        )

    @staticmethod
    def _company(article: CleanArticle, text: str) -> str:
        structured = str(article.index.structured_data.get("company") or "").strip()
        if 2 <= len(structured) <= 40:
            return structured
        legal = _LEGAL_COMPANY.search(text)
        if legal:
            return legal.group(1)
        for match in _QUOTED_COMPANY.finditer(text):
            candidate = match.group(1).strip()
            following = text[match.end() : match.end() + 24]
            directly_asserted = re.match(
                r"^[\s\uff0c,:\uff1a]*(?:(?:\u8fd1\u65e5|\u65e5\u524d|"
                r"\u5df2|\u4e8e|\u5728|\d+\u4e2a?\u6708)\s*)?"
                r"(?:\u5ba3\u5e03|\u5b98\u5ba3)?\s*(?:\u5df2)?"
                r"(?:\u5b8c\u6210|\u83b7|\u65a9\u83b7)",
                following,
            )
            bracketed_title = (
                match.start() <= 2
                and article.index.title.startswith(("\u3010", "\u3016"))
            )
            if (
                not re.search(
                    r"\u878d\u8d44\u6210\u672c|\u878d\u8d44\u5feb\u62a5|"
                    r"\u539f\u6587\u94fe\u63a5|\u9886\u57df\u7684|"
                    r"\u4e0a\u6e38\u5927\u8111|\u7528\u5341\u5e74\u4e0d\u574f|"
                    r"\u5361\u8116\u5b50",
                    candidate,
                )
                and (directly_asserted or bracketed_title)
            ):
                return candidate
        prefix = _PREFIX_COMPANY.search(article.index.title)
        if prefix:
            candidate = prefix.group(1).strip(" \uff0c,\uff1a:")
            if not re.search(
                r"(?:\u4f01\u4e1a|\u54c1\u724c|\u9879\u76ee)$",
                candidate,
            ):
                return candidate
        return ""

    @staticmethod
    def _evidence_sentence(text: str, company: str) -> str:
        sentences = [
            item.strip()
            for item in re.split(r"(?<=[。！？；])", text)
            if item.strip()
        ]
        for sentence in sentences:
            if company in sentence and _FUNDING_ASSERTION.search(sentence):
                return sentence
        for sentence in sentences:
            if _FUNDING_ASSERTION.search(sentence):
                return sentence
        return ""
