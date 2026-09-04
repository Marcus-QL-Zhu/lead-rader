import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = PROJECT_ROOT / "deployment" / "deploy_exact_sha_release.sh"
ROLLBACK = PROJECT_ROOT / "deployment" / "rollback_exact_sha_release.sh"
BOOTSTRAP = PROJECT_ROOT / "deployment" / "bootstrap_legacy_exact_sha_release.sh"
CANONICAL_REPO = "https://github.com/Marcus-QL-Zhu/lead-rader.git"


pytestmark = pytest.mark.skipif(os.name == "nt", reason="requires GNU/POSIX tools")


def _run(command, *, cwd=None, env=None, check=True):
    return subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def _commit(repository: Path, message: str) -> str:
    _run(["git", "add", "."], cwd=repository)
    _run(["git", "commit", "-m", message], cwd=repository)
    return _run(["git", "rev-parse", "HEAD"], cwd=repository).stdout.strip()


def _make_origin(tmp_path: Path) -> tuple[Path, str, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _run(["git", "init", "-b", "main"], cwd=origin)
    _run(["git", "config", "user.name", "Deployment Test"], cwd=origin)
    _run(["git", "config", "user.email", "deployment-test@example.invalid"], cwd=origin)
    shutil.copytree(
        PROJECT_ROOT / "src" / "ht_lead_radar",
        origin / "src" / "ht_lead_radar",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        PROJECT_ROOT / "deployment",
        origin / "deployment",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (origin / "scripts").mkdir()
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "run_lead_radar_v2.py",
        origin / "scripts" / "run_lead_radar_v2.py",
    )
    (origin / "config").mkdir()
    for name in ("fixed-sources.json", "source-packs.json", "openclaw-report-cron.json"):
        (origin / "config" / name).write_text("{}\n", encoding="utf-8")
    artifact = origin / "evaluation" / "production-regression-20260818-31"
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text("{}\n", encoding="utf-8")
    (origin / "RELEASE_VERSION").write_text("one\n", encoding="utf-8")
    first = _commit(origin, "release one")
    (origin / "RELEASE_VERSION").write_text("two\n", encoding="utf-8")
    second = _commit(origin, "release two")
    assert re.fullmatch(r"[0-9a-f]{40}", first)
    assert re.fullmatch(r"[0-9a-f]{40}", second)
    return origin, first, second


def _make_test_wrappers(tmp_path: Path, real_git: str, real_mv: str) -> Path:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    git_wrapper = binary_dir / "git"
    git_wrapper.write_text(
        f"""#!{sys.executable}
import os
from pathlib import Path
import signal
import subprocess
import sys

REAL_GIT = {real_git!r}
CANONICAL = {CANONICAL_REPO!r}
args = sys.argv[1:]
filtered = []
i = 0
while i < len(args):
    if i + 1 < len(args) and args[i] == '-c' and args[i + 1] == 'protocol.file.allow=never':
        i += 2
        continue
    filtered.append(os.environ['LOCAL_GIT_ORIGIN'] if args[i] == CANONICAL else args[i])
    i += 1
environment = dict(os.environ)
for key in ('GIT_ALLOW_PROTOCOL', 'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL'):
    environment.pop(key, None)
completed = subprocess.run([REAL_GIT, *filtered], env=environment)
if completed.returncode == 0 and 'clone' in filtered:
    destination = Path(filtered[-1])
    completed = subprocess.run(
        [REAL_GIT, '-C', str(destination), 'remote', 'set-url', 'origin', CANONICAL],
        env=environment,
    )
if completed.returncode == 0 and os.environ.get('INTERRUPT_AFTER_GIT_STAGE') == '1' and 'clone' in filtered:
    os.kill(os.getppid(), signal.SIGTERM)
raise SystemExit(completed.returncode)
""",
        encoding="utf-8",
    )
    git_wrapper.chmod(0o700)

    python_wrapper = binary_dir / "test-python"
    python_wrapper.write_text(
        f"""#!{sys.executable}
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

REAL_PYTHON = {sys.executable!r}
args = sys.argv[1:]
if args and Path(args[0]).name == 'verify_github_ci.py':
    raise SystemExit(0 if len(args) == 2 and re.fullmatch(r'[0-9a-f]{{40}}', args[1]) else 64)
if (
    args
    and Path(args[0]).name == 'smoke_release.py'
    and '--expected-realpath' in args
    and os.environ.get('FAIL_POST_SMOKE') == '1'
    and Path(args[args.index('--expected-realpath') + 1]).name
        == os.environ.get('FAIL_POST_SMOKE_SHA')
):
    raise SystemExit(74)
if args and Path(args[0]).name in ('validate_runtime_env.py', 'smoke_release.py'):
    import importlib.util
    sys.path.insert(0, str(Path(args[0]).resolve().parent))
    spec = importlib.util.spec_from_file_location('test_injected_' + Path(args[0]).stem, args[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raise SystemExit(module.main(args[1:], expected_owner_uid=os.geteuid(), expected_owner_gid=os.getegid()))
if (
    args and Path(args[0]).name == 'release_metadata.py'
    and 'write' in args and os.environ.get('INTERRUPT_AFTER_METADATA_WRITE')
):
    completed = subprocess.run([REAL_PYTHON, *args])
    if completed.returncode:
        raise SystemExit(completed.returncode)
    os.kill(os.getppid(), getattr(signal, 'SIG' + os.environ['INTERRUPT_SIGNAL']))
    raise SystemExit(0)
os.execv(REAL_PYTHON, [REAL_PYTHON, *args])
""",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o700)

    mv_wrapper = binary_dir / "mv"
    mv_wrapper.write_text(
        f"""#!{sys.executable}
import os
from pathlib import Path
import signal
import subprocess
import sys

REAL_MV = {real_mv!r}
args = sys.argv[1:]
if (
    os.environ.get('FAIL_METADATA_MOVE') == '1'
    and args
    and Path(args[-1]).name == '.release-manifest.json'
):
    raise SystemExit(1)
marker = os.environ.get('INTERRUPT_AFTER_LIVE_MV_MARKER')
interrupt_destination = os.environ.get('INTERRUPT_AFTER_MV_DEST', 'hardtech-lead-radar')
if marker and args and Path(args[-1]).name == interrupt_destination and not Path(marker).exists():
    completed = subprocess.run([REAL_MV, *args])
    if completed.returncode:
        raise SystemExit(completed.returncode)
    Path(marker).write_text('sent', encoding='utf-8')
    os.kill(os.getppid(), getattr(signal, 'SIG' + os.environ.get('INTERRUPT_SIGNAL', 'TERM')))
    raise SystemExit(0)
os.execv(REAL_MV, [REAL_MV, *args])
""",
        encoding="utf-8",
    )
    mv_wrapper.chmod(0o700)
    return python_wrapper


def _deployment_command(
    sha: str,
    *,
    releases: Path,
    live: Path,
    runtime: Path,
    env_file: Path,
    josint_db: Path,
    python: Path,
) -> list[str]:
    _prepare_runtime(runtime)
    return [
        "/bin/sh",
        str(DEPLOY),
        "--sha",
        sha,
        "--releases-dir",
        str(releases),
        "--live-link",
        str(live),
        "--runtime-dir",
        str(runtime),
        "--env-file",
        str(env_file),
        "--josint-db",
        str(josint_db),
        "--python",
        str(python),
    ]


def _prepare_runtime(runtime: Path) -> None:
    data = runtime / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in (
        "fixed-sources.sqlite",
        "facts.sqlite",
        "runtime.sqlite",
        "relationships.sqlite",
        "search-budget.sqlite",
        "feishu-projection.sqlite",
        "audit.sqlite",
        "ops-metrics.sqlite",
        "talent-pool.sqlite",
        "feishu-notifications.sqlite",
    ):
        path = data / name
        if path.exists():
            continue
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE sample (value TEXT)")


def _rollback_command(sha, *, releases, live, runtime, env_file, josint_db, python):
    return [
        "/bin/sh", str(ROLLBACK), "--sha", sha,
        "--releases-dir", str(releases), "--live-link", str(live),
        "--runtime-dir", str(runtime), "--env-file", str(env_file),
        "--josint-db", str(josint_db), "--python", str(python),
    ]


def _bootstrap_command(
    sha, *, releases, live, runtime, env_file, josint_db, python, script=BOOTSTRAP
):
    return [
        "/bin/sh", str(script), "--sha", sha,
        "--releases-dir", str(releases), "--live-path", str(live),
        "--runtime-dir", str(runtime), "--env-file", str(env_file),
        "--josint-db", str(josint_db), "--python", str(python),
    ]


def _legacy_tree(live: Path) -> None:
    _prepare_runtime(live)
    for name in ("logs", "backups", "reports-daily", "reports-archive"):
        (live / name).mkdir(parents=True)
        (live / name / "preserved.txt").write_text(name, encoding="utf-8")
    config = live / "config"
    config.mkdir()
    for name in ("fixed-sources.json", "source-packs.json", "openclaw-report-cron.json"):
        (config / name).write_text('{"legacy":true}\n', encoding="utf-8")
    (live / "legacy-source.txt").write_text("recoverable", encoding="utf-8")


def _bootstrap_tool(tmp_path: Path, origin: Path, sha: str, real_git: str) -> Path:
    tool = tmp_path / f"bootstrap-tool-{sha[:8]}"
    _run([real_git, "clone", str(origin), str(tool)])
    _run([real_git, "-C", tool, "checkout", "--detach", sha])
    _run([real_git, "-C", tool, "remote", "set-url", "origin", CANONICAL_REPO])
    return tool / "deployment" / "bootstrap_legacy_exact_sha_release.sh"


def test_release_transaction_lock_serializes_deploy_and_rollback(tmp_path):
    import fcntl

    real_git, real_mv = shutil.which("git"), shutil.which("mv")
    if not real_git or not real_mv or not shutil.which("flock"):
        pytest.skip("git/mv/flock unavailable")
    origin, first_sha, second_sha = _make_origin(tmp_path)
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    releases, runtime = tmp_path / "releases", tmp_path / "runtime"
    runtime.mkdir()
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    environment = dict(os.environ)
    environment.update(
        {
            "LOCAL_GIT_ORIGIN": str(origin),
            "PATH": f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}",
        }
    )
    common = {
        "releases": releases,
        "live": live,
        "runtime": runtime,
        "env_file": env_file,
        "josint_db": database,
        "python": python_wrapper,
    }
    first = _run(_deployment_command(first_sha, **common), env=environment, check=False)
    assert first.returncode == 0, first.stderr
    assert live.resolve() == (releases / first_sha).resolve()

    lock_path = runtime / ".release-transaction.lock"
    with lock_path.open("r+") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        competing_deploy = _run(
            _deployment_command(second_sha, **common), env=environment, check=False
        )
        competing_rollback = _run(
            _rollback_command(first_sha, **common), env=environment, check=False
        )
        assert competing_deploy.returncode == 75
        assert competing_rollback.returncode == 75
        assert "another release transaction is active" in competing_deploy.stderr
        assert "another release transaction is active" in competing_rollback.stderr
        assert live.resolve() == (releases / first_sha).resolve()
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)

    committed = _run(
        _deployment_command(second_sha, **common), env=environment, check=False
    )
    assert committed.returncode == 0, committed.stderr
    assert live.resolve() == (releases / second_sha).resolve()


