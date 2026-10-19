from __future__ import annotations

import hashlib
import io
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
