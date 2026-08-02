from datetime import datetime, timezone

import pytest

from ht_lead_radar.aggregate_adapters.base import AdapterContext, DetailFetchError, ListingInvariantError
from ht_lead_radar.aggregate_adapters.sites.fusion import FusionIndustryMediaAdapter, IterChinaAdapter

NOW = datetime(2026, 8, 1, 2, tzinfo=timezone.utc)
ITER = IterChinaAdapter()
MEDIA = FusionIndustryMediaAdapter()


def _context(tmp_path, *, full_visible_window=False):
    return AdapterContext.create(
        state_db=tmp_path / "state.sqlite3",
        fetch=lambda _url: b"",
        now=NOW,
        decision_state={
            "capture_full_visible_window": {"enabled": full_visible_window}
        },
    )


def _iter_listing(*, drifted=False, bad_url=False):
    wrapper = "moved-list" if drifted else "neiye-list tuwen"
    href = "/outside/1.html" if bad_url else "/picnews/info/2026/24397.html"
    return f'''<div class="{wrapper}"><ul id="content">
    <li><a class="db" href="{href}"><div class="tuwen-list"><div class="title">ITER installation milestone</div><div class="des">A sufficiently long public summary describing the engineering milestone.</div><div class="date-s">2026-07-31</div></div></a></li>
    <li><a class="db" href="/picnews/info/2026/24391.html"><div class="tuwen-list"><div class="title">ITER training program</div><div class="des">A sufficiently long old summary which closes the overlap window.</div><div class="date-s">2026-07-24</div></div></a></li>
    </ul></div>'''.encode()


def _iter_detail(index, *, drifted=False, title=None, date="2026-07-31"):
    body_class = "relocated-detail" if drifted else "neiye-detail"
    return f'''<main><h4 class="detail-title">{title or index.title}</h4><div class="flex-boxs"><span>Published: {date}</span><span>Font size</span></div><div class="{body_class}" id="detailsCont"><p>The ITER team completed a major component installation after continuous safe engineering work and precise alignment.</p><p>The completed work supports subsequent integration, system testing, and international delivery milestones.</p></div><footer>navigation noise</footer></main>'''.encode()


def _media_listing(*, drifted=False):
    wrapper = "moved-posts" if drifted else "s-blog-posts"
    return f'''<div class="{wrapper}">
    <div class="s-blog-entry"><div class="s-blog-title"><a href="/blog/iter-123">ITER consortium contract</a></div><span class="s-blog-date">2026\u5e747\u670831\u65e5</span><span class="s-blog-tag">news</span><div class="s-blog-details-blurb">A public summary describing the ITER contract and engineering delivery scope.</div></div>
    <div class="s-blog-entry"><div class="s-blog-title"><a href="/blog/fusion-456">Fusion test milestone</a></div><span class="s-blog-date">2026\u5e747\u670830\u65e5</span><span class="s-blog-tag">technology</span><div class="s-blog-details-blurb">A public summary describing a fusion test and its engineering verification result.</div></div>
    <div class="s-blog-entry"><div class="s-blog-title"><a href="/blog/old-789">Older fusion item</a></div><span class="s-blog-date">2026\u5e747\u670820\u65e5</span><span class="s-blog-tag">other</span><div class="s-blog-details-blurb">An older item that proves the homepage closes the incremental time window.</div></div>
    </div>'''.encode()


def _media_detail(
    index,
    *,
    drifted=False,
    title=None,
    created="2026-07-31",
    short=False,
):
    header = "moved-header" if drifted else "s-blog-header-content"
    body = "s-blog-body"
    content = (
        "short"
        if short
        else "The consortium signed a critical ITER power-system contract "
        "covering design, manufacturing, testing, installation, and "
        "commissioning. The contract demonstrates fusion engineering "
        "capability and provides support for future international delivery work."
    )
    return f'''<script>$S.blogPostData={{"blogPostMeta":{{"createdAt":"{created}T02:00:00+08:00","publishedAt":"{created}T00:43:02+08:00"}}}};</script><div class="{header}"><h1>{title or index.title}</h1></div><div class="s-blog-content"><div class="{body}"><div class="s-blog-post-section"><p>{content}</p></div><div class="s-blog-post-section"><p>https://example.com/unrelated-navigation</p></div></div><div class="s-blog-footer s-blog-body">recommendation noise</div></div>'''.encode()


