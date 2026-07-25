from ht_lead_radar.cli import main


def test_demo_cli_writes_markdown_and_json(tmp_path):
    exit_code = main([
        'run', '--direction', '灵巧手', '--demo', '--output-dir', str(tmp_path),
    ])
    assert exit_code == 0
    markdown = list(tmp_path.glob('*.md'))
    json_files = list(tmp_path.glob('*.json'))
    assert len(markdown) == 1
    assert len(json_files) == 1
    assert '强信号企业：3 家' in markdown[0].read_text(encoding='utf-8')