def test_deploy_fails_closed_when_production_backup_gate_cannot_run(tmp_path):
    real_git, real_mv = shutil.which("git"), shutil.which("mv")
    if not real_git or not real_mv or not shutil.which("flock"):
        pytest.skip("git/mv/flock unavailable")
    origin, first_sha, _ = _make_origin(tmp_path)
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    releases, runtime = tmp_path / "releases", tmp_path / "runtime"
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    environment = dict(os.environ)
    environment.update(
        {
            "LOCAL_GIT_ORIGIN": str(origin),
            "PATH": f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}",
        }
    )
    command = _deployment_command(
        first_sha,
        releases=releases,
        live=live,
        runtime=runtime,
        env_file=env_file,
        josint_db=database,
        python=python_wrapper,
    )
    (runtime / "data" / "talent-pool.sqlite").unlink()
    blocked = _run(command, env=environment, check=False)
    assert blocked.returncode == 74
    assert "production backup gate failed" in blocked.stderr
    assert not live.exists() and not live.is_symlink()
    assert not (releases / first_sha / ".deployed_git_sha").exists()


def test_artifact_free_source_commit_is_explicitly_not_deployable(tmp_path):
    real_git, real_mv = shutil.which("git"), shutil.which("mv")
    if not real_git or not real_mv or not shutil.which("flock"):
        pytest.skip("git/mv/flock unavailable")
    origin, _first_sha, _second_sha = _make_origin(tmp_path)
    shutil.rmtree(origin / "evaluation" / "production-regression-20260818-31")
    artifact_free_sha = _commit(origin, "source A without frozen artifact")
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n", encoding="utf-8"
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    runtime = tmp_path / "runtime"
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    environment = dict(os.environ)
    environment.update({
        "LOCAL_GIT_ORIGIN": str(origin),
        "PATH": f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}",
    })

    result = _run(
        _deployment_command(
            artifact_free_sha, releases=tmp_path / "releases", live=live,
            runtime=runtime, env_file=env_file, josint_db=database,
            python=python_wrapper,
        ),
        env=environment,
        check=False,
    )
    assert result.returncode == 74
    assert "source commit A is not deployable" in result.stderr
    assert not live.exists() and not live.is_symlink()


