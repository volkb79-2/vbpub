"""KI-89 / PWMCP-07: the name the producer publishes IS the name get.py downloads.

``publish_versioned`` uploads the primary asset under the file name the project's build
produced, while the rendered ``get.py`` computes ``<tag><suffix>`` on its own. pwmcp built
``pwmcp-<version>.tar.xz`` (no ``v``) and every release's own ``get.py`` answered HTTP 404. The tests below compare the two sides through
the REAL producer (``publish_versioned``) and the REAL rendered download path, never a
fake that restates the template's own formula.

Stdlib + tmp files only: no network, no git.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from cmru import release
from cmru.getpy import render_get_py

OWNER, REPO = "octocat", "demo"
TAG = "demo-v1.2.3-r4"
VERSION = "1.2.3-r4"
SUFFIX = ".tar.xz"


class _FakeHost:
    """A GitHub stand-in that stores uploaded assets per tag, exactly as published."""

    def __init__(self) -> None:
        self.assets: dict[str, dict[str, Path]] = {}

    def asset_download_url(self, tag: str, asset_name: str) -> str:
        return f"https://example.test/{tag}/{asset_name}"

    def publish(self, tag, title, notes, assets, *, recreate=False, target_commitish=None, **_):
        store = self.assets.setdefault(tag, {})
        for asset in assets:
            path = Path(asset)
            kept = path.parent / ".published" / path.name
            kept.parent.mkdir(exist_ok=True)
            shutil.copy2(path, kept)
            store[path.name] = kept
        return {"id": len(self.assets)}


def _render() -> dict:
    src = render_get_py(
        project_name="demo", repo_owner=OWNER, repo_name=REPO, tag_prefix="demo-v",
        install_dir_system="/srv/demo", install_dir_user="demo",
        entrypoint="", required_commands=[], preserve_paths=[],
    )
    ns: dict = {}
    exec(compile(src, "<rendered-get.py>", "exec"), ns)
    return ns


def _serve(ns: dict, host: _FakeHost) -> None:
    """Route get.py's HTTP layer to the fake host; a missing asset is a real 404."""
    api = ns["RELEASES_API"]

    def gh_request(url, *, token=None, accept="", stream_to=None, timeout=30, max_bytes=None):
        for tag, store in host.assets.items():
            if url == f"{api}/tags/{tag}":
                listing = [{"name": n, "id": i} for i, n in enumerate(store)]
                return 200, json.dumps({"assets": listing}).encode()
            for index, (name, path) in enumerate(store.items()):
                if url in (
                    f"https://github.com/{OWNER}/{REPO}/releases/download/{tag}/{name}",
                    f"https://api.github.com/repos/{OWNER}/{REPO}/releases/assets/{index}",
                ):
                    shutil.copyfile(path, stream_to)
                    return 200, b""
        return 404, b""

    ns["_gh_request"] = gh_request


def _built(tmp_path: Path, name: str, payload: bytes = b"bundle-bytes") -> Path:
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    path = dist / name
    path.write_bytes(payload)
    return path


def _put_raw(host: _FakeHost, tmp_path: Path, tag: str, name: str, payload: bytes,
             sidecar_digest: str | None = None) -> None:
    """Publish a pre-fix style release: asset + sidecar under ``name`` exactly."""
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    (raw / name).write_bytes(payload)
    digest = sidecar_digest or hashlib.sha256(payload).hexdigest()
    (raw / f"{name}.sha256").write_text(f"{digest}  {name}\n")
    host.publish(tag, tag, "", [raw / name, raw / f"{name}.sha256"])


class TestOneNameFunction:
    def test_bundle_asset_name_is_tag_based(self):
        assert release.bundle_asset_name("demo", VERSION, SUFFIX) == "demo-v1.2.3-r4.tar.xz"
        assert release.bundle_asset_name("demo", "1.0.0+x", SUFFIX) == "demo-v1.0.0-x.tar.xz"

    def test_variant_name_is_the_same_function(self):
        assert release.variant_asset_name("demo", "1.2.3", "py311", SUFFIX) == \
            release.bundle_asset_name("demo", "1.2.3", SUFFIX, variant="py311")

    @pytest.mark.parametrize("version", ["1.2.3", "1.64.0-r2", "2.0.0+local.1"])
    @pytest.mark.parametrize("variant", [None, "py311"])
    def test_rendered_get_py_computes_the_functions_name(self, version, variant):
        ns = _render()
        tag = release.version_to_tag("demo", version)
        assert ns["expected_asset_name"](tag, variant) == \
            release.bundle_asset_name("demo", version, SUFFIX, variant=variant)


