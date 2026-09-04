import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="requires POSIX modes and /bin/sh")


def test_public_launcher_always_uses_validated_file_and_ignores_ambient_sentinel(tmp_path):
    app = tmp_path / "hardtech-lead-radar"
    scripts = app / "scripts"
    deployment = app / "deployment"
    scripts.mkdir(parents=True)
    deployment.mkdir()
    outer_source = (PROJECT_ROOT / "scripts" / "run_daily_fixed_sources.sh").read_text(
        encoding="utf-8"
    )
    outer_source = outer_source.replace(
        'SERVER_PYTHON="/home/admin/.pyenv/versions/3.11.14/bin/python3"',
        f'SERVER_PYTHON="{sys.executable}"',
    )
    (scripts / "run_daily_fixed_sources.sh").write_text(
        outer_source,
        encoding="utf-8",
    )
    for name in ("exec_with_runtime_env.py", "consume_runtime_capability.py"):
        shutil.copy2(PROJECT_ROOT / "deployment" / name, deployment)
    validator = (PROJECT_ROOT / "deployment" / "validate_runtime_env.py").read_text(
        encoding="utf-8"
    ).replace(
        "identity_getter: Callable[[], tuple[int, int]] = _service_identity,",
        "identity_getter: Callable[[], tuple[int, int]] = lambda: (os.geteuid(), os.getegid()),",
    )
    (deployment / "validate_runtime_env.py").write_text(validator, encoding="utf-8")
    inner = scripts / "run_daily_fixed_sources_inner.sh"
    inner.write_text(
        "#!/bin/sh\n"
        f'"{sys.executable}" "{deployment / "consume_runtime_capability.py"}" || exit 64\n'
        "test \"$FEISHU_APP_ID\" = file-app || exit 21\n"
        "test \"$FEISHU_APP_SECRET\" = file-secret || exit 22\n"
        "test \"$FEISHU_NOTIFY_RECEIVE_ID\" = file-user || exit 23\n"
        "test \"$METASO_API_KEY\" = file-metaso || exit 24\n"
        "test -z \"${MINIMAX_API_KEY:-}\" || exit 25\n"
        "echo boundary-passed\n",
        encoding="utf-8",
    )
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "FEISHU_APP_ID=file-app\n"
        "FEISHU_APP_SECRET=file-secret\n"
        "FEISHU_NOTIFY_RECEIVE_ID=file-user\n"
        "METASO_API_KEY=file-metaso\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    environment = dict(os.environ)
    environment.update(
        {
            "HT_LEAD_APP_DIR": str(app),
            "HT_LEAD_ENV_FILE": str(env_file),
            "HT_LEAD_RUNTIME_ENV_LOADED": "1",
            "PYTHON_BIN": sys.executable,
            "LD_PRELOAD": "/attacker/lib.so",
            "BASH_ENV": "/attacker/bashenv",
            "PYTHONPATH": "/attacker/python",
            "OPENCLAW_CONFIG_PATH": "/attacker/openclaw.json",
            "FEISHU_APP_SECRET": "ambient-secret-must-not-win",
            "MINIMAX_API_KEY": "ambient-provider-key-must-not-reach-child",
        }
    )

    completed = subprocess.run(
        ["/bin/sh", str(scripts / "run_daily_fixed_sources.sh")],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "boundary-passed"
    assert "ambient-secret-must-not-win" not in completed.stdout + completed.stderr


def test_inner_launcher_rejects_direct_invocation(tmp_path):
    app = tmp_path / "hardtech-lead-radar"
    (app / "scripts").mkdir(parents=True)
    (app / "deployment").mkdir()
    shutil.copy2(PROJECT_ROOT / "scripts" / "run_daily_fixed_sources_inner.sh", app / "scripts")
    for name in ("exec_with_runtime_env.py", "consume_runtime_capability.py", "validate_runtime_env.py"):
        shutil.copy2(PROJECT_ROOT / "deployment" / name, app / "deployment")
    inner_path = app / "scripts" / "run_daily_fixed_sources_inner.sh"
    inner_text = inner_path.read_text(encoding="utf-8").replace(
        'SERVER_PYTHON="/home/admin/.pyenv/versions/3.11.14/bin/python3"',
        f'SERVER_PYTHON="{sys.executable}"',
    )
    inner_path.write_text(inner_text, encoding="utf-8")
    completed = subprocess.run(
        ["/bin/sh", str(app / "scripts" / "run_daily_fixed_sources_inner.sh")],
        env={"PATH": "/usr/bin:/bin", "HT_LEAD_RUNTIME_CAPABILITY_FD": "999"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 64
    assert "capability rejected" in completed.stderr


def test_stable_symlink_executes_exact_sha_release_through_real_capability(tmp_path):
    sha = "a" * 40
    skills = tmp_path / "skills"
    releases = skills / "hardtech-lead-radar-releases"
    app = releases / sha
    (app / "scripts").mkdir(parents=True)
    (app / "deployment").mkdir()
    outer = (PROJECT_ROOT / "scripts" / "run_daily_fixed_sources.sh").read_text(encoding="utf-8").replace(
        'SERVER_PYTHON="/home/admin/.pyenv/versions/3.11.14/bin/python3"',
        f'SERVER_PYTHON="{sys.executable}"',
    )
    (app / "scripts" / "run_daily_fixed_sources.sh").write_text(outer, encoding="utf-8")
    inner = (PROJECT_ROOT / "scripts" / "run_daily_fixed_sources_inner.sh").read_text(encoding="utf-8").replace(
        'SERVER_PYTHON="/home/admin/.pyenv/versions/3.11.14/bin/python3"',
        f'SERVER_PYTHON="{sys.executable}"',
    ).replace(
        "/home/admin/.openclaw/workspace/skills/hardtech-lead-radar-releases/*)",
        f"{releases}/*)",
    )
    inner = inner.split("unset HT_LEAD_RUNTIME_CAPABILITY_FD", 1)[0] + "unset HT_LEAD_RUNTIME_CAPABILITY_FD\necho exact-release-capability-passed\nexit 0\n"
    (app / "scripts" / "run_daily_fixed_sources_inner.sh").write_text(inner, encoding="utf-8")
    for name in ("exec_with_runtime_env.py", "consume_runtime_capability.py"):
        shutil.copy2(PROJECT_ROOT / "deployment" / name, app / "deployment")
    validator = (PROJECT_ROOT / "deployment" / "validate_runtime_env.py").read_text(encoding="utf-8").replace(
        "identity_getter: Callable[[], tuple[int, int]] = _service_identity,",
        "identity_getter: Callable[[], tuple[int, int]] = lambda: (os.geteuid(), os.getegid()),",
    )
    (app / "deployment" / "validate_runtime_env.py").write_text(validator, encoding="utf-8")
    stable = skills / "hardtech-lead-radar"
    stable.symlink_to(app, target_is_directory=True)
    secrets = tmp_path / "secrets-release"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "FEISHU_APP_ID=id\nFEISHU_APP_SECRET=secret\n"
        "FEISHU_NOTIFY_RECEIVE_ID=receiver\nMETASO_API_KEY=metaso\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    completed = subprocess.run(
        ["/bin/sh", str(stable / "scripts" / "run_daily_fixed_sources.sh")],
        env={"PATH": "/usr/bin:/bin", "HT_LEAD_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "exact-release-capability-passed"
    assert not list(app.rglob("__pycache__"))
    assert not list(app.rglob("*.pyc"))