def test_iter_listing_detail_and_fail_closed(tmp_path):
    context = _context(tmp_path)
    indexes = ITER.parse_listing(ITER.channels[0], _iter_listing(), context)
    assert [item.source_article_id for item in indexes] == ["24397"]
    assert indexes[0].canonical_url.endswith("/picnews/info/2026/24397.html")
    article = ITER.parse_detail(ITER.channels[0], indexes[0], _iter_detail(indexes[0]), context)
    assert article.extraction_method == "exact"
    assert "navigation noise" not in article.clean_body
    with pytest.raises(DetailFetchError, match="title mismatch"):
        ITER.parse_detail(ITER.channels[0], indexes[0], _iter_detail(indexes[0], title="wrong title"), context)
    with pytest.raises(DetailFetchError, match="date mismatch"):
        ITER.parse_detail(ITER.channels[0], indexes[0], _iter_detail(indexes[0], date="2026-07-30"), context)
    with pytest.raises(ListingInvariantError, match="invalid item"):
        ITER.parse_listing(ITER.channels[0], _iter_listing(bad_url=True), _context(tmp_path / "bad"))


def test_iter_adaptive_recovery_and_access_fail_closed(tmp_path):
    context = _context(tmp_path)
    index = ITER.parse_listing(ITER.channels[0], _iter_listing(), context)[0]
    ITER.parse_detail(ITER.channels[0], index, _iter_detail(index), context)
    assert ITER.parse_listing(ITER.channels[0], _iter_listing(drifted=True), context)[0].discovery_method == "adaptive"
    assert ITER.parse_detail(ITER.channels[0], index, _iter_detail(index, drifted=True), context).extraction_method == "adaptive"
    with pytest.raises(ListingInvariantError, match="no bypass"):
        ITER.parse_listing(ITER.channels[0], b"Just a moment", context)


def test_media_listing_detail_adaptive_and_fail_closed(tmp_path):
    context = _context(tmp_path)
    indexes = MEDIA.parse_listing(MEDIA.channels[0], _media_listing(), context)
    assert [item.source_article_id for item in indexes] == ["iter-123", "fusion-456"]
    assert indexes[0].structured_data["tags"] == ("news",)
    article = MEDIA.parse_detail(MEDIA.channels[0], indexes[0], _media_detail(indexes[0]), context)
    assert article.extraction_method == "exact"
    assert "recommendation noise" not in article.clean_body
    assert "example.com" not in article.clean_body
    assert MEDIA.parse_listing(MEDIA.channels[0], _media_listing(drifted=True), context)[0].discovery_method == "adaptive"
    assert MEDIA.parse_detail(MEDIA.channels[0], indexes[0], _media_detail(indexes[0], drifted=True), context).extraction_method == "adaptive"
    fallback = MEDIA.parse_detail(
        MEDIA.channels[0],
        indexes[0],
        _media_detail(indexes[0], short=True),
        context,
    )
    assert fallback.fetch_status == "listing_complete"
    assert fallback.extraction_method == "listing-headline-summary-fallback"
    with pytest.raises(DetailFetchError, match="date mismatch"):
        MEDIA.parse_detail(MEDIA.channels[0], indexes[0], _media_detail(indexes[0], created="2026-07-30"), context)
    with pytest.raises(DetailFetchError, match="title mismatch"):
        MEDIA.parse_detail(MEDIA.channels[0], indexes[0], _media_detail(indexes[0], title="wrong title"), context)
    with pytest.raises(ListingInvariantError, match="no bypass"):
        MEDIA.parse_listing(MEDIA.channels[0], b"Access Denied", context)


def test_full_visible_window_keeps_older_bootstrap_items(tmp_path):
    context = _context(tmp_path, full_visible_window=True)

    assert len(ITER.parse_listing(ITER.channels[0], _iter_listing(), context)) == 2
    assert len(MEDIA.parse_listing(MEDIA.channels[0], _media_listing(), context)) == 3
    assert ITER._titles_match(
        "NFEC2026 industrial exhibition third notice...",
        "NFEC2026 industrial exhibition third notice and registration guide",
    )
