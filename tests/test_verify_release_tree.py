import os
from pathlib import Path
import subprocess

import pytest

from deployment.verify_release_tree import ReleaseTreeError, verify_release_tree


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _release(tmp_path: Path) -> tuple[Path, Path, str]:
    release = tmp_path / "release"
    runtime = tmp_path / "runtime"
    release.mkdir()
    runtime.mkdir()
    _git(release, "init", "-b", "main")
    _git(release, "config", "user.name", "Release Test")
    _git(release, "config", "user.email", "release-test@example.invalid")
    _git(release, "config", "core.autocrlf", "false")
    _git(release, "config", "core.filemode", "false")
    (release / ".gitignore").write_text(
        "*.pyc\n.deployed_git_sha\n.release-manifest.json\n",
        encoding="utf-8",
    )
    (release / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(release, "add", ".")
    _git(release, "commit", "-m", "release")
    return release, runtime, _git(release, "rev-parse", "HEAD")


def test_release_tree_accepts_only_tracked_files_and_regular_metadata(tmp_path):
    release, runtime, sha = _release(tmp_path)
    (release / ".deployed_git_sha").write_text(sha + "\n", encoding="utf-8")
    (release / ".release-manifest.json").write_text("{}\n", encoding="utf-8")

    verify_release_tree(release, sha=sha, runtime_dir=runtime)


@pytest.mark.parametrize("unexpected", ["evil.py", "ignored.pyc"])
def test_release_tree_rejects_untracked_and_ignored_payloads(tmp_path, unexpected):
    release, runtime, sha = _release(tmp_path)
    (release / unexpected).write_text("malicious\n", encoding="utf-8")

    with pytest.raises(ReleaseTreeError, match="unexpected untracked"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)


def test_release_tree_rejects_tracked_modification(tmp_path):
    release, runtime, sha = _release(tmp_path)
    (release / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(ReleaseTreeError, match="tracked files differ"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlinks")
def test_release_tree_requires_exact_runtime_symlink_targets(tmp_path):
    release, runtime, sha = _release(tmp_path)
    for name in ("data", "logs", "backups", "reports-daily"):
        state = runtime / name
        state.mkdir()
        (release / name).symlink_to(state, target_is_directory=True)

    verify_release_tree(release, sha=sha, runtime_dir=runtime)

    (release / "data").unlink()
    (release / "data").symlink_to(runtime / "logs", target_is_directory=True)
    with pytest.raises(ReleaseTreeError, match="target is invalid"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX hard-link semantics")
def test_release_tree_rejects_hardlinked_tracked_and_metadata_files(tmp_path):
    release, runtime, sha = _release(tmp_path)
    original = tmp_path / "same-module.py"
    original.write_bytes((release / "module.py").read_bytes())
    (release / "module.py").unlink()
    os.link(original, release / "module.py")

    with pytest.raises(ReleaseTreeError, match="hard link"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)

    (release / "module.py").unlink()
    (release / "module.py").write_bytes(original.read_bytes())
    marker = release / ".deployed_git_sha"
    marker.write_text(sha + "\n", encoding="utf-8")
    os.link(marker, tmp_path / "marker-copy")
    with pytest.raises(ReleaseTreeError, match="single-link"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)


def test_release_tree_rejects_assume_unchanged_and_skip_worktree_flags(tmp_path):
    release, runtime, sha = _release(tmp_path)
    _git(release, "update-index", "--assume-unchanged", "module.py")
    with pytest.raises(ReleaseTreeError, match="assume-unchanged"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)

    _git(release, "update-index", "--no-assume-unchanged", "module.py")
    _git(release, "update-index", "--skip-worktree", "module.py")
    with pytest.raises(ReleaseTreeError, match="skip-worktree"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)


def test_release_tree_ignores_core_worktree_and_checks_real_release_bytes(tmp_path):
    release, runtime, sha = _release(tmp_path)
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    (alternate / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(release, "config", "core.worktree", str(alternate))
    (release / "module.py").write_text("VALUE = 999\n", encoding="utf-8")

    with pytest.raises(ReleaseTreeError, match="tracked files differ"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)


def test_release_tree_ignores_git_replace_refs(tmp_path):
    release, runtime, trusted = _release(tmp_path)
    (release / "module.py").write_text("VALUE = 999\n", encoding="utf-8")
    _git(release, "add", "module.py")
    _git(release, "commit", "-m", "malicious replacement")
    malicious = _git(release, "rev-parse", "HEAD")
    _git(release, "replace", trusted, malicious)
    _git(release, "checkout", "--detach", trusted)
    assert (release / "module.py").read_text(encoding="utf-8") == "VALUE = 999\n"
    with pytest.raises(ReleaseTreeError, match="tracked files differ"):
        verify_release_tree(release, sha=trusted, runtime_dir=runtime)


@pytest.mark.parametrize(
    "reserved",
    ["data/state.txt", ".deployed_git_sha", ".release-manifest.json/payload"],
)
def test_release_tree_rejects_runtime_or_metadata_committed_to_git(tmp_path, reserved):
    release, runtime, _sha = _release(tmp_path)
    target = release / reserved
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("tracked\n", encoding="utf-8")
    _git(release, "add", "-f", reserved)
    _git(release, "commit", "-m", "track reserved path")
    sha = _git(release, "rev-parse", "HEAD")

    with pytest.raises(ReleaseTreeError, match="must not be tracked"):
        verify_release_tree(release, sha=sha, runtime_dir=runtime)
