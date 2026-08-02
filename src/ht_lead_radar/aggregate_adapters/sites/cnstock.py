"""Dedicated adapter for the public CNStock company channel."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from scrapling import Selector

from ..base import AdapterContext, AggregateAdapter, DetailFetchError, ListingInvariantError
from ..industry_rules import IndustryRuleConfig, extract_industry_events
from ..models import CleanArticle, SemanticEvent, SourceArticleIndex, SourceChannel


_CHINA = ZoneInfo("Asia/Shanghai")
_DETAIL_PATH = re.compile(r"/commonDetail/(\d+)")
_ACCESS_CONTROL = re.compile(
    r"访问过于频繁|安全验证|captcha|challenge-platform|403 Forbidden|Access Denied",
    re.I,
)


class CnstockCompanyChannelAdapter(AggregateAdapter):
    """Read one complete visible company-channel window from Next.js state."""

    adapter_id = "cnstock_company_channel"
    channels = (
        SourceChannel(
            source_id="cnstock-company-channel",
            name="上海证券报·中国证券网—公司",
            url="https://www.cnstock.com/channel/10006",
            source_grade="B",
            event_prior=(
                "funding",
                "executive_change",
                "factory_or_capacity",
                "major_order",
                "partnership",
                "customer_validation",
                "new_site_or_entity",
                "regulatory_or_clinical",
                "merger_acquisition",
                "ipo_or_listing",
                "enterprise_system",
                "technical_milestone",
            ),
            allowed_hosts=("www.cnstock.com",),
            allowed_path_patterns=(r"/commonDetail/\d+",),
        ),
    )
    # The previous completed day can legitimately have no company article.
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
        document = self._next_data(text, channel.source_id, listing=True)
        page_info = self._page_info(document)
        build_id = str(document.get("buildId") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", build_id):
            raise ListingInvariantError(
                f"{channel.source_id} Next.js build id missing/invalid"
            )
        containers = page_info.get("list") if isinstance(page_info, dict) else None
        raw_items = self._article_items(containers)
        if not raw_items:
            raise ListingInvariantError(
                f"{channel.source_id} Next.js listing payload missing"
            )
        target_day = context.now.astimezone(_CHINA).date() - timedelta(days=1)
        discovered_at = context.now.replace(microsecond=0).isoformat()
        output: list[SourceArticleIndex] = []
        seen: set[str] = set()
        for item in raw_items:
            if not isinstance(item, dict):
                raise ListingInvariantError(
                    f"{channel.source_id} invalid listing item"
                )
            article_id = str(item.get("contId") or "").strip()
            title = self.clean_text(str(item.get("name") or ""))
            if not article_id.isdigit() or not 4 <= len(title) <= 300:
                raise ListingInvariantError(
                    f"{channel.source_id} invalid item id/title"
                )
            if article_id in seen:
                continue
            seen.add(article_id)
            published = self._item_datetime(item)
            if published is None:
                raise ListingInvariantError(
                    f"{channel.source_id} missing exact date for {article_id}"
                )
            if not context.capture_full_visible_window and published.date() != target_day:
                continue
            canonical_url = f"https://www.cnstock.com/commonDetail/{article_id}"
            node = item.get("nodeInfo") if isinstance(item.get("nodeInfo"), dict) else {}
            tag = item.get("tagInfo") if isinstance(item.get("tagInfo"), dict) else {}
            summary = self.clean_text(
                str(item.get("summary") or (item.get("shareInfo") or {}).get("summary") or "")
            )
            output.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=article_id,
                    channel="financial-company-news",
                    canonical_url=canonical_url,
                    title=title,
                    published_at=published.isoformat(),
                    discovered_at=discovered_at,
                    cursor_value=f"{published.isoformat()}|{article_id}",
                    listing_page=channel.url,
                    listing_position=len(output) + 1,
                    content_hash=self.stable_hash(
                        f"{canonical_url}\n{title}\n{published.isoformat()}\n{summary}"
                    ),
                    discovery_method="next-data:pageInfo.list",
                    summary=summary,
                    structured_data={
                        "next_build_id": build_id,
                        "node_name": str(node.get("name") or ""),
                        "tag_name": str(tag.get("name") or ""),
                        "author": str(item.get("author") or ""),
                    },
                )
            )
        self.validate_listing(channel, output)
        return output

    def fetch_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        context: AdapterContext,
    ) -> bytes:
        """Fetch the public Next.js JSON payload instead of bursting HTML pages.

        The canonical article URLs are protected by an Aliyun JavaScript
        challenge after a short burst. CNStock's own page uses the same-host,
        cacheable ``/_next/data`` representation for client navigation. It is
        materially smaller, carries the identical article record, and avoids
        turning ordinary incremental collection into repeated challenge hits.
        """

        del channel
        build_id = str(index.structured_data.get("next_build_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", build_id):
            raise DetailFetchError(
                f"{index.source_id} detail build id missing for {index.source_article_id}"
            )
        url = (
            f"https://www.cnstock.com/_next/data/{build_id}/commonDetail/"
            f"{index.source_article_id}.json?id={index.source_article_id}"
        )
        return context.fetch(url)

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        del context
        text = html.decode("utf-8", errors="replace")
        if _ACCESS_CONTROL.search(text):
            raise DetailFetchError(
                f"{channel.source_id} detail access control detected; no bypass"
            )
        document = self._detail_document(text, channel.source_id)
        try:
            page_props = document.get("pageProps") or document["props"]["pageProps"]
            data = page_props["data"]
        except (AttributeError, KeyError, TypeError) as exc:
            raise DetailFetchError(
                f"{channel.source_id} detail payload missing for {index.source_article_id}"
            ) from exc
        if not isinstance(data, dict) or str(data.get("contId") or "") != index.source_article_id:
            raise DetailFetchError(
                f"{channel.source_id} detail id mismatch for {index.source_article_id}"
            )
        title = self.clean_text(str(data.get("title") or data.get("name") or ""))
        if not self._titles_match(index.title, title):
            raise DetailFetchError(
                f"{channel.source_id} detail title mismatch for {index.source_article_id}"
            )
        published = self._detail_datetime(str(data.get("pubTime") or ""))
        if published is None or published.date().isoformat() != index.published_at[:10]:
            raise DetailFetchError(
                f"{channel.source_id} listing/detail date mismatch for {index.source_article_id}"
            )
        text_info = data.get("textInfo") if isinstance(data.get("textInfo"), dict) else {}
        raw_body = str(text_info.get("content") or "")
        body = self.clean_text(
            Selector(raw_body, url=index.canonical_url).get_all_text(
                separator=" ", strip=True
            )
        )
        if len(body) < 30:
            raise DetailFetchError(
                f"{channel.source_id} detail body too short for {index.source_article_id}"
            )
        digest = sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()
        return CleanArticle(
            index=index,
            clean_body=body,
            author=self.clean_text(str(data.get("author") or "")).removeprefix("作者："),
            tags=tuple(
                value
                for value in (
                    self.clean_text(str((data.get("tagInfo") or {}).get("name") or "")),
                    self.clean_text(str((data.get("nodeInfo") or {}).get("name") or "")),
                )
                if value
            ),
            structured_data={
                **index.structured_data,
                "document_type_hint": (
                    "long_feature"
                    if re.search(r"专访|对话|会客厅|董事长访谈", title)
                    else ""
                ),
            },
            extraction_method="next-data-structured",
            evidence_locators={
                "title": "__NEXT_DATA__.props.pageProps.data.title",
                "published_at": "__NEXT_DATA__.props.pageProps.data.pubTime",
                "body": "__NEXT_DATA__.props.pageProps.data.textInfo.content",
            },
            fetch_status="ok",
            content_hash=digest,
        )

    @classmethod
    def _detail_document(cls, text: str, source_id: str) -> dict[str, Any]:
        stripped = text.lstrip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise DetailFetchError(f"{source_id} invalid detail JSON") from exc
            if not isinstance(payload, dict):
                raise DetailFetchError(f"{source_id} invalid detail JSON root")
            return payload
        return cls._next_data(text, source_id, listing=False)

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        return extract_industry_events(
            channel,
            article,
            config=IndustryRuleConfig(processor="rules:cnstock-company-v1"),
        )

    @staticmethod
    def _next_data(text: str, source_id: str, *, listing: bool) -> dict[str, Any]:
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            text,
            re.S,
        )
        if not match:
            error = ListingInvariantError if listing else DetailFetchError
            raise error(f"{source_id} __NEXT_DATA__ missing")
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            error = ListingInvariantError if listing else DetailFetchError
            raise error(f"{source_id} invalid __NEXT_DATA__") from exc
        if not isinstance(payload, dict):
            error = ListingInvariantError if listing else DetailFetchError
            raise error(f"{source_id} invalid __NEXT_DATA__ root")
        return payload

    @classmethod
    def _page_info(cls, value: Any) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                rows = node.get("list")
                if isinstance(rows, list) and rows:
                    candidates.append(node)
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return max(candidates, key=lambda item: len(item["list"])) if candidates else {}

    @staticmethod
    def _article_items(value: Any) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if str(node.get("contId") or "").isdigit() and node.get("name"):
                    output.append(node)
                for child in node.values():
                    if isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return output

    @staticmethod
    def _item_datetime(item: dict[str, Any]) -> datetime | None:
        share = item.get("shareInfo") if isinstance(item.get("shareInfo"), dict) else {}
        date_info = share.get("dateInfo") if isinstance(share.get("dateInfo"), dict) else {}
        try:
            return datetime(
                int(date_info["year"]),
                int(date_info["month"]),
                int(date_info["day"]),
                int(date_info.get("hour") or 0),
                int(date_info.get("minute") or 0),
                tzinfo=_CHINA,
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _detail_datetime(value: str) -> datetime | None:
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=_CHINA)
        except ValueError:
            return None

    @staticmethod
    def _titles_match(expected: str, actual: str) -> bool:
        left = re.sub(r"\s+", "", expected)
        right = re.sub(r"\s+", "", actual)
        return bool(left and right and (left == right or left in right or right in left))


__all__ = ["CnstockCompanyChannelAdapter"]
