import json
import sqlite3

from ht_lead_radar.fixed_sources import FixedSourceCollector


def _write_registry(tmp_path):
    registry = {
        'sources': [
            {
                'id': 'good',
                'name': 'official news',
                'list_url': 'https://example.com/news/',
                'link_pattern': r'example\.com/news/\d+\.html$',
                'company': '\u56e0\u65f6\u673a\u5668\u4eba',
                'grade': 'A',
                'fetch_detail': True,
            },
            {
                'id': 'broken',
                'name': 'broken source',
                'list_url': 'https://broken.example/',
                'link_pattern': r'broken\.example/',
                'grade': 'B',
            },
        ]
    }
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')
    return path


def test_fixed_sources_persist_dedupe_and_isolate_failures(tmp_path, monkeypatch):
    state_db = tmp_path / 'state.sqlite'
    collector = FixedSourceCollector(_write_registry(tmp_path), state_db)

    listing = '<a href="/news/297.html">\u56e0\u65f6\u5b9e\u73b0\u4e07\u53f0\u7075\u5de7\u624b\u4ea4\u4ed8</a>'
    detail = '<article>2026-07-20 \u65b0\u57fa\u5730\u6295\u4ea7\uff0c\u5e74\u4ea7\u80fd10\u4e07\u53f0\uff0c\u5df2\u91cf\u4ea7\u4ea4\u4ed8</article>'

    def fake_fetch(url):
        if url == 'https://broken.example/':
            raise RuntimeError('temporary failure')
        return detail if url.endswith('297.html') else listing

    monkeypatch.setattr(collector, '_fetch', fake_fetch)
    first = collector.collect('\u7075\u5de7\u624b')
    second = collector.collect('\u7075\u5de7\u624b')

    assert len(first) == len(second) == 1
    assert first[0].company == '\u56e0\u65f6\u673a\u5668\u4eba'
    assert first[0].event_type == 'factory_or_capacity'
    assert collector.last_run_summary['errors']
    with sqlite3.connect(state_db) as connection:
        assert connection.execute('SELECT COUNT(*) FROM fixed_evidence').fetchone()[0] == 1
        statuses = dict(connection.execute('SELECT source_id, status FROM source_runs'))
    assert statuses == {'good': 'ok', 'broken': 'error'}


def test_media_source_extracts_quoted_financing_company(tmp_path):
    registry = {'sources': []}
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps(registry), encoding='utf-8')
    collector = FixedSourceCollector(path, tmp_path / 'state.sqlite')
    source = {'name': 'media', 'grade': 'B'}
    evidence = collector._evidence(
        source,
        '\u7075\u5de7\u624b',
        'https://example.com/a',
        '\u300c\u66e6\u8bfa\u672a\u6765\u300d\u5b8c\u6210\u6570\u5343\u4e07\u5143Pre-A\u8f6e\u878d\u8d44',
        '\u4e13\u6ce8\u7075\u5de7\u624b\u4e0e\u5177\u8eab\u667a\u80fd\u91cf\u4ea7',
    )
    assert any(item.company == '\u66e6\u8bfa\u672a\u6765' for item in evidence)

def test_legacy_registry_can_be_scoped_away_from_unrelated_industries(
    tmp_path, monkeypatch
):
    registry = {
        'policy': {'applicable_directions': ['灵巧手', '具身智能']},
        'sources': [{
            'id': 'robot-only',
            'name': 'robot source',
            'list_url': 'https://robot.example/news',
            'company': '机器人公司',
        }],
    }
    path = tmp_path / 'sources.json'
    path.write_text(
        json.dumps(registry, ensure_ascii=False),
        encoding='utf-8',
    )
    collector = FixedSourceCollector(path, tmp_path / 'state.sqlite')
    calls = []
    monkeypatch.setattr(collector, '_fetch', lambda url: calls.append(url))

    assert collector.collect('脑机接口') == []
    assert collector.last_run_summary['skipped'] == 'direction_outside_legacy_scope'
    assert calls == []


def test_media_inference_rejects_product_edition_as_company(tmp_path):
    collector = FixedSourceCollector(
        _write_registry(tmp_path), tmp_path / 'product-state.sqlite'
    )
    evidence = collector._evidence(
        {'name': 'media', 'grade': 'B'},
        '具身智能',
        'https://example.com/product',
        '启元机器人亮相，启元Q1探索者版发布',
        '具身智能新品发布',
    )

    assert all(item.company != '启元Q1探索者版' for item in evidence)

def test_fixed_source_fetch_rejects_oversized_response(tmp_path, monkeypatch):
    class Headers:
        @staticmethod
        def get_content_charset():
            return "utf-8"

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        @staticmethod
        def read(_size=-1):
            return b"12345"

    collector = FixedSourceCollector(
        _write_registry(tmp_path), tmp_path / "bounded.sqlite", max_bytes=4
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())

    try:
        collector._fetch("https://example.com/large")
    except ValueError as error:
        assert "response exceeds 4 bytes" in str(error)
    else:
        raise AssertionError("oversized response was accepted")