def test_successful_rollback_points_to_release_just_left(tmp_path):
    real_git, real_mv = shutil.which("git"), shutil.which("mv")
    if not real_git or not real_mv:
        pytest.skip("git/mv unavailable")
    origin, first_sha, second_sha = _make_origin(tmp_path)
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n", encoding="utf-8"
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    releases, runtime = tmp_path / "releases", tmp_path / "runtime"
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    environment = dict(os.environ)
    environment.update({"LOCAL_GIT_ORIGIN": str(origin), "PATH": f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}"})
    for sha in (first_sha, second_sha):
        completed = _run(_deployment_command(sha, releases=releases, live=live, runtime=runtime, env_file=env_file, josint_db=database, python=python_wrapper), env=environment, check=False)
        assert completed.returncode == 0, completed.stderr
    second_backup = next(
        json.loads(path.read_text(encoding="utf-8"))
        for path in (runtime / "backups").glob("*/manifest.json")
        if json.loads(path.read_text(encoding="utf-8"))["git_sha"] == second_sha
    )
    manifest_sources = {
        Path(item["source_path"])
        for item in second_backup["items"]
        if item["kind"] == "source_manifest"
    }
    assert manifest_sources == {
        releases / first_sha / "config" / name
        for name in ("fixed-sources.json", "source-packs.json", "openclaw-report-cron.json")
    }
    assert any(
        Path(item["source_path"]).name == "relationships.sqlite"
        for item in second_backup["items"]
        if item["kind"] == "sqlite"
    )
    before_rollback_backups = set((runtime / "backups").glob("*/manifest.json"))
    rolled_back = _run(_rollback_command(first_sha, releases=releases, live=live, runtime=runtime, env_file=env_file, josint_db=database, python=python_wrapper), env=environment, check=False)
    assert rolled_back.returncode == 0, rolled_back.stderr
    after_rollback_backups = set((runtime / "backups").glob("*/manifest.json"))
    assert len(after_rollback_backups) == len(before_rollback_backups) + 1
    rollback_manifest_path = next(iter(after_rollback_backups - before_rollback_backups))
    rollback_manifest = json.loads(rollback_manifest_path.read_text(encoding="utf-8"))
    assert any(
        Path(item["source_path"]).name == "relationships.sqlite"
        for item in rollback_manifest["items"]
        if item["kind"] == "sqlite"
    )
    assert live.resolve() == (releases / first_sha).resolve()
    assert (runtime / ".previous_release_target").read_text(encoding="utf-8") == f"{releases / second_sha}\n"
    pointer_before = (runtime / ".previous_release_target").read_bytes()
    interrupted_environment = dict(environment)
    interrupted_environment["INTERRUPT_AFTER_LIVE_MV_MARKER"] = str(tmp_path / "rollback-interrupt.sent")
    interrupted = _run(_rollback_command(second_sha, releases=releases, live=live, runtime=runtime, env_file=env_file, josint_db=database, python=python_wrapper), env=interrupted_environment, check=False)
    assert interrupted.returncode != 0
    assert live.resolve() == (releases / first_sha).resolve()
    assert (runtime / ".previous_release_target").read_bytes() == pointer_before


def test_exact_sha_activation_rejects_drift_and_rolls_back_post_smoke(tmp_path):
    real_git = shutil.which("git")
    real_mv = shutil.which("mv")
    if not real_git or not real_mv:
        pytest.skip("git/mv unavailable")
    origin, first_sha, second_sha = _make_origin(tmp_path)
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=integration-placeholder\n"
        "FEISHU_APP_ID=integration-placeholder\n"
        "FEISHU_APP_SECRET=integration-placeholder\n"
        "FEISHU_NOTIFY_RECEIVE_ID=integration-placeholder\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    releases = tmp_path / "releases"
    runtime = tmp_path / "runtime"
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    environment = dict(os.environ)
    environment["LOCAL_GIT_ORIGIN"] = str(origin)
    environment["PATH"] = f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}"

    staging_environment = dict(environment)
    staging_environment["INTERRUPT_AFTER_GIT_STAGE"] = "1"
    interrupted_staging = _run(
        _deployment_command(
            first_sha,
            releases=releases,
            live=live,
            runtime=runtime,
            env_file=env_file,
            josint_db=database,
            python=python_wrapper,
        ),
        env=staging_environment,
        check=False,
    )
    assert interrupted_staging.returncode != 0
    assert not (releases / first_sha).exists()
    assert not list(releases.glob(f".incoming-{first_sha}-*"))

    first = _run(
        _deployment_command(
            first_sha,
            releases=releases,
            live=live,
            runtime=runtime,
            env_file=env_file,
            josint_db=database,
            python=python_wrapper,
        ),
        env=environment,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert live.resolve() == (releases / first_sha).resolve()
    assert (live / ".deployed_git_sha").read_text().strip() == first_sha
    assert not list((releases / first_sha).rglob("__pycache__"))
    assert not list((releases / first_sha).rglob("*.pyc"))
    previous_before = (runtime / ".previous_release_target").read_bytes()

    failing_environment = dict(environment)
    failing_environment["FAIL_POST_SMOKE"] = "1"
    failing_environment["FAIL_POST_SMOKE_SHA"] = second_sha
    second = _run(
        _deployment_command(
            second_sha,
            releases=releases,
            live=live,
            runtime=runtime,
            env_file=env_file,
            josint_db=database,
            python=python_wrapper,
        ),
        env=failing_environment,
        check=False,
    )
    assert second.returncode == 74
    assert "previous release restored" in second.stderr
    assert live.resolve() == (releases / first_sha).resolve()
    assert not (releases / second_sha / ".deployed_git_sha").exists()
    assert (runtime / ".previous_release_target").read_bytes() == previous_before

    for stage, signal_name in (
        ("metadata-write", "TERM"),
        (".deployed_git_sha", "HUP"),
        (".release-manifest.json", "INT"),
        (".previous_release_target", "TERM"),
    ):
        stage_environment = dict(environment)
        stage_environment["INTERRUPT_SIGNAL"] = signal_name
        if stage == "metadata-write":
            stage_environment["INTERRUPT_AFTER_METADATA_WRITE"] = "1"
        else:
            stage_environment["INTERRUPT_AFTER_MV_DEST"] = stage
            stage_environment["INTERRUPT_AFTER_LIVE_MV_MARKER"] = str(
                tmp_path / f"{stage}.sent"
            )
        interrupted_stage = _run(
            _deployment_command(
                second_sha,
                releases=releases,
                live=live,
                runtime=runtime,
                env_file=env_file,
                josint_db=database,
                python=python_wrapper,
            ),
            env=stage_environment,
            check=False,
        )
        assert interrupted_stage.returncode != 0
        assert live.resolve() == (releases / first_sha).resolve()
        assert (runtime / ".previous_release_target").read_bytes() == previous_before
        assert not (releases / second_sha / ".deployed_git_sha").exists()
        assert not (releases / second_sha / ".release-manifest.json").exists()
        assert not list((releases / second_sha).glob("*.next.*"))
        assert not list(runtime.glob(".previous_release_target.next.*"))
        assert not list(runtime.glob(".previous_release_target.backup.*"))

    interrupted_environment = dict(environment)
    interrupted_environment["INTERRUPT_AFTER_LIVE_MV_MARKER"] = str(tmp_path / "interrupt.sent")
    interrupted = _run(
        _deployment_command(
            second_sha,
            releases=releases,
            live=live,
            runtime=runtime,
            env_file=env_file,
            josint_db=database,
            python=python_wrapper,
        ),
        env=interrupted_environment,
        check=False,
    )
    assert interrupted.returncode != 0
    assert live.resolve() == (releases / first_sha).resolve()
    assert not (releases / second_sha / ".deployed_git_sha").exists()
    assert not (releases / second_sha / ".release-manifest.json").exists()
    assert (runtime / ".previous_release_target").read_bytes() == previous_before
    assert not list((releases / second_sha).glob("*.next.*"))
    assert not list(runtime.glob(".previous_release_target.next.*"))
    assert not list(runtime.glob(".previous_release_target.backup.*"))

    metadata_environment = dict(environment)
    metadata_environment["FAIL_METADATA_MOVE"] = "1"
    metadata_failure = _run(
        _deployment_command(
            second_sha,
            releases=releases,
            live=live,
            runtime=runtime,
            env_file=env_file,
            josint_db=database,
            python=python_wrapper,
        ),
        env=metadata_environment,
        check=False,
    )
    assert metadata_failure.returncode == 74
    assert "metadata activation failed" in metadata_failure.stderr
    assert live.resolve() == (releases / first_sha).resolve()
    assert not (releases / second_sha / ".deployed_git_sha").exists()
    assert not (releases / second_sha / ".release-manifest.json").exists()
    assert (runtime / ".previous_release_target").read_bytes() == previous_before
    assert not list((releases / second_sha).glob("*.next.*"))
    assert not list(runtime.glob(".previous_release_target.next.*"))
    assert not list(runtime.glob(".previous_release_target.backup.*"))

    unexpected = releases / second_sha / "evil.py"
    unexpected.write_text("raise RuntimeError('must never import')\n", encoding="utf-8")
    untracked_payload = _run(
        _deployment_command(
            second_sha,
            releases=releases,
            live=live,
            runtime=runtime,
            env_file=env_file,
            josint_db=database,
            python=python_wrapper,
        ),
        env=environment,
        check=False,
    )
    assert untracked_payload.returncode == 74
    assert "unexpected untracked content" in untracked_payload.stderr
    assert live.resolve() == (releases / first_sha).resolve()
    unexpected.unlink()

    bad_runtime = tmp_path / "bad-runtime"
    bad_runtime.mkdir()
    (bad_runtime / "data").symlink_to(runtime / "data", target_is_directory=True)
    symlinked_state = _run(
        _deployment_command(
            second_sha,
            releases=releases,
            live=live,
            runtime=bad_runtime,
            env_file=env_file,
            josint_db=database,
            python=python_wrapper,
        ),
        env=environment,
        check=False,
    )
    assert symlinked_state.returncode == 74
    assert (
        "runtime state child must not be a symlink" in symlinked_state.stderr
        or "runtime release symlink target is invalid" in symlinked_state.stderr
        or "runtime data must be a real directory" in symlinked_state.stderr
    )
    assert live.resolve() == (releases / first_sha).resolve()

    _run(
        [
            real_git,
            "-C",
            releases / second_sha,
            "remote",
            "set-url",
            "origin",
            "ssh://github.com/Marcus-QL-Zhu/lead-rader.git",
        ]
    )
    wrong_origin = _run(
        _deployment_command(
            second_sha,
            releases=releases,
            live=live,
            runtime=runtime,
            env_file=env_file,
            josint_db=database,
            python=python_wrapper,
        ),
        env=environment,
        check=False,
    )
    assert wrong_origin.returncode == 74
    assert "origin is not canonical" in wrong_origin.stderr
    assert live.resolve() == (releases / first_sha).resolve()


def test_deploy_and_rollback_refuse_an_active_daily_task_lock(tmp_path):
    import fcntl

    real_git, real_mv = shutil.which("git"), shutil.which("mv")
    if not real_git or not real_mv or not shutil.which("flock"):
        pytest.skip("git/mv/flock unavailable")
    origin, first_sha, second_sha = _make_origin(tmp_path)
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n", encoding="utf-8"
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    releases, runtime = tmp_path / "releases", tmp_path / "runtime"
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    environment = dict(os.environ)
    environment.update({
        "LOCAL_GIT_ORIGIN": str(origin),
        "PATH": f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}",
    })
    common = dict(
        releases=releases, live=live, runtime=runtime, env_file=env_file,
        josint_db=database, python=python_wrapper,
    )
    first = _run(_deployment_command(first_sha, **common), env=environment, check=False)
    assert first.returncode == 0, first.stderr

    daily_lock = runtime / "data" / "daily-task.lock"
    with daily_lock.open("r+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        deploy = _run(_deployment_command(second_sha, **common), env=environment, check=False)
        rollback = _run(_rollback_command(first_sha, **common), env=environment, check=False)
        assert deploy.returncode == 75
        assert rollback.returncode == 75
        assert "daily task is active" in deploy.stderr
        assert "daily task is active" in rollback.stderr
        assert live.resolve() == (releases / first_sha).resolve()


def test_bootstrap_migrates_legacy_state_and_retains_verified_archive(tmp_path):
    real_git, real_mv = shutil.which("git"), shutil.which("mv")
    if not real_git or not real_mv or not shutil.which("flock"):
        pytest.skip("git/mv/flock unavailable")
    origin, first_sha, _ = _make_origin(tmp_path)
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    bootstrap_script = _bootstrap_tool(tmp_path, origin, first_sha, real_git)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n", encoding="utf-8"
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    releases, runtime = tmp_path / "releases", tmp_path / "runtime"
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    live.mkdir()
    _legacy_tree(live)
    environment = dict(os.environ)
    environment.update({
        "LOCAL_GIT_ORIGIN": str(origin),
        "PATH": f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}",
    })

    result = _run(
        _bootstrap_command(
            first_sha, releases=releases, live=live, runtime=runtime,
            env_file=env_file, josint_db=database, python=python_wrapper,
            script=bootstrap_script,
        ),
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert live.is_symlink()
    assert live.resolve() == (releases / first_sha).resolve()
    for name in ("data", "logs", "backups", "reports-daily"):
        assert (releases / first_sha / name).is_symlink()
        assert (releases / first_sha / name).resolve() == (runtime / name).resolve()
    assert (runtime / "reports-archive" / "preserved.txt").read_text() == "reports-archive"
    archives = list((runtime / "legacy-source-archives").glob(f"*-{first_sha}"))
    assert len(archives) == 1
    assert (archives[0] / "legacy-source.txt").read_text() == "recoverable"
    assert not (archives[0] / "data").exists()
    backup_manifests = list((runtime / "backups").glob("*/manifest.json"))
    assert len(backup_manifests) >= 2
    assert all(
        any(
            Path(item["source_path"]).name == "relationships.sqlite"
            for item in json.loads(path.read_text(encoding="utf-8"))["items"]
            if item["kind"] == "sqlite"
        )
        for path in backup_manifests
    )
    assert any(
        any(
            str(item["source_path"]).startswith(str(archives[0] / "config"))
            or "/hardtech-lead-radar/config/" in str(item["source_path"])
            for item in json.loads(path.read_text(encoding="utf-8"))["items"]
            if item["kind"] == "source_manifest"
        )
        for path in backup_manifests
    )


def test_bootstrap_failure_restores_legacy_layout_and_metadata(tmp_path):
    real_git, real_mv = shutil.which("git"), shutil.which("mv")
    if not real_git or not real_mv or not shutil.which("flock"):
        pytest.skip("git/mv/flock unavailable")
    origin, first_sha, _ = _make_origin(tmp_path)
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    bootstrap_script = _bootstrap_tool(tmp_path, origin, first_sha, real_git)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n", encoding="utf-8"
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    releases, runtime = tmp_path / "releases", tmp_path / "runtime"
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    live.mkdir()
    _legacy_tree(live)
    environment = dict(os.environ)
    environment.update({
        "LOCAL_GIT_ORIGIN": str(origin),
        "PATH": f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}",
        "INTERRUPT_AFTER_LIVE_MV_MARKER": str(tmp_path / "bootstrap-interrupt.sent"),
        "INTERRUPT_SIGNAL": "TERM",
    })

    result = _run(
        _bootstrap_command(
            first_sha, releases=releases, live=live, runtime=runtime,
            env_file=env_file, josint_db=database, python=python_wrapper,
            script=bootstrap_script,
        ),
        env=environment,
        check=False,
    )
    assert result.returncode != 0, result.stderr
    assert live.is_dir() and not live.is_symlink()
    assert (live / "legacy-source.txt").read_text() == "recoverable"
    for name in ("data", "logs", "backups", "reports-daily", "reports-archive"):
        assert (live / name).is_dir()
        assert not (runtime / name).exists()
    assert not (runtime / ".previous_release_target").exists()
    assert not (releases / first_sha / ".deployed_git_sha").exists()
    assert not (releases / first_sha / ".release-manifest.json").exists()


