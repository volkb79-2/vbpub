"""INS-18: `resolve_latest` picks the primary asset by type and never fails open on its sidecar."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cmru import resolve
from cmru.hosts.github import GitHubReleaseHost

DIGEST = "ab12" * 16


def _host(assets):
    host = GitHubReleaseHost("o", "r", "t")
    host._gh = SimpleNamespace(list_releases=lambda: [
        {"tag_name": "demo-v1.0.0", "assets": [
            {"name": n, "browser_download_url": f"https://dl/{n}"} for n in assets]}])
    return host


class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def test_asset_suffix_selects_the_primary_asset_not_api_order(monkeypatch):
    seen = []
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, **kw: seen.append(url) or _Resp(DIGEST.encode() + b"  x\n"))
    host = _host(["notes.txt", "demo-v1.0.0.tar.xz", "demo-v1.0.0.tar.xz.sha256"])
    out = host.resolve_latest("demo-v", asset_suffix=".tar.xz")
    assert out["asset"] == "demo-v1.0.0.tar.xz" and out["sha256"] == DIGEST
    assert seen == ["https://dl/demo-v1.0.0.tar.xz.sha256"]
    assert host.resolve_latest("demo-v", asset_suffix=".whl") is None


def test_sidecar_fetch_failure_is_an_error_not_a_missing_digest(monkeypatch):
    def boom(url, **kw):
        raise OSError("connection reset")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(RuntimeError, match="cannot read the checksum sidecar"):
        _host(["a.tar.xz", "a.tar.xz.sha256"]).resolve_latest("demo-v")


@pytest.mark.parametrize("body", [b"", b"not-a-digest  a.tar.xz\n", b"abc123  a.tar.xz\n"])
def test_malformed_sidecar_is_an_error(monkeypatch, body):
    monkeypatch.setattr("urllib.request.urlopen", lambda url, **kw: _Resp(body))
    with pytest.raises(RuntimeError, match="SHA-256 hex digest"):
        _host(["a.tar.xz", "a.tar.xz.sha256"]).resolve_latest("demo-v")


def test_resolve_passes_the_suffix_only_when_given():
    calls = []

    class Host:
        def resolve_latest(self, prefix, **kw):
            calls.append(kw)
            return {"tag": "t"}

    resolve.resolve(Host(), "demo-v", use_latest_json=False)
    resolve.resolve(Host(), "demo-v", use_latest_json=False, asset_suffix=".tar.xz")
    assert calls == [{}, {"asset_suffix": ".tar.xz"}]


def _cli(monkeypatch, resolver, installer):
    project = SimpleNamespace(prefix="demo-v", github_token="", installer=installer)
    loaded = (None, {"demo": project}, ["demo"], None, None, None, None, None,
              SimpleNamespace(owner="o", repo="r", token=None), None)
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: None)
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    monkeypatch.setattr(
        "cmru.config.load_forge_config", lambda _: SimpleNamespace(projects={"demo": project}),
    )
    monkeypatch.setattr("cmru.hosts.github.GitHubReleaseHost", lambda **kw: object())
    monkeypatch.setattr(resolve, "resolve", resolver)


def test_cli_reports_a_sidecar_failure_as_exit_1(monkeypatch, capsys):
    def resolver(host, prefix, **kw):
        raise RuntimeError("cannot read the checksum sidecar a.tar.xz.sha256 of t: boom")

    _cli(monkeypatch, resolver, installer=None)
    assert resolve.resolve_main(["demo"]) == 1
    assert "cannot resolve project 'demo'" in capsys.readouterr().err


def test_cli_passes_the_installer_asset_suffix(monkeypatch):
    got = {}

    def resolver(host, prefix, **kw):
        got.update(kw)
        return None

    _cli(monkeypatch, resolver, installer=SimpleNamespace(asset_suffix=".tar.xz"))
    assert resolve.resolve_main(["demo"]) == 1
    assert got["asset_suffix"] == ".tar.xz"


# ─── review round 1: the leftovers ───────────────────────────────────────────

def test_installer_release_without_a_sidecar_is_an_error_not_a_missing_digest():
    with pytest.raises(RuntimeError, match="no demo-v1.0.0.tar.xz.sha256 asset"):
        _host(["demo-v1.0.0.tar.xz"]).resolve_latest("demo-v", asset_suffix=".tar.xz")


def test_a_release_without_a_sidecar_still_resolves_when_no_suffix_is_asked(monkeypatch):
    out = _host(["demo-v1.0.0.tar.xz"]).resolve_latest("demo-v")
    assert out["asset"] == "demo-v1.0.0.tar.xz" and out["sha256"] is None


@pytest.mark.parametrize("assets,variant,expected", [
    (["z.tar.xz", "demo-v1.0.0.tar.xz", "a.tar.xz"], "", "demo-v1.0.0.tar.xz"),
    (["z.tar.xz", "a.tar.xz"], "", "a.tar.xz"),  # no tag match: first in sorted order
    (["demo-v1.0.0-py312.tar.xz", "demo-v1.0.0-py39.tar.xz"], "py39",
     "demo-v1.0.0-py39.tar.xz"),
    (["demo-v1.0.0-py39.tar.xz", "demo-v1.0.0-py312.tar.xz"], "py312",
     "demo-v1.0.0-py312.tar.xz"),
])
def test_primary_asset_is_chosen_deterministically(monkeypatch, assets, variant, expected):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, **kw: _Resp(DIGEST.encode() + b"  x\n"))
    names = assets + [f"{a}.sha256" for a in assets]
    for order in (names, list(reversed(names))):
        out = _host(order).resolve_latest("demo-v", asset_suffix=".tar.xz", variant=variant)
        assert out["asset"] == expected


def test_an_unknown_variant_resolves_to_nothing():
    assert _host(["demo-v1.0.0-py39.tar.xz", "demo-v1.0.0-py39.tar.xz.sha256"]
                 ).resolve_latest("demo-v", asset_suffix=".tar.xz", variant="py27") is None


@pytest.mark.parametrize("digest", ["zz" * 32, "ab" * 31, "AB" * 33, 12345])
def test_latest_json_pointer_with_a_malformed_sha256_is_an_error(monkeypatch, digest):
    monkeypatch.setattr(resolve, "resolve_via_latest_json", lambda url, prefix: {
        "version": "1.0.0", "tag": "demo-v1.0.0", "asset": "a.tar.xz",
        "sha256": digest, "url": "https://dl/a"})
    with pytest.raises(RuntimeError, match="not 64 hex"):
        resolve.resolve(object(), "demo-v", gh_releases_url="https://x")


def test_resolve_passes_the_variant_only_when_given():
    calls = []

    class Host:
        def resolve_latest(self, prefix, **kw):
            calls.append(kw)
            return {"tag": "t"}

    resolve.resolve(Host(), "demo-v", use_latest_json=False, asset_suffix=".tar.xz")
    resolve.resolve(Host(), "demo-v", use_latest_json=False, asset_suffix=".tar.xz",
                    variant="py39")
    assert calls == [{"asset_suffix": ".tar.xz"},
                     {"asset_suffix": ".tar.xz", "variant": "py39"}]


def test_latest_json_pointer_with_a_good_sha256_is_used(monkeypatch):
    pointer = {"version": "1.0.0", "tag": "demo-v1.0.0", "asset": "a.tar.xz",
               "sha256": DIGEST, "url": "https://dl/a"}
    monkeypatch.setattr(resolve, "resolve_via_latest_json", lambda url, prefix: pointer)
    assert resolve.resolve(object(), "demo-v", gh_releases_url="https://x",
                           asset_suffix=".tar.xz") == pointer


def test_latest_json_pointer_without_a_digest_falls_through_for_an_installer(monkeypatch):
    monkeypatch.setattr(resolve, "resolve_via_latest_json", lambda url, prefix: {
        "version": "1.0.0", "tag": "demo-v1.0.0", "asset": "a.tar.xz",
        "sha256": None, "url": "https://dl/a"})

    class Host:
        def resolve_latest(self, prefix, **kw):
            return {"tag": "from-host", **kw}

    out = resolve.resolve(Host(), "demo-v", gh_releases_url="https://x", asset_suffix=".tar.xz")
    assert out["tag"] == "from-host" and out["asset_suffix"] == ".tar.xz"
