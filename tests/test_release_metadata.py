import json
import os
from pathlib import Path

import pytest

from deployment.release_metadata import (
    CANONICAL_REPO,
    ReleaseMetadataError,
    verify_metadata,
    write_metadata,
)


SHA = "a" * 40


def _write_set(tmp_path: Path) -> tuple[Path, Path]:
    releases = tmp_path / "releases"
    release = releases / SHA
    release.mkdir(parents=True)
    write_metadata(
        marker=release / ".deployed_git_sha",
        manifest=release / ".release-manifest.json",
        previous_file=tmp_path / "previous",
        sha=SHA,
        previous_release="",
    )
    return releases, release


def test_release_metadata_is_canonical_strict_json(tmp_path):
    releases, release = _write_set(tmp_path)
    assert verify_metadata(release, sha=SHA, releases_dir=releases) == ""
    payload = (release / ".release-manifest.json").read_bytes()
    assert payload.endswith(b"\n")
    assert json.loads(payload)["repository"] == CANONICAL_REPO


def test_release_metadata_rejects_noncanonical_or_duplicate_json(tmp_path):
    releases, release = _write_set(tmp_path)
    manifest = release / ".release-manifest.json"
    manifest.write_text(
        '{"schema_version":1,"git_sha":"%s","git_sha":"%s",'
        '"repository":"%s","previous_release":""}\n' % (SHA, SHA, CANONICAL_REPO),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseMetadataError, match="duplicate"):
        verify_metadata(release, sha=SHA, releases_dir=releases)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX hard-link semantics")
def test_release_metadata_rejects_hardlinked_marker(tmp_path):
    releases, release = _write_set(tmp_path)
    os.link(release / ".deployed_git_sha", tmp_path / "marker-copy")
    with pytest.raises(ReleaseMetadataError, match="single-link"):
        verify_metadata(release, sha=SHA, releases_dir=releases)


def test_metadata_writer_rejects_non_sha_previous_path(tmp_path):
    with pytest.raises(ReleaseMetadataError, match="exact-SHA"):
        write_metadata(
            marker=tmp_path / "marker",
            manifest=tmp_path / "manifest",
            previous_file=tmp_path / "previous",
            sha=SHA,
            previous_release=str(tmp_path / "not-a-sha"),
        )
