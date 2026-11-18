from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import pytest

import upstream


INDEX = b"""
<html><body>
<a href="TrueNAS-SCALE-Fangtooth/25.04.2.6/TrueNAS-SCALE-25.04.2.6.iso">ISO</a>
<a href="TrueNAS-SCALE-Fangtooth/25.04.2.6/TrueNAS-SCALE-25.04.2.6.iso.sha256">SHA</a>
<a href="TrueNAS-27-RC/27.0.0-RC.1/TrueNAS-27.0.0-RC.1.iso">ISO</a>
<a href="TrueNAS-27-RC/27.0.0-RC.1/TrueNAS-27.0.0-RC.1.iso.sha256">SHA</a>
<a href="TrueNAS-28-Nightlies/TrueNAS-28.0.0-MASTER+20261014-230204.iso">ISO</a>
<a href="TrueNAS-28-Nightlies/TrueNAS-28.0.0-MASTER+20261014-230204.iso.sha256">SHA</a>
<a href="TrueNAS-SCALE-Dragonfish/24.04.2/TrueNAS-SCALE-24.04.2.iso">old</a>
<a href="https://example.invalid/TrueNAS-29.0.0.iso">untrusted</a>
</body></html>
"""

ISO_INDEX = b"""
<html><body>
<a href="TrueNAS-28-Nightlies/TrueNAS-28.0.0-MASTER+20261014-230204.iso">same</a>
<a href="TrueNAS-28-Nightlies/TrueNAS-28.0.0-MASTER+20261014-230204.iso.sha256">same sha</a>
<a href="TrueNAS-28-Nightlies/TrueNAS-28.0.0-MASTER+20261015-175234.iso">new</a>
<a href="TrueNAS-28-Nightlies/TrueNAS-28.0.0-MASTER+20261015-175234.iso.sha256">new sha</a>
</body></html>
"""


def test_catalog_enumerates_versioned_stable_rc_and_nightly_images():
    assets = upstream._parse_index(INDEX, "https://download.sys.truenas.net/")

    assert {(asset.family, asset.version) for asset in assets} == {
        ("TrueNAS-SCALE-Fangtooth", "25.04.2.6"),
        ("TrueNAS-27-RC", "27.0.0-RC.1"),
        ("TrueNAS-28-Nightlies", "28.0.0-MASTER+20261014-230204"),
    }
    nightly = next(asset for asset in assets if asset.family == "TrueNAS-28-Nightlies")
    assert nightly.iso_url.endswith("20261014-230204.iso")
    assert nightly.checksum_url.endswith("20261014-230204.iso.sha256")


def test_catalog_requires_checksum_sidecar_and_refuses_untrusted_links():
    html = b'<a href="TrueNAS-29-Nightly/TrueNAS-29.0.0-MASTER+20270101-000000.iso">ISO</a>'
    assert upstream._parse_index(html, "https://download.sys.truenas.net/") == []


def test_catalog_directory_subversion_must_match_filename_for_four_part_version():
    html = b"""
    <a href="TrueNAS-SCALE/25.04.2.6/TrueNAS-SCALE-25.04.2.7.iso">ISO</a>
    <a href="TrueNAS-SCALE/25.04.2.6/TrueNAS-SCALE-25.04.2.7.iso.sha256">SHA</a>
    """
    assert upstream._parse_index(html, "https://download.sys.truenas.net/") == []


def test_discover_releases_reads_official_catalog_url_and_fails_on_network_error():
    seen = []

    def fetch(url, maximum):
        seen.append((url, maximum))
        return INDEX

    releases = upstream.discover_releases(catalog_urls=("https://download.sys.truenas.net/",), fetch=fetch)
    assert len(releases) == 3
    assert seen == [("https://download.sys.truenas.net/", upstream.CATALOG_MAX_BYTES)]

    def fail(_url, _maximum):
        raise upstream.UpstreamError("offline")

    with pytest.raises(upstream.UpstreamError, match="official TrueNAS ISO catalog"):
        upstream.discover_releases(catalog_urls=("https://download.sys.truenas.net/",), fetch=fail)

    with pytest.raises(upstream.UpstreamError, match="matching SHA-256 sidecar links"):
        upstream.discover_releases(
            catalog_urls=("https://download.sys.truenas.net/",),
            fetch=lambda *_args: b"<html><body>catalog changed</body></html>",
        )


