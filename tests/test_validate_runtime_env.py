import os
import stat
from types import SimpleNamespace

import pytest
import deployment.validate_runtime_env as runtime_env

from deployment.validate_runtime_env import (
    UnsafeRuntimeEnvError,
    load_validated_runtime_env,
    open_validated_runtime_env,
    validate_runtime_env_file,
    validate_runtime_env_metadata,
)


def _metadata(mode: int, uid: int = 1001, *, gid: int = 1001, inode: int = 1, nlink: int = 1):
    return SimpleNamespace(
        st_mode=mode,
        st_uid=uid,
        st_gid=gid,
        st_dev=7,
        st_ino=inode,
        st_nlink=nlink,
    )


def _owner_kwargs():
    return {"expected_owner_uid": os.geteuid(), "expected_owner_gid": os.getegid()}


def test_service_identity_rejects_non_admin_runtime(monkeypatch):
    monkeypatch.setattr(runtime_env, "pwd", SimpleNamespace(getpwnam=lambda _n: SimpleNamespace(pw_uid=1001, pw_gid=1001)))
    monkeypatch.setattr(runtime_env, "grp", SimpleNamespace(getgrnam=lambda _n: SimpleNamespace(gr_gid=1001)))
    monkeypatch.setattr(runtime_env.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(runtime_env.os, "getegid", lambda: 0, raising=False)
    with pytest.raises(UnsafeRuntimeEnvError, match="admin:admin"):
        runtime_env._service_identity()


def test_accepts_owned_regular_0600_under_owned_0700_directory():
    validate_runtime_env_metadata(
        _metadata(stat.S_IFREG | 0o600),
        _metadata(stat.S_IFDIR | 0o700),
        expected_owner_uid=1001,
    )


@pytest.mark.parametrize(
    ("file_metadata", "parent_metadata", "expected"),
    [
        (_metadata(stat.S_IFLNK | 0o600), _metadata(stat.S_IFDIR | 0o700), "symlink"),
        (_metadata(stat.S_IFDIR | 0o600), _metadata(stat.S_IFDIR | 0o700), "regular"),
        (_metadata(stat.S_IFREG | 0o640), _metadata(stat.S_IFDIR | 0o700), "0600"),
        (_metadata(stat.S_IFREG | 0o600, uid=1002), _metadata(stat.S_IFDIR | 0o700), "file owner"),
        (_metadata(stat.S_IFREG | 0o600, gid=1002), _metadata(stat.S_IFDIR | 0o700), "file group"),
        (_metadata(stat.S_IFREG | 0o600), _metadata(stat.S_IFLNK | 0o700), "directory.*symlink"),
        (_metadata(stat.S_IFREG | 0o600), _metadata(stat.S_IFREG | 0o700), "parent.*directory"),
        (_metadata(stat.S_IFREG | 0o600), _metadata(stat.S_IFDIR | 0o750), "0700"),
        (_metadata(stat.S_IFREG | 0o600), _metadata(stat.S_IFDIR | 0o700, uid=1002), "directory owner"),
        (_metadata(stat.S_IFREG | 0o600), _metadata(stat.S_IFDIR | 0o700, gid=1002), "directory group"),
        (_metadata(stat.S_IFREG | 0o600, nlink=2), _metadata(stat.S_IFDIR | 0o700), "hard link"),
    ],
)
def test_rejects_every_file_and_parent_security_contract_violation(
    file_metadata,
    parent_metadata,
    expected,
):
    with pytest.raises(UnsafeRuntimeEnvError, match=expected):
        validate_runtime_env_metadata(
            file_metadata,
            parent_metadata,
            expected_owner_uid=1001,
        )


def test_relative_path_fails_before_filesystem_access():
    with pytest.raises(UnsafeRuntimeEnvError, match="absolute"):
        validate_runtime_env_file("relative.env", expected_owner_uid=1001)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX descriptor flags/modes")
def test_reads_from_same_validated_fd_even_if_path_is_replaced(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text("VALUE=original\n", encoding="utf-8")
    env_file.chmod(0o600)

    with open_validated_runtime_env(env_file, **_owner_kwargs()) as stream:
        old_file = secrets / "old.env"
        env_file.rename(old_file)
        env_file.write_text("VALUE=replacement\n", encoding="utf-8")
        env_file.chmod(0o600)
        assert stream.read() == "VALUE=original\n"


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX descriptor flags/modes")
def test_loader_rejects_symlinked_file_and_parent(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    real_file = secrets / "real.env"
    real_file.write_text("VALUE=secret\n", encoding="utf-8")
    real_file.chmod(0o600)
    linked_file = secrets / "linked.env"
    linked_file.symlink_to(real_file)

    with pytest.raises(UnsafeRuntimeEnvError):
        load_validated_runtime_env(linked_file, **_owner_kwargs())

    parent_link = tmp_path / "secrets-link"
    parent_link.symlink_to(secrets, target_is_directory=True)
    with pytest.raises(UnsafeRuntimeEnvError):
        load_validated_runtime_env(parent_link / "real.env", **_owner_kwargs())


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX hard-link semantics")
def test_loader_rejects_hardlinked_secret_file(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    secrets.chmod(0o700)
    env_file = secrets / "lead-radar.env"
    env_file.write_text("VALUE=secret\n", encoding="utf-8")
    env_file.chmod(0o600)
    os.link(env_file, tmp_path / "credential-copy")

    with pytest.raises(UnsafeRuntimeEnvError, match="hard link"):
        load_validated_runtime_env(env_file, **_owner_kwargs())
