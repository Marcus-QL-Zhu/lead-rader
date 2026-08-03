from ht_lead_radar.aggregate_adapters.adaptive import (
    AdaptiveSelector,
    _BoundedSQLiteStorageSystem,
)
import gc


def test_adaptive_storage_connections_have_a_bounded_working_set(tmp_path):
    storage_path = tmp_path / "adaptive.sqlite3"
    for index in range(24):
        selector = AdaptiveSelector(
            "<html><body><div class='card'>ok</div></body></html>",
            url=f"https://example.test/articles/{index}",
            storage_path=storage_path,
        )
        selector.css(
            "div.card",
            identifier=f"card-{index}",
            minimum_count=1,
            maximum_count=1,
        )
    del selector
    gc.collect()

    info = _BoundedSQLiteStorageSystem.cache_info()
    assert info.maxsize == 8
    assert info.currsize <= 8


def test_adaptive_selectors_can_be_disabled_without_opening_storage(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LEAD_RADAR_ADAPTIVE_SELECTORS", "0")
    storage_path = tmp_path / "disabled.sqlite3"
    selector = AdaptiveSelector(
        "<html><body><div class='changed'>ok</div></body></html>",
        url="https://example.test/changed",
        storage_path=storage_path,
    )

    result = selector.css(
        "div.original",
        identifier="card",
        minimum_count=1,
        maximum_count=1,
    )

    assert not result.elements
    assert result.method == "failed"
    assert not storage_path.exists()