def test_default_catalog_merges_both_official_sites_and_refuses_partial_results():
    seen = []

    def fetch(url, maximum):
        seen.append((url, maximum))
        return ISO_INDEX if url == "https://iso.sys.truenas.net/" else INDEX

    releases = upstream.discover_releases(fetch=fetch)
    assert len(releases) == 4
    assert seen == [
        ("https://download.sys.truenas.net/", upstream.CATALOG_MAX_BYTES),
        ("https://iso.sys.truenas.net/", upstream.CATALOG_MAX_BYTES),
    ]
    same_asset = next(asset for asset in releases if asset.version.endswith("20261014-230204"))
    iso_only_asset = next(asset for asset in releases if asset.version.endswith("20261015-175234"))
    assert same_asset.iso_url.startswith("https://download.sys.truenas.net/")
    assert iso_only_asset.iso_url.startswith("https://iso.sys.truenas.net/")

    def second_site_offline(url, maximum):
        if url == "https://iso.sys.truenas.net/":
            raise upstream.UpstreamError("offline")
        return INDEX

    with pytest.raises(upstream.UpstreamError, match="incomplete release list"):
        upstream.discover_releases(fetch=second_site_offline)


def test_resolve_release_requires_family_when_a_version_is_ambiguous():
    assets = upstream._parse_index(
        b"""
        <a href="TrueNAS-A/25.01.0/TrueNAS-SCALE-25.01.0.iso">ISO</a>
        <a href="TrueNAS-A/25.01.0/TrueNAS-SCALE-25.01.0.iso.sha256">SHA</a>
        <a href="TrueNAS-B/25.01.0/TrueNAS-SCALE-25.01.0.iso">ISO</a>
        <a href="TrueNAS-B/25.01.0/TrueNAS-SCALE-25.01.0.iso.sha256">SHA</a>
        """,
        "https://download.sys.truenas.net/",
    )

    with pytest.raises(upstream.UpstreamError, match="ambiguous"):
        upstream.resolve_release(assets, "25.01.0")
    assert upstream.resolve_release(assets, "25.01.0", "TrueNAS-B").family == "TrueNAS-B"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"a" * 64, "a" * 64),
        (b"b" * 64 + b"  TrueNAS.iso\n", "b" * 64),
        (b"c" * 64 + b" *TrueNAS.iso\n", "c" * 64),
    ],
)
def test_sha256_sidecar_formats(payload, expected):
    assert upstream.parse_checksum(payload, filename="TrueNAS.iso") == expected


@pytest.mark.parametrize(
    "payload",
    [
        b"too short",
        b"a" * 64 + b"  another.iso",
        b"a" * 64 + b"\n" + b"b" * 64,
        b"a" * 63 + b"z",
    ],
)
def test_sha256_sidecar_rejects_invalid_or_wrong_filename(payload):
    with pytest.raises(upstream.UpstreamError):
        upstream.parse_checksum(payload, filename="TrueNAS.iso")


