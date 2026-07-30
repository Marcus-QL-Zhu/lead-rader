"""Dedicated adapter for the public VBDATA investment tag archive."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
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
from ..finance_rules import FundingRuleConfig, extract_funding_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_ARTICLE_PATH = re.compile(r"/(\d+)")
_LEADING_LABEL = re.compile(r"^[【〖［\[][^】〗］\]]{1,16}[】〗］\]]")
_TITLE_ASSERTION = re.compile(
    r"(?:刚|已|再|顺利|正式|成功|宣布|官宣)*"
    r"(?:完成|获得|斩获|获(?!悉))"
)
_LEGAL_ALIAS = re.compile(
    r"(?P<legal>[\u4e00-\u9fffA-Za-z0-9·（）()]{4,80}"
    r"(?:有限责任公司|股份有限公司|有限公司))"
    r"（(?:以下简称|简称)[“「](?P<alias>[^”」]{2,40})[”」]）"
)
_LEGAL_COMPANY = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·（）()]{4,80}"
    r"(?:有限责任公司|股份有限公司|有限公司))"
)
_QUOTED_COMPANY = re.compile(r"[「『“\"]([^」』”\"]{2,40})[」』”\"]")
_GENERIC_COMPANY = re.compile(
    r"(?:报告|活动|峰会|专场|行业|市场|成本|逻辑|观察|盘点|"
    r"投融资|融资|投资|资本|项目|产品)$"
)
_FULL_DATE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_RHETORICAL_FUNDING = re.compile(
    r"投融资角度|融资逻辑|估值逻辑|融资成本|融资环境|融资难|"
    r"融资渠道|融资需求|融资实属|融资叙事"
)
_SHORTHAND_AMOUNT = re.compile(
    r"((?:近|超|逾|约|数)?\s*\d+(?:\.\d+)?\s*亿)"
    r"(?=(?:Pre-[A-Z](?:\+{1,2})?|[A-Z](?:\+{1,2})?|"
    r"天使(?:\+{1,2})?|种子(?:\+{1,2})?)轮)"
)


class VbdataAdapter(AggregateAdapter):
    """Enumerate the public VBDATA investment-tag list without keyword filtering."""

    adapter_id = "vbdata"
    channels = (
        SourceChannel(
            source_id="vbdata-funding",
            name="动脉网—投融资",
            url="https://www.vbdata.cn/articleList?tag=5512",
            source_grade="B",
            event_prior=("funding",),
            allowed_hosts=("www.vbdata.cn",),
            allowed_path_patterns=(r"/\d+",),
        ),
    )
    minimum_listing_count = 10
    maximum_listing_count = 50

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
            "ul.special > li",
            identifier=f"{channel.source_id}:listing-item",
            minimum_count=self.minimum_listing_count,
            maximum_count=self.maximum_listing_count,
        )
        if not selection.elements:
            raise ListingInvariantError(f"{channel.source_id} listing selector failed")

        embedded_dates = self._embedded_publish_times(html)
        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        for position, item in enumerate(selection.elements, start=1):
            links = tuple(item.css("div.article-content div.spc_cnt > a.h1.over-p2"))
            if len(links) != 1:
                raise ListingInvariantError(
                    f"{channel.source_id} item {position} has {len(links)} title links"
                )
            link = links[0]
            canonical_url = self._canonical_url(
                urljoin(
                    channel.url,
                    str(link.attrib.get("href") or "").strip(),
                )
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
            summary = self._first_text(item, "h2.over-p1")
            category = self._first_text(item, "span.icon_column")
            tracks = tuple(
                dict.fromkeys(
                    self.clean_text(node.get_all_text(separator=" ", strip=True))
                    for node in item.css("div.tags1 > a")
                    if self.clean_text(node.get_all_text(separator=" ", strip=True))
                )
            )
            attribution = tuple(item.css("div.auth_time > span"))
            if len(attribution) != 2:
                raise ListingInvariantError(
                    f"{channel.source_id} item {article_id} has invalid "
                    "author/time attribution"
                )
            author = self.clean_text(
                attribution[0].get_all_text(separator=" ", strip=True)
            )
            time_label = self.clean_text(
                attribution[1].get_all_text(separator=" ", strip=True)
            )
            published_at = embedded_dates.get(article_id) or self._parse_time(
                time_label,
                context,
            )
            if not published_at:
                raise ListingInvariantError(
                    f"{channel.source_id} item {article_id} has no exact date"
                )
            if self._date_value(published_at) > context.now.date():
                raise ListingInvariantError(
                    f"{channel.source_id} item {article_id} is future dated"
                )

            company = self._title_company(title)
            structured = {
                "author": author,
                "category": category,
                "company": company,
                "time_label": time_label,
                "tracks": tracks,
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
                    channel="investment-tag",
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
        title_selection = adaptive.css(
            "div.lelt-container > h1",
            identifier=f"{channel.source_id}:detail-title",
            minimum_count=1,
            maximum_count=1,
        )
        body_selection = adaptive.css(
            "div.lelt-container > div.content",
            identifier=f"{channel.source_id}:detail-body",
            minimum_count=1,
            maximum_count=1,
        )
        if not title_selection.elements:
            raise DetailFetchError(
                f"{channel.source_id} detail title selector failed for "
                f"{index.source_article_id}"
            )
        if not body_selection.elements:
            raise DetailFetchError(
                f"{channel.source_id} detail body selector failed for "
                f"{index.source_article_id}"
            )

        detail_title = self.clean_text(
            title_selection.elements[0].get_all_text(
                separator=" ",
                strip=True,
            )
        )
        if not self._titles_match(index.title, detail_title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for "
                f"{index.source_article_id}"
            )
        body = self.clean_text(
            " ".join(self._body_paragraphs(body_selection.elements[0]))
        )
        if len(body) < 80:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for "
                f"{index.source_article_id}"
            )

        detail_time = self._first_text(
            adaptive.selector,
            "div.intel-source div.card-info span.spa2",
        )
        detail_date = self._parse_full_date(detail_time)
        if not detail_date:
            raise DetailFetchError(
                f"{channel.source_id} detail date missing for {index.source_article_id}"
            )
        if detail_date != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for "
                f"{index.source_article_id}: "
                f"{index.published_at} != {detail_date}"
            )
        if self._date_value(detail_date) > context.now.date():
            raise DetailFetchError(
                f"{channel.source_id} detail is future dated for "
                f"{index.source_article_id}"
            )

        author = self._first_text(
            adaptive.selector,
            "div.intel-source div.card-info span.spa1",
        )
        entity_company = self._first_text(
            adaptive.selector,
            "div.entity-link h1.product-name",
        )
        legal, alias = self._company_alias_relation(body)
        title_company = self._title_company(index.title)
        company = legal or entity_company or title_company
        mentions = tuple(
            dict.fromkeys(
                item
                for item in (legal, alias, entity_company, title_company)
                if self._valid_company(item)
            )
        )
        structured = dict(index.structured_data)
        structured.update(
            {
                "company": company,
                "company_mentions": mentions,
                "detail_published_at": detail_time,
            }
        )
        digest = sha256(f"{index.title}\n{body}".encode("utf-8")).hexdigest()
        methods = {title_selection.method, body_selection.method}
        method = "adaptive" if "adaptive" in methods else "exact"
        similarity = 72 if method == "adaptive" else None
        return CleanArticle(
            index=index,
            clean_body=body,
            author=author,
            tags=tuple(str(item) for item in index.structured_data.get("tracks", ())),
            structured_data=structured,
            extraction_method=method,
            adaptive_similarity=similarity,
            evidence_locators={
                "title": "detail:div.lelt-container>h1",
                "published_at": ("detail:div.intel-source div.card-info span.spa2"),
                "body": "detail:div.lelt-container>div.content p",
                "author": ("detail:div.intel-source div.card-info span.spa1"),
                "company": (
                    "detail:div.entity-link h1.product-name / body:legal-alias"
                ),
            },
            fetch_status="ok",
            content_hash=digest,
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        rule_article = replace(
            article,
            clean_body=self._rule_body(article.clean_body),
        )
        events = extract_funding_events(
            channel,
            rule_article,
            config=FundingRuleConfig(
                processor="rules:vbdata-v1",
                company_resolver=self._company_for_event,
            ),
        )
        return [self._enrich_shorthand_amount(event) for event in events]

    @classmethod
    def _company_for_event(
        cls,
        article: CleanArticle,
        sentence: str,
        assertion: re.Match[str],
        last_company: str,
    ) -> tuple[str, tuple[str, ...]]:
        structured_company = cls.clean_text(
            str(article.structured_data.get("company") or "")
        )
        structured_mentions = tuple(
            cls.clean_text(str(item))
            for item in article.structured_data.get("company_mentions", ())
            if cls.clean_text(str(item))
        )
        if cls._valid_company(structured_company) and (
            structured_company in sentence
            or any(item in sentence for item in structured_mentions)
            or sentence == article.index.title
        ):
            return (
                structured_company,
                structured_mentions or (structured_company,),
            )

        prefix = sentence[: assertion.start()]
        quoted = [
            cls.clean_text(match.group(1))
            for match in _QUOTED_COMPANY.finditer(prefix)
            if cls._valid_company(cls.clean_text(match.group(1)))
        ]
        if quoted:
            return quoted[-1], (quoted[-1],)
        legal = list(_LEGAL_COMPANY.finditer(prefix))
        if legal:
            company = cls.clean_text(legal[-1].group(1))
            return company, (company,)
        title_company = cls._title_company(article.index.title)
        if title_company and (
            title_company in sentence or sentence == article.index.title
        ):
            return title_company, (title_company,)
        if cls._valid_company(last_company) and last_company in sentence:
            return last_company, (last_company,)
        return "", ()

    @classmethod
    def _embedded_publish_times(cls, html: bytes) -> dict[str, str]:
        text = html.decode("utf-8", errors="replace")
        marker = "window.__NUXT__=(function("
        start = text.find(marker)
        if start < 0:
            return {}
        script_end = text.find("</script>", start)
        if script_end < 0:
            return {}
        script = text[start:script_end]
        params_end = script.find("){return")
        call_marker = script.rfind("}(")
        if params_end < 0 or call_marker < 0 or not script.endswith("));"):
            return {}
        params = [item.strip() for item in script[len(marker) : params_end].split(",")]
        arguments = cls._split_js_arguments(script[call_marker + 2 : -3])
        if len(params) != len(arguments):
            return {}
        values: dict[str, str] = {}
        for name, raw in zip(params, arguments):
            raw = raw.strip()
            if not raw.startswith('"'):
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, str):
                values[name] = value

        list_start = script.find("articleList:[")
        list_end = script.find("}],total:", list_start)
        if list_start < 0 or list_end < 0:
            return {}
        list_body = script[list_start:list_end]
        output: dict[str, str] = {}
        for match in re.finditer(
            r"\bid:(?P<id>\d+).*?"
            r"\bpublishTime:(?P<value>[A-Za-z_$][A-Za-z0-9_$]*)",
            list_body,
            re.S,
        ):
            published = cls._parse_full_date(values.get(match.group("value"), ""))
            if published:
                output[match.group("id")] = published
        return output

    @staticmethod
    def _split_js_arguments(value: str) -> list[str]:
        output: list[str] = []
        start = 0
        depth = 0
        quote = ""
        escaped = False
        for position, character in enumerate(value):
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = ""
                continue
            if character in {'"', "'"}:
                quote = character
            elif character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
            elif character == "," and depth == 0:
                output.append(value[start:position])
                start = position + 1
        output.append(value[start:])
        return output

    @classmethod
    def _parse_time(
        cls,
        value: str,
        context: AdapterContext,
    ) -> str:
        full = cls._parse_full_date(value)
        if full:
            return full
        normalized = cls.clean_text(value)
        hours = re.fullmatch(r"(\d+)\s*小时前", normalized)
        if hours:
            return (
                (context.now - timedelta(hours=int(hours.group(1)))).date().isoformat()
            )
        days = re.fullmatch(r"(\d+)\s*天前", normalized)
        if days:
            return (context.now - timedelta(days=int(days.group(1)))).date().isoformat()
        return ""

    @staticmethod
    def _parse_full_date(value: str) -> str:
        match = _FULL_DATE.search(value)
        if not match:
            return ""
        candidate = "-".join(match.groups())
        try:
            return datetime.fromisoformat(candidate).date().isoformat()
        except ValueError:
            return ""

    @staticmethod
    def _date_value(value: str):
        return datetime.fromisoformat(value[:10]).date()

    @staticmethod
    def _canonical_url(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in {"vbdata.cn", "www.vbdata.cn"}:
            return url
        return f"https://www.vbdata.cn{parsed.path.rstrip('/')}"

    @classmethod
    def _title_company(cls, title: str) -> str:
        normalized = _LEADING_LABEL.sub("", cls.clean_text(title)).strip()
        if normalized.startswith("对话"):
            normalized = normalized[2:].strip()
        assertion = _TITLE_ASSERTION.search(normalized)
        if not assertion:
            return ""
        prefix = normalized[: assertion.start()].rstrip(" ，,：:丨｜|")
        prefix = re.sub(r"(?:刚|已|再|顺利|正式|成功)$", "", prefix)
        segments = [
            item.strip() for item in re.split(r"[，,：:丨｜|]", prefix) if item.strip()
        ]
        candidate = segments[-1] if segments else prefix
        if "企业" in candidate:
            candidate = candidate.rsplit("企业", 1)[-1].strip()
        return candidate if cls._valid_company(candidate) else ""

    @staticmethod
    def _valid_company(value: str) -> bool:
        normalized = value.strip()
        return bool(
            2 <= len(normalized) <= 80 and not _GENERIC_COMPANY.search(normalized)
        )

    @classmethod
    def _company_alias_relation(cls, body: str) -> tuple[str, str]:
        match = _LEGAL_ALIAS.search(body)
        if not match:
            return "", ""
        return (
            cls.clean_text(match.group("legal")),
            cls.clean_text(match.group("alias")),
        )

    @classmethod
    def _rule_body(cls, body: str) -> str:
        sentences = [
            cls.clean_text(item)
            for item in re.split(r"(?<=[。！？；])", body)
            if cls.clean_text(item)
        ]
        return " ".join(
            sentence
            for sentence in sentences
            if not _RHETORICAL_FUNDING.search(sentence)
        )

    @staticmethod
    def _enrich_shorthand_amount(event: SemanticEvent) -> SemanticEvent:
        if event.funding_amount or not event.evidence_quotes:
            return event
        match = _SHORTHAND_AMOUNT.search(event.evidence_quotes[0])
        if not match:
            return event
        return replace(event, funding_amount=re.sub(r"\s+", "", match.group(1)))

    @classmethod
    def _body_paragraphs(cls, body: object) -> tuple[str, ...]:
        paragraphs = tuple(body.css("p"))
        output = tuple(
            cls.clean_text(paragraph.get_all_text(separator=" ", strip=True))
            for paragraph in paragraphs
            if cls.clean_text(paragraph.get_all_text(separator=" ", strip=True))
        )
        if output:
            return output
        fallback = cls.clean_text(body.get_all_text(separator=" ", strip=True))
        return (fallback,) if fallback else ()

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


__all__ = ["VbdataAdapter"]
