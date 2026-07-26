from pathlib import Path


def test_daily_launcher_pins_supported_server_python_and_checks_version():
    script = (
        Path(__file__).parents[1] / "scripts" / "run_daily_fixed_sources.sh"
    ).read_text(encoding="utf-8")

    assert "/home/admin/.pyenv/versions/3.11.14/bin/python3" in script
    assert "sys.version_info >= (3, 10)" in script
    assert 'PYTHON_BIN="${PYTHON_BIN:-python3}"' not in script