class _Response(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def geturl(self):
        return "https://download.sys.truenas.net/test.iso"


@pytest.fixture
def asset():
    return upstream.UpstreamIso(
        family="TrueNAS-28-Nightlies",
        version="28.0.0-MASTER+20261014-230204",
        filename="TrueNAS-28.0.0-MASTER+20261014-230204.iso",
        iso_url="https://download.sys.truenas.net/test.iso",
        checksum_url="https://download.sys.truenas.net/test.iso.sha256",
    )


def test_download_publishes_only_a_digest_verified_file(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(upstream, "_request", lambda *_args, **_kwargs: _Response(payload))
    destination = tmp_path / asset.filename

    actual = upstream.download_verified_iso(asset, destination, expected_sha256=expected)

    assert actual == expected
    assert destination.read_bytes() == payload
    assert not list(tmp_path.glob("*.download-*"))


def test_download_sha_mismatch_leaves_no_partial_iso(tmp_path, monkeypatch, asset):
    monkeypatch.setattr(upstream, "_request", lambda *_args, **_kwargs: _Response(b"wrong bytes"))
    destination = tmp_path / asset.filename

    with pytest.raises(upstream.UpstreamError, match="SHA-256 mismatch"):
        upstream.download_verified_iso(asset, destination, expected_sha256="0" * 64)

    assert not destination.exists()
    assert not list(tmp_path.glob(".*.download-*"))


def test_ensure_in_iso_dir_fetches_once_then_reuses_the_verified_copy(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    calls = []
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: calls.append(1) or _Response(payload))

    first, digest = upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=tmp_path)
    second, _ = upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=tmp_path)

    assert first == second == tmp_path / asset.filename
    assert digest == expected and len(calls) == 1
    assert (tmp_path / (asset.filename + ".sha256")).read_text().startswith(expected)


def test_ensure_in_iso_dir_concurrent_runs_fetch_once_under_the_lock(tmp_path, monkeypatch, asset):
    import threading
    import time

    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    calls = []

    def slow_request(*args, **kwargs):
        calls.append(1)
        time.sleep(0.3)
        return _Response(payload)

    monkeypatch.setattr(upstream, "_request", slow_request)
    results, errors = [], []

    def run():
        try:
            results.append(upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=tmp_path))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(calls) == 1 and len(results) == 2
    assert (tmp_path / f".{asset.filename}.lock").exists()


def test_ensure_in_iso_dir_refuses_a_file_that_differs_from_the_published_digest(tmp_path, asset):
    (tmp_path / asset.filename).write_bytes(b"tampered")
    with pytest.raises(upstream.UpstreamError, match="differs from the published"):
        upstream.ensure_in_iso_dir(asset, expected_sha256="0" * 64, iso_folder=tmp_path)
    assert (tmp_path / asset.filename).read_bytes() == b"tampered"


def test_the_iso_cache_and_its_lock_are_in_the_resolved_iso_folder(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(upstream.repo_paths_access, "iso_dir", lambda: tmp_path / "iso")
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: _Response(payload))
    path, _ = upstream.ensure_in_iso_dir(asset, expected_sha256=expected)
    assert upstream.iso_dir() == tmp_path / "iso"
    assert path == tmp_path / "iso" / asset.filename
    assert (tmp_path / "iso" / f".{asset.filename}.lock").exists()
    assert (tmp_path / "iso" / f"{asset.filename}.sha256").is_file()


def _no_download(monkeypatch):
    calls = []
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: calls.append(1) or _Response(b"must not be fetched"))
    return calls