class TestRealProducerRealConsumer:
    @pytest.mark.parametrize("token", [None, "tok"])
    def test_published_bundle_is_downloadable_by_its_own_get_py(self, tmp_path, token):
        # the producer names its build output by the contract (bundle_asset_name) and the
        # REAL keystone publishes it untouched; get.py must then download it by name
        host = _FakeHost()
        built = _built(tmp_path, release.bundle_asset_name("demo", VERSION, SUFFIX))
        release.publish_versioned(
            host, prefix="demo", version=VERSION, asset_path=built, latest_pointer=False,
        )
        assert set(host.assets[TAG]) == {f"demo-v{VERSION}.tar.xz", f"demo-v{VERSION}.tar.xz.sha256"}

        ns = _render()
        _serve(ns, host)
        wd = tmp_path / "wd"
        wd.mkdir()
        got = ns["download_and_verify"](TAG, wd, token)
        assert got.name == f"demo-v{VERSION}.tar.xz"
        assert got.read_bytes() == b"bundle-bytes"

    def test_the_keystone_never_renames_the_primary_asset(self, tmp_path):
        # wheels (and any non-bundle artifact) keep their file name: pip reads it
        host = _FakeHost()
        release.publish_versioned(
            host, prefix="demo", version="1.2.3", latest_pointer=False,
            asset_path=_built(tmp_path, "demo-1.2.3-py3-none-any.whl"),
        )
        assert "demo-1.2.3-py3-none-any.whl" in host.assets["demo-v1.2.3"]


class TestLegacyNameFallback:
    """Releases published before the fix carry ``<prefix>-<version>.tar.xz`` (pwmcp r1..r2)."""

    @pytest.mark.parametrize("token", [None, "tok"])
    def test_single_legacy_asset_is_taken_and_logged(self, tmp_path, capsys, token):
        host = _FakeHost()
        _put_raw(host, tmp_path, TAG, f"demo-{VERSION}.tar.xz", b"legacy")
        ns = _render()
        _serve(ns, host)
        wd = tmp_path / "wd"
        wd.mkdir()
        got = ns["download_and_verify"](TAG, wd, token)
        assert got.name == f"demo-{VERSION}.tar.xz" and got.read_bytes() == b"legacy"
        out = capsys.readouterr()
        assert "legacy" in (out.out + out.err).lower()
        assert f"demo-{VERSION}.tar.xz" in (out.out + out.err)

    def test_zero_candidates_fails_loudly(self, tmp_path):
        host = _FakeHost()
        _put_raw(host, tmp_path, TAG, "get.py", b"x")
        ns = _render()
        _serve(ns, host)
        wd = tmp_path / "wd"
        wd.mkdir()
        with pytest.raises(SystemExit) as stopped:
            ns["download_and_verify"](TAG, wd, None)
        assert stopped.value.code != 0

    def test_two_candidates_fail_loudly_and_never_guess(self, tmp_path, capsys):
        host = _FakeHost()
        _put_raw(host, tmp_path, TAG, "a-1.tar.xz", b"a")
        _put_raw(host, tmp_path, TAG, "b-1.tar.xz", b"b")
        ns = _render()
        _serve(ns, host)
        wd = tmp_path / "wd"
        wd.mkdir()
        with pytest.raises(SystemExit):
            ns["download_and_verify"](TAG, wd, None)
        err = capsys.readouterr().err
        assert "a-1.tar.xz" in err and "b-1.tar.xz" in err

    def test_legacy_asset_with_a_bad_sidecar_is_refused(self, tmp_path):
        host = _FakeHost()
        _put_raw(host, tmp_path, TAG, f"demo-{VERSION}.tar.xz", b"legacy",
                 sidecar_digest="0" * 64)
        ns = _render()
        _serve(ns, host)
        wd = tmp_path / "wd"
        wd.mkdir()
        with pytest.raises(SystemExit):
            ns["download_and_verify"](TAG, wd, None)

    def test_a_variant_request_never_uses_the_fallback(self, tmp_path):
        host = _FakeHost()
        _put_raw(host, tmp_path, TAG, f"demo-{VERSION}.tar.xz", b"legacy")
        ns = _render()
        _serve(ns, host)
        wd = tmp_path / "wd"
        wd.mkdir()
        with pytest.raises(SystemExit):
            ns["download_and_verify"](TAG, wd, None, "py311")

    def test_a_release_with_only_a_variant_asset_is_refused_not_guessed(self, tmp_path):
        # <tag>-<variant><suffix> belongs to the variants namespace: it is not a legacy name
        host = _FakeHost()
        _put_raw(host, tmp_path, TAG, f"{TAG}-py311.tar.xz", b"variant")
        ns = _render()
        _serve(ns, host)
        wd = tmp_path / "wd"
        wd.mkdir()
        with pytest.raises(SystemExit):
            ns["download_and_verify"](TAG, wd, None)
        assert not (wd / f"{TAG}-py311.tar.xz").exists()

    def test_a_variant_asset_beside_one_legacy_asset_leaves_the_legacy_one(self, tmp_path):
        host = _FakeHost()
        _put_raw(host, tmp_path, TAG, f"{TAG}-py311.tar.xz", b"variant")
        _put_raw(host, tmp_path, TAG, f"demo-{VERSION}.tar.xz", b"legacy")
        ns = _render()
        _serve(ns, host)
        wd = tmp_path / "wd"
        wd.mkdir()
        assert ns["download_and_verify"](TAG, wd, None).name == f"demo-{VERSION}.tar.xz"