def test_bootstrap_refuses_competing_release_or_legacy_daily_lock(tmp_path):
    import fcntl

    real_git, real_mv = shutil.which("git"), shutil.which("mv")
    if not real_git or not real_mv or not shutil.which("flock"):
        pytest.skip("git/mv/flock unavailable")
    origin, first_sha, _ = _make_origin(tmp_path)
    python_wrapper = _make_test_wrappers(tmp_path, real_git, real_mv)
    bootstrap_script = _bootstrap_tool(tmp_path, origin, first_sha, real_git)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text(
        "METASO_API_KEY=x\nFEISHU_APP_ID=x\nFEISHU_APP_SECRET=x\n"
        "FEISHU_NOTIFY_RECEIVE_ID=x\n", encoding="utf-8"
    )
    env_file.chmod(0o600)
    database = tmp_path / "jobs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (title TEXT)")
    releases, runtime = tmp_path / "releases", tmp_path / "runtime"
    runtime.mkdir()
    live_parent = tmp_path / "live"
    live_parent.mkdir()
    live = live_parent / "hardtech-lead-radar"
    live.mkdir()
    _legacy_tree(live)
    environment = dict(os.environ)
    environment.update({
        "LOCAL_GIT_ORIGIN": str(origin),
        "PATH": f"{python_wrapper.parent}{os.pathsep}{environment['PATH']}",
    })
    command = _bootstrap_command(
        first_sha, releases=releases, live=live, runtime=runtime,
        env_file=env_file, josint_db=database, python=python_wrapper,
        script=bootstrap_script,
    )

    release_lock = runtime / ".release-transaction.lock"
    release_lock.touch(mode=0o600)
    with release_lock.open("r+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = _run(command, env=environment, check=False)
        assert blocked.returncode == 75
        assert "release transaction is active" in blocked.stderr

    daily_lock = live / "data" / "daily-task.lock"
    daily_lock.touch(mode=0o600)
    daily_lock.chmod(0o600)
    with daily_lock.open("r+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocked = _run(command, env=environment, check=False)
        assert blocked.returncode == 75
        assert "daily task is active" in blocked.stderr
    assert live.is_dir() and not live.is_symlink()
    assert (live / "data" / "fixed-sources.sqlite").is_file()