def test_an_iso_that_is_still_flat_in_downloads_is_reused_not_fetched_again(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    flat = tmp_path / "downloads"
    flat.mkdir()
    (flat / asset.filename).write_bytes(payload)
    monkeypatch.setattr(upstream.repo_paths_access, "iso_dir", lambda: flat / "iso")
    monkeypatch.setattr(upstream.repo_paths_access, "downloads_dir", lambda: flat)
    calls = _no_download(monkeypatch)
    messages = []

    path, digest = upstream.ensure_in_iso_dir(asset, expected_sha256=expected, progress=messages.append)

    assert path == flat / asset.filename and digest == expected and calls == []
    assert messages == [f"Reusing verified {flat / asset.filename}"]
    # The lock stays in the iso folder; nothing else is written there.
    assert sorted(p.name for p in (flat / "iso").iterdir()) == [f".{asset.filename}.lock"]


def test_the_iso_folder_wins_over_the_flat_downloads_copy(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    (tmp_path / "iso").mkdir()
    (tmp_path / "iso" / asset.filename).write_bytes(payload)
    (tmp_path / asset.filename).write_bytes(b"a flat copy that is never even read")
    monkeypatch.setattr(upstream.repo_paths_access, "iso_dir", lambda: tmp_path / "iso")
    monkeypatch.setattr(upstream.repo_paths_access, "downloads_dir", lambda: tmp_path)
    assert _no_download(monkeypatch) == []

    path, _ = upstream.ensure_in_iso_dir(asset, expected_sha256=expected)

    assert path == tmp_path / "iso" / asset.filename


def test_a_flat_copy_with_a_wrong_digest_is_refused_and_kept(tmp_path, monkeypatch, asset):
    flat = tmp_path / "downloads"
    flat.mkdir()
    (flat / asset.filename).write_bytes(b"tampered")
    monkeypatch.setattr(upstream.repo_paths_access, "iso_dir", lambda: flat / "iso")
    monkeypatch.setattr(upstream.repo_paths_access, "downloads_dir", lambda: flat)
    calls = _no_download(monkeypatch)

    with pytest.raises(upstream.UpstreamError, match="differs from the published"):
        upstream.ensure_in_iso_dir(asset, expected_sha256="0" * 64)

    assert calls == [] and (flat / asset.filename).read_bytes() == b"tampered"
    assert not (flat / "iso" / asset.filename).exists()


def test_no_flat_copy_means_one_fetch_into_the_iso_folder(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(upstream.repo_paths_access, "iso_dir", lambda: tmp_path / "iso")
    monkeypatch.setattr(upstream.repo_paths_access, "downloads_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: calls.append(1) or _Response(payload))

    path, _ = upstream.ensure_in_iso_dir(asset, expected_sha256=expected)

    assert path == tmp_path / "iso" / asset.filename and len(calls) == 1


def test_an_unresolvable_flat_folder_is_not_searched(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()

    def refuse():
        raise upstream.repo_paths_access.RepoPathsError("no downloads entry")

    monkeypatch.setattr(upstream.repo_paths_access, "iso_dir", lambda: tmp_path / "iso")
    monkeypatch.setattr(upstream.repo_paths_access, "downloads_dir", refuse)
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: _Response(payload))
    assert upstream.flat_downloads_dir() is None

    path, _ = upstream.ensure_in_iso_dir(asset, expected_sha256=expected)

    assert path == tmp_path / "iso" / asset.filename


def test_an_explicit_iso_folder_does_not_search_the_resolved_flat_folder(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / asset.filename).write_bytes(payload)
    monkeypatch.setattr(upstream.repo_paths_access, "downloads_dir", lambda: flat)
    calls = []
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: calls.append(1) or _Response(payload))

    path, _ = upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=tmp_path / "iso")
    assert path == tmp_path / "iso" / asset.filename and len(calls) == 1
    # An explicit flat folder is honoured.
    other = tmp_path / "other"
    other.mkdir()
    (other / asset.filename).write_bytes(payload)
    again, _ = upstream.ensure_in_iso_dir(
        asset, expected_sha256=expected, iso_folder=tmp_path / "iso2", flat_folder=other
    )
    assert again == other / asset.filename and len(calls) == 1


def _pretend_sudo(monkeypatch):
    chowned = []
    monkeypatch.setattr(upstream.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", "1234")
    monkeypatch.setenv("SUDO_GID", "5678")
    monkeypatch.setattr(upstream.os, "chown", lambda path, uid, gid: chowned.append((Path(path), uid, gid)))
    return chowned


def test_under_sudo_a_created_iso_folder_and_its_parents_go_to_the_invoking_user(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    iso = tmp_path / "cow" / "downloads" / "iso"
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: _Response(payload))
    chowned = _pretend_sudo(monkeypatch)

    upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=iso)

    owned = {path for path, uid, gid in chowned if (uid, gid) == (1234, 5678)}
    assert owned == {
        tmp_path / "cow", tmp_path / "cow" / "downloads", iso, iso / asset.filename, iso / f"{asset.filename}.sha256",
        iso / f".{asset.filename}.lock",
    }


def test_under_sudo_an_existing_iso_folder_is_left_alone(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    iso = tmp_path / "iso"
    iso.mkdir()
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: _Response(payload))
    chowned = _pretend_sudo(monkeypatch)

    upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=iso)

    # Only the fetched ISO, its sidecar and the lock file are handed over; the folder (not created by this run) is never
    # touched.
    assert {path for path, *_ in chowned} == {
        iso / asset.filename, iso / f"{asset.filename}.sha256", iso / f".{asset.filename}.lock"
    }


def test_under_sudo_the_lock_file_goes_to_the_invoking_user(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    iso = tmp_path / "iso"
    iso.mkdir()
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: _Response(payload))
    chowned = _pretend_sudo(monkeypatch)

    upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=iso)

    # A root-owned lock would make a later non-sudo run fail with "could not lock", so it is handed over too (uid, gid
    # in that order).
    assert (iso / f".{asset.filename}.lock", 1234, 5678) in chowned


@pytest.mark.parametrize(
    "euid, sudo_uid, sudo_gid",
    [(1000, "1234", "5678"), (0, None, None), (0, "1234", None), (0, None, "5678"), (0, "abc", "5678"), (0, "1234", "x1")],
)
def test_the_lock_file_is_left_alone_without_root_or_with_malformed_sudo_variables(
    tmp_path, monkeypatch, asset, euid, sudo_uid, sudo_gid
):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    iso = tmp_path / "iso"
    iso.mkdir()
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: _Response(payload))
    monkeypatch.setattr(upstream.os, "geteuid", lambda: euid)
    for name, value in (("SUDO_UID", sudo_uid), ("SUDO_GID", sudo_gid)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(upstream.os, "chown", lambda *a: pytest.fail("chown without a valid sudo owner"))

    path, _ = upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=iso)

    assert path == iso / asset.filename and (iso / f".{asset.filename}.lock").exists()


def test_without_sudo_nothing_is_chowned_and_an_existing_folder_is_not_recreated(tmp_path, monkeypatch, asset):
    payload = b"vanilla installer ISO fixture"
    expected = hashlib.sha256(payload).hexdigest()
    iso = tmp_path / "iso"
    iso.mkdir()
    monkeypatch.setattr(upstream, "_request", lambda *a, **k: _Response(payload))
    monkeypatch.setattr(upstream.os, "chown", lambda *a: pytest.fail("chown without sudo"))
    made = []
    real_mkdir = Path.mkdir
    monkeypatch.setattr(Path, "mkdir", lambda self, *a, **k: made.append(self) or real_mkdir(self, *a, **k))

    upstream.ensure_in_iso_dir(asset, expected_sha256=expected, iso_folder=iso)

    assert made == []


def test_the_truenas_root_is_put_on_sys_path_only_if_absent():
    import importlib.util
    import sys

    root = str(Path(upstream.__file__).resolve().parents[1])

    def load():
        spec = importlib.util.spec_from_file_location("upstream_guard_probe", upstream.__file__)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # the module's dataclasses resolve their annotations through sys.modules
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(spec.name, None)

    saved = list(sys.path)
    try:
        sys.path[:] = [entry for entry in sys.path if entry != root]
        load()
        assert sys.path.count(root) == 1 and sys.path[0] == root
        load()  # present now: a second load must not add another entry
        assert sys.path.count(root) == 1
    finally:
        sys.path[:] = saved


def test_the_iso_folder_is_the_repo_path_config_iso_entry_in_plain_mode(monkeypatch):
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)
    repo = Path(upstream.__file__).resolve().parents[3]
    assert upstream.iso_dir() == repo / "downloads" / "iso"


def test_a_missing_repo_path_config_refuses_naming_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(upstream.repo_paths_access, "REPO_ROOT", tmp_path)
    with pytest.raises(upstream.UpstreamError, match=re.escape(f"{upstream.repo_paths_access.config_name()} not found")):
        upstream.iso_dir()
