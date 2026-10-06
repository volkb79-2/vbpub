"""W1-INSTALLER: the generic get.py installer (decision O4, part A).

Oracles follow GETPY-REDESIGN R2/R3/R5/R7/R8/R9/R10/R11. Everything runs against temp
roots with local fakes: no network, no real root. Real `minisign`, `venv` and `pip` are
used where the oracle is about them (offline, from a local wheelhouse only).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from cmru.getpy import RenderError, getpy_main, render_get_py

from tests.installer_fakes import (
    args, as_root, install, make_bundle, make_wheel, minisign_keypair, render_ns, rollback,
    root_of, sha, snapshot, update, use_bundles,
)

V1, V2 = "demo-v1.0.0", "demo-v2.0.0"
HAVE_MINISIGN = shutil.which("minisign") is not None
needs_minisign = pytest.mark.skipif(not HAVE_MINISIGN, reason="minisign binary not installed")


def _exits(code):
    return pytest.raises(SystemExit, match=f"^{code}$")


def _plain_setup(tmp_path, **kw):
    """A no-wheel project with v1 and v2 bundles (VERSION files)."""
    ns = render_ns(tmp_path, **kw)
    bundles = tmp_path / "bundles"
    for tag in (V1, V2):
        make_bundle(bundles, tag, files={"VERSION": tag.encode()})
    use_bundles(ns, bundles)
    return ns, bundles


def _state(ns) -> dict:
    return json.loads((root_of(ns) / "state.json").read_text())


# ─── R5 fail-closed parsing ───────────────────────────────────────────────────

class TestFailClosed:
    """Each case: EXIT_FAIL and <root> byte-identical afterwards (INS-02)."""

    def _broken(self, tmp_path, name, **bundle_kw):
        ns, bundles = _plain_setup(tmp_path)
        install(ns, version=V1)
        before = snapshot(root_of(ns))
        make_bundle(bundles, V2, **bundle_kw)
        with _exits(1):
            update(ns, version=V2)
        assert snapshot(root_of(ns)) == before, name

    @pytest.mark.parametrize("name,kw", [
        ("absent manifest", dict(files={"VERSION": b"2"}, manifest=None)),
        ("unparseable manifest", dict(files={"VERSION": b"2"}, manifest=b"{not json")),
        ("manifest not an object", dict(files={"VERSION": b"2"}, manifest=b"[1]")),
        ("not utf-8", dict(files={"VERSION": b"2"}, manifest=b"\xff\xfe")),
        ("unknown schema", dict(files={"VERSION": b"2"}, manifest_extra={"schema_version": 2})),
        ("boolean schema", dict(files={"VERSION": b"2"}, manifest_extra={"schema_version": True})),
        ("tag mismatch", dict(files={"VERSION": b"2"}, manifest_extra={"tag": V1})),
        ("version mismatch", dict(files={"VERSION": b"2"}, manifest_extra={"version": "9.9.9"})),
        ("files entry missing from bundle",
         dict(files={"VERSION": b"2"}, manifest_extra={"files": {"NOPE": {"sha256": "0" * 64}}})),
        ("files entry without sha256",
         dict(files={"VERSION": b"2"}, manifest_extra={"files": {"VERSION": {}}})),
        ("files entry size mismatch",
         dict(files={"VERSION": b"2"},
              manifest_extra={"files": {"VERSION": {"sha256": sha(b"2"), "size": 99}}})),
        ("files entry hash mismatch",
         dict(files={"VERSION": b"2"},
              manifest_extra={"files": {"VERSION": {"sha256": "0" * 64}}})),
        ("files not an object", dict(files={"VERSION": b"2"}, manifest_extra={"files": []})),
        ("members outside a single top-level dir", dict(files={"VERSION": b"2"}, top="")),
    ])
    def test_refused_and_root_unchanged(self, tmp_path, name, kw):
        self._broken(tmp_path, name, **kw)

    def test_bundle_with_unsafe_member_refused(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        install(ns, version=V1)
        before = snapshot(root_of(ns))
        make_bundle(bundles, V2, files={"VERSION": b"2"},
                    extra_members=[("../other/manifest.json", b"{}")])
        with _exits(1):
            update(ns, version=V2)
        assert snapshot(root_of(ns)) == before

    def test_manifest_inside_bundle_cannot_differ_from_installed_copy(self, tmp_path):
        """INS-01: the only manifest ever used is the verified one (never re-read)."""
        ns, bundles = _plain_setup(tmp_path)
        _asset, manifest_bytes = make_bundle(bundles, V1, files={"VERSION": b"1"})
        install(ns, version=V1)
        release = root_of(ns) / "releases" / ns["_release_name"](V1, sha(manifest_bytes), None)
        assert (release / "manifest.json").read_bytes() == manifest_bytes
        assert not (release / "tree" / "manifest.json").exists()

    def test_wheel_without_manifest_entry_refused(self, tmp_path):
        """The old test pinned warn-and-skip; a missing entry is now fatal."""
        ns = render_ns(tmp_path, wheel_specs=[("vendor/demotool-*.whl", "demotool")])
        wheel = make_wheel(tmp_path / "w", "demotool", "1.0.0")
        with _exits(1):
            ns["_verify_wheel_sha256"](wheel, {"schema_version": 1}, "demotool")

    @pytest.mark.parametrize("entry", [
        {}, {"sha256": ""}, {"sha256": "zz"}, {"sha256": "0" * 64},
        {"wheel": "other.whl", "sha256": "0" * 64},
    ])
    def test_wheel_entry_defects_refused(self, tmp_path, entry):
        ns = render_ns(tmp_path)
        wheel = make_wheel(tmp_path / "w", "demotool", "1.0.0")
        with _exits(1):
            ns["_verify_wheel_sha256"](wheel, {"demotool": entry}, "demotool")

    def test_wheel_size_mismatch_refused(self, tmp_path):
        ns = render_ns(tmp_path)
        wheel = make_wheel(tmp_path / "w", "demotool", "1.0.0")
        entry = {"sha256": sha(wheel.read_bytes()), "size": 1}
        with _exits(1):
            ns["_verify_wheel_sha256"](wheel, {"demotool": entry}, "demotool")

    def test_wheel_good_entry_returns_digest(self, tmp_path):
        ns = render_ns(tmp_path)
        wheel = make_wheel(tmp_path / "w", "demotool", "1.0.0")
        digest = sha(wheel.read_bytes())
        got = ns["_verify_wheel_sha256"](
            wheel, {"demotool": {"sha256": digest, "wheel": wheel.name}}, "demotool")
        assert got == digest

    def test_adapter_must_be_covered_by_manifest(self, tmp_path):
        ns = render_ns(tmp_path, entrypoint="adapter.py")
        bundles = tmp_path / "bundles"
        make_bundle(bundles, V1, files={"adapter.py": b"import sys\nsys.exit(0)\n"})
        use_bundles(ns, bundles)
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_adapter_hash_mismatch_refused(self, tmp_path):
        ns = render_ns(tmp_path, entrypoint="adapter.py")
        bundles = tmp_path / "bundles"
        make_bundle(bundles, V1, files={"adapter.py": b"import sys\nsys.exit(0)\n"},
                    manifest_extra={"files": {"adapter.py": {"sha256": "1" * 64}}})
        use_bundles(ns, bundles)
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}


# ─── R10 transport ────────────────────────────────────────────────────────────

class TestTransport:
    def _redirect(self, ns, newurl, auth=True):
        handler = ns["_RedirectGuard"]()
        req = urllib.request.Request("https://api.github.com/x",
                                     headers={"Authorization": "Bearer secret"} if auth else {})
        return handler.redirect_request(req, None, 302, "Found", {}, newurl)

    def test_https_to_http_redirect_refused(self, tmp_path):
        with _exits(1):
            self._redirect(render_ns(tmp_path), "http://github.com/x")

    def test_redirect_to_unlisted_host_refused(self, tmp_path):
        with _exits(1):
            self._redirect(render_ns(tmp_path), "https://evil.example.com/x")

    def test_no_authorization_follows_a_redirect(self, tmp_path):
        new = self._redirect(render_ns(tmp_path), "https://objects.githubusercontent.com/blob")
        assert new is not None
        assert new.get_header("Authorization") is None
        assert not [k for k in new.headers if k.lower() == "authorization"]
        assert not [k for k in new.unredirected_hdrs if k.lower() == "authorization"]

    def test_request_goes_through_the_guard(self, tmp_path):
        ns = render_ns(tmp_path)
        seen = {}

        def fake_build_opener(*handlers):
            seen["handlers"] = handlers
            opener = mock.Mock()
            opener.open.side_effect = ns["urllib"].error.URLError("stop")
            return opener

        with mock.patch.object(ns["urllib"].request, "build_opener", side_effect=fake_build_opener):
            with _exits(1):
                ns["_gh_request"]("https://api.github.com/x", token="t")
        assert seen["handlers"] == (ns["_RedirectGuard"],)

    def test_empty_stdin_token_is_config_error(self, tmp_path, monkeypatch):
        ns = render_ns(tmp_path)
        monkeypatch.setattr(ns["sys"], "stdin", __import__("io").StringIO(""))
        with _exits(2):
            ns["_resolve_token"](args(github_token_stdin=True))

    def test_stdin_token_used(self, tmp_path, monkeypatch):
        ns = render_ns(tmp_path)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("CMRU_GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(ns["sys"], "stdin", __import__("io").StringIO("tok\n"))
        assert ns["_resolve_token"](args(github_token_stdin=True)) == "tok"

    # The asset's REAL digest is used wherever the digest is not the defect, so each case
    # fails only because of the sidecar grammar (a wrong digest would be refused anyway).
    @pytest.mark.parametrize("text", [
        "", "   \n", "abc  bundle.tar.xz\n", sha(b"x")[:63] + "  bundle.tar.xz\n",
        sha(b"x") + "  other.tar.xz\n", sha(b"x") + " a b\n",
        sha(b"x") + "  bundle.tar.xz\n" + sha(b"x") + "  bundle.tar.xz\n",
        sha(b"x") + "  bundle.tar.xz\nextra line\n",
    ])
    def test_sidecar_parsed_strictly(self, tmp_path, text):
        ns = render_ns(tmp_path)
        asset = tmp_path / "bundle.tar.xz"
        asset.write_bytes(b"x")
        side = tmp_path / "bundle.tar.xz.sha256"
        side.write_text(text)
        with _exits(1):
            ns["_verify_sha256"](asset, side)

    def test_sidecar_bare_digest_and_star_name_accepted(self, tmp_path):
        ns = render_ns(tmp_path)
        asset = tmp_path / "bundle.tar.xz"
        asset.write_bytes(b"x")
        side = tmp_path / "s"
        side.write_text(sha(b"x").upper() + "\n")
        ns["_verify_sha256"](asset, side)
        side.write_text(f"{sha(b'x')} *bundle.tar.xz\n")
        ns["_verify_sha256"](asset, side)

    @pytest.mark.parametrize("status", [404, 500])
    def test_download_http_error_is_fatal(self, tmp_path, status):
        ns = render_ns(tmp_path)
        def fake(url, **kw):
            # an error body streamed into the destination must not pass as the asset
            kw["stream_to"].write_bytes(b"<html>error page</html>")
            return status, b""

        ns["_gh_request"] = fake
        with _exits(1):
            ns["_download_asset"]("demo-v1.0.0", "demo-v1.0.0.tar.xz", tmp_path / "d", None)


# ─── R11 rendering + config ───────────────────────────────────────────────────

BASE = dict(project_name="demo", repo_owner="o", repo_name="r", tag_prefix="demo-v",
            install_dir_system="/opt/demo", install_dir_user="demo")


class TestRendering:
    def test_quote_in_owner_refused_exit_2(self, tmp_path):
        cfg = tmp_path / "cmru.toml"
        cfg.write_text(
            'schema_version = 1\n[github]\nowner = \'o"x\'\nrepo = "r"\nowner_type = "user"\n'
            '[targets]\nhost = "github"\nregistry = []\n[runtime]\nkind = "none"\n'
            '[project]\nid = "demo"\ndescription = "d"\ntemplate_revision = 2\nprefix = "demo-v"\n'
            'artifacts = ["tarball"]\n[project.version]\nstrategy = "file:VERSION"\nbump = "conventional"\n'
            '[project.release]\ngit_tag = true\nbuild_step = "build"\n'
            '[project.installer]\ninstall_dir_system = "/opt/demo"\ninstall_dir_user = "demo"\n'
            '[steps.run-tests]\nquiet = true\ncommands = [{ label = "t", argv = ["true"], cwd = "." }]\n'
            '[steps.build]\nquiet = true\ncommands = [{ label = "b", argv = ["true"], cwd = "." }]\n'
            '[steps.push]\nquiet = true\ncommands = [{ label = "p", argv = ["true"], cwd = "." }]\n')
        assert getpy_main(["demo", "--config", str(cfg)]) == 2

    @pytest.mark.parametrize("field,value", [
        ("repo_owner", 'o"; import os'), ("repo_name", "r\nx"), ("project_name", "a b"),
        ("tag_prefix", 'p"'),
    ])
    def test_bad_values_raise_render_error(self, field, value):
        with pytest.raises(RenderError):
            render_get_py(**{**BASE, field: value})

    def test_two_renders_are_byte_identical(self):
        kw = {**BASE, "variants": [{"name": "py39", "label": 'Py "3.9" \\ x'}],
              "launchers": ["demo"], "wheel_specs": [("vendor/demo-*.whl", "demo")]}
        assert render_get_py(**kw) == render_get_py(**kw)

    def test_values_round_trip_through_json_literals(self):
        ns: dict = {}
        label = 'Py "3.9" \\ é\n'
        exec(compile(render_get_py(**{**BASE, "variants": [{"name": "py39", "label": label}],
                                      "preserve_paths": ['a"b/c'],
                                      "required_commands": ["python3"]}), "<g>", "exec"), ns)
        assert ns["VARIANTS"] == [{"name": "py39", "label": label}]
        assert ns["PRESERVE_PATHS"] == ['a"b/c']

    def test_unreplaced_placeholder_is_fatal(self, tmp_path):
        tmpl = tmp_path / "t.tmpl"
        tmpl.write_text('X = [[NOPE]]\n[[REPO_OWNER]]\n')
        with pytest.raises(RenderError, match=r"\[\[NOPE\]\]"):
            render_get_py(**BASE, template_path=tmpl)

    def test_extension_with_unknown_placeholder_is_fatal(self):
        frag = b"def _r(s):\n    print('[[NOPE]]')\n    return {}\n_EXTENSIONS.append(_r)\n"
        with pytest.raises(RenderError, match="NOPE"):
            render_get_py(**BASE, extensions=[("x.py", frag)])

    def test_value_containing_a_placeholder_is_not_resubstituted(self):
        out = render_get_py(**{**BASE, "variants": [{"name": "v", "label": "[[PROJECT_NAME]]"}]})
        assert '"label": "[[PROJECT_NAME]]"' in out

    @pytest.mark.parametrize("override", [
        {"install_dir_system": "opt/demo"}, {"install_dir_system": "/opt/../etc"},
        {"install_dir_user": "/abs"}, {"install_dir_user": "../x"},
        {"entrypoint": "../evil.py"}, {"entrypoint": "/abs.py"},
        {"asset_suffix": ".zip"}, {"manifest_name": "a/b.json"}, {"signature_name": ".."},
        {"manifest_pubkey": "short"}, {"launchers": ["a b"], "wheel_specs": [("v/*.whl", "d")]},
        {"launchers": ["demo"]},  # no wheels
        {"required_commands": ["a;b"]}, {"preserve_paths": ["../x"]},
        {"wheel_specs": [("../x.whl", "d")]}, {"wheel_specs": [("v/*.whl", "d d")]},
    ])
    def test_installer_grammar_refused(self, override):
        with pytest.raises(RenderError):
            render_get_py(**{**BASE, **override})

    def test_pubkey_and_launchers_rendered(self):
        key = "RWQf6LRCGA9i53mlYecO4IzT51TGPpvWucNSCh1CBM0QTaLn73Y7GFO3"
        ns: dict = {}
        exec(compile(render_get_py(**{**BASE, "manifest_pubkey": key, "launchers": ["demo"],
                                      "wheel_specs": [("vendor/demo-*.whl", "demo")]}),
                     "<g>", "exec"), ns)
        assert ns["MANIFEST_PUBKEY"] == key and ns["LAUNCHERS"] == ["demo"]
        assert "--manifest-pubkey" not in render_get_py(**BASE)


class TestConfigFlag:
    def _adapter_setup(self, tmp_path):
        ns = render_ns(tmp_path, entrypoint="adapter.py")
        marker = tmp_path / "seen-config"
        code = (
            "import sys, pathlib\n"
            "args = sys.argv\n"
            f"pathlib.Path({str(marker)!r}).write_text(args[args.index('--config') + 1])\n"
        ).encode()
        make_bundle(tmp_path / "b", V1, files={"adapter.py": code}, hash_files=["adapter.py"])
        use_bundles(ns, tmp_path / "b")
        return ns, marker

    def test_config_installed_0600_and_given_to_adapter(self, tmp_path):
        ns, marker = self._adapter_setup(tmp_path)
        host = tmp_path / "HOST.toml"
        host.write_text("a = 1\n")
        install(ns, version=V1, config=str(host))
        target = root_of(ns) / "shared" / "host.toml"
        assert target.read_text() == "a = 1\n"
        assert (target.stat().st_mode & 0o777) == 0o600
        assert marker.read_text() == str(host)
        assert not list((root_of(ns) / "shared").glob(".*.tmp"))

    def test_without_config_adapter_gets_shared_host_toml(self, tmp_path):
        ns, marker = self._adapter_setup(tmp_path)
        install(ns, version=V1)
        assert marker.read_text() == str(root_of(ns) / "shared" / "host.toml")

    def test_missing_config_file_is_config_error(self, tmp_path):
        ns, _ = self._adapter_setup(tmp_path)
        with _exits(2):
            install(ns, version=V1, config=str(tmp_path / "nope.toml"))
        assert snapshot(root_of(ns)) == {}


# ─── R2 signature policy ──────────────────────────────────────────────────────

@needs_minisign
class TestSignature:
    def _setup(self, tmp_path, *, sign=True, sign_comment=None, key_name="k"):
        pub, sec = minisign_keypair(tmp_path / "keys", key_name)
        ns = render_ns(tmp_path, manifest_pubkey=pub)
        make_bundle(tmp_path / "b", V1, files={"VERSION": b"1"},
                    sign_with=sec if sign else None, sign_comment=sign_comment)
        use_bundles(ns, tmp_path / "b")
        return ns, pub, sec

    def test_valid_signature_installs(self, tmp_path):
        ns, _pub, _sec = self._setup(tmp_path)
        install(ns, version=V1)
        state = _state(ns)
        assert state["current"]["tag"] == V1
        release = root_of(ns) / "releases" / state["current"]["name"]
        assert (release / "manifest.json.minisig").is_file()

    def test_cmru_producer_output_verifies_with_the_installer(self, tmp_path):
        """The shipped producer primitives (`manifest.write_manifest`/`build_trusted_comment`
        + `delegated.minisign_sign`) emit exactly the trusted comment the installer requires."""
        from cmru.delegated import minisign_sign
        from cmru.manifest import build_trusted_comment, write_manifest
        pub, sec = minisign_keypair(tmp_path / "keys")
        ns = render_ns(tmp_path, manifest_pubkey=pub)
        mpath = write_manifest({"schema_version": 1, "project": "demo", "tag": V1},
                               tmp_path / "m" / "manifest.json")
        minisign_sign(mpath, secret_key=str(sec), trusted_comment=build_trusted_comment(
            project="demo", tag=V1, manifest_path=mpath))
        make_bundle(tmp_path / "b", V1, files={"VERSION": b"1"},
                    manifest=mpath.read_bytes(),
                    signature=(tmp_path / "m" / "manifest.json.minisig").read_bytes())
        use_bundles(ns, tmp_path / "b")
        install(ns, version=V1)
        assert _state(ns)["current"]["tag"] == V1

    def test_missing_signature_refused_nothing_changed(self, tmp_path):
        ns, _pub, _sec = self._setup(tmp_path, sign=False)
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_signature_from_another_key_refused(self, tmp_path):
        ns, _pub, _sec = self._setup(tmp_path)
        _other_pub, other_sec = minisign_keypair(tmp_path / "keys", "other")
        make_bundle(tmp_path / "b", V1, files={"VERSION": b"1"}, sign_with=other_sec)
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_tampered_manifest_refused(self, tmp_path):
        ns, _pub, sec = self._setup(tmp_path)
        # a valid signature over the ORIGINAL manifest, but the bundle ships another one
        _a, original = make_bundle(tmp_path / "o", V1, files={"VERSION": b"1"})
        from tests.installer_fakes import minisign_sign
        sig = minisign_sign(sec, original, f"project=demo tag={V1} manifest_sha256={sha(original)}",
                            tmp_path / "s")
        make_bundle(tmp_path / "b", V1, files={"VERSION": b"1"},
                    manifest_extra={"extra": 1}, signature=sig)
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    @pytest.mark.parametrize("comment", [
        "project=demo tag=demo-v9.9.9 manifest_sha256=" + "0" * 64,  # replay of another tag
        "project=demo tag=demo-v1.0.0 manifest_sha256=" + "0" * 64,  # other manifest digest
        "nothing useful",
    ])
    def test_trusted_comment_must_bind_tag_and_digest(self, tmp_path, comment):
        ns, _pub, _sec = self._setup(tmp_path, sign_comment=comment)
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_signed_release_replayed_as_another_tag_refused(self, tmp_path):
        """A genuine signature whose manifest digest is right but whose trusted comment names
        ANOTHER tag (an old signed release replayed) is refused on the tag alone."""
        ns, _pub, sec = self._setup(tmp_path)
        _a, original = make_bundle(tmp_path / "o", V1, files={"VERSION": b"1"})
        from tests.installer_fakes import minisign_sign
        sig = minisign_sign(sec, original, f"project=demo tag={V2} manifest_sha256={sha(original)}",
                            tmp_path / "s")
        make_bundle(tmp_path / "b", V1, files={"VERSION": b"1"}, signature=sig)
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_minisign_required_before_any_network_io(self, tmp_path):
        pub, _sec = minisign_keypair(tmp_path / "keys")
        ns = render_ns(tmp_path, manifest_pubkey=pub)
        calls = []
        ns["_download_asset"] = lambda *a, **k: calls.append(a)
        ns["_gh_request"] = lambda *a, **k: calls.append(a)
        with mock.patch.object(ns["shutil"], "which", return_value=None):
            with _exits(3):
                install(ns)  # unpinned: would resolve latest over the network
        assert calls == [] and snapshot(root_of(ns)) == {}

    def test_unsigned_project_says_so_and_does_not_need_minisign(self, tmp_path, capsys):
        ns, _bundles = _plain_setup(tmp_path)
        with mock.patch.object(ns["shutil"], "which", return_value=None):
            install(ns, version=V1)
        assert "unsigned" in capsys.readouterr().out

    def test_status_reports_signed(self, tmp_path, capsys):
        ns, _pub, _sec = self._setup(tmp_path)
        install(ns, version=V1)
        ns["do_status"](args())
        assert "signed:   yes" in capsys.readouterr().out


# ─── R3 version pinning ───────────────────────────────────────────────────────

class TestPinning:
    def test_pinned_install_never_resolves_latest(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)

        def boom(*a, **k):
            raise AssertionError("latest was resolved for a pinned install")

        ns["resolve_latest_tag"] = boom
        install(ns, version=V1)
        assert _state(ns)["current"]["tag"] == V1

    def test_fake_newer_release_does_not_change_pinned_install(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        make_bundle(bundles, "demo-v9.9.9", files={"VERSION": b"evil"})
        ns["_gh_request"] = lambda *a, **k: (200, json.dumps(
            [{"tag_name": "demo-v9.9.9"}]).encode())
        install(ns, version="1.0.0")
        update(ns, version="2.0.0")
        assert _state(ns)["current"]["tag"] == V2

    def test_unpinned_resolves_and_prints_tag(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        ns["resolve_latest_tag"] = lambda token=None: V2
        install(ns)
        out = capsys.readouterr().out
        assert "Latest: " in out and V2 in out
        assert _state(ns)["current"]["tag"] == V2

    @pytest.mark.parametrize("bad", ["../etc", "demo-v1/../x", "demo-v1 0", "other-v1.0.0/x"])
    def test_hostile_tag_refused_before_network(self, tmp_path, bad):
        ns, _ = _plain_setup(tmp_path)
        ns["_download_asset"] = mock.Mock(side_effect=AssertionError("network"))
        with _exits(2):
            install(ns, version=bad)

    def test_hostile_latest_tag_from_api_refused(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        ns["_gh_json"] = lambda *a, **k: [{"tag_name": "demo-v1/../../x"}]
        with _exits(2):
            install(ns)

    def test_same_version_is_idempotent_noop(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        before = snapshot(root_of(ns))
        ns["_download_asset"] = mock.Mock(side_effect=AssertionError("network"))
        update(ns, version=V1)
        install(ns, version=V1)  # install over an existing install == update
        assert "Nothing to do" in capsys.readouterr().out
        assert snapshot(root_of(ns)) == before

    def test_noop_reverifies_recorded_manifest_digest(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        release = root_of(ns) / "releases" / _state(ns)["current"]["name"]
        (release / "manifest.json").write_text("{}")
        ns["_download_asset"] = mock.Mock(side_effect=AssertionError("network"))
        with _exits(1):
            update(ns, version=V1)

    def test_manifest_tag_must_equal_requested_tag(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        make_bundle(bundles, V1, files={"VERSION": b"x"}, manifest_extra={"tag": V2})
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_update_without_install_is_refused(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        with _exits(1):
            update(ns, version=V1)


# ─── R7 layout, rollback, migration ───────────────────────────────────────────

class TestLayoutAndRollback:
    def test_layout_after_install_and_update(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        update(ns, version=V2)
        root = root_of(ns)
        state = _state(ns)
        assert state["current"]["tag"] == V2 and state["previous"]["tag"] == V1
        assert state["history"][-1]["tag"] == V1
        for entry in (state["current"], state["previous"]):
            rel = root / "releases" / entry["name"]
            assert entry["name"] == f"{entry['tag']}-{entry['manifest_sha256'][:12]}"
            assert (rel / ".complete").is_file() and not (rel / ".incomplete").exists()
            assert (rel / "tree" / "VERSION").read_text() == entry["tag"]
            assert sha((rel / "manifest.json").read_bytes()) == entry["manifest_sha256"]
        assert (root / "current").resolve() == root / "releases" / state["current"]["name"]
        assert not list(root.glob(".*.tmp"))

    def test_rollback_goes_to_previous_and_toggles(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        update(ns, version=V2)
        rollback(ns)
        state = _state(ns)
        assert state["current"]["tag"] == V1 and state["previous"]["tag"] == V2
        assert (root_of(ns) / "current" / "tree" / "VERSION").read_text() == V1
        rollback(ns)
        assert _state(ns)["current"]["tag"] == V2

    def test_rollback_by_version_from_history(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        make_bundle(bundles, "demo-v3.0.0", files={"VERSION": b"3"})
        install(ns, version=V1)
        update(ns, version=V2)
        update(ns, version="3.0.0")
        # v1 was pruned (only current+previous are kept) -> not available
        with _exits(1):
            rollback(ns, version=V1)
        rollback(ns, version=V2)
        assert _state(ns)["current"]["tag"] == V2

    def test_rollback_without_previous_refused(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        with _exits(1):
            rollback(ns)
        assert ("pre-migration layout is not a rollback target; the first update "
                "after migration creates one") in capsys.readouterr().err

    def test_rollback_reverifies_previous(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        update(ns, version=V2)
        prev = root_of(ns) / "releases" / _state(ns)["previous"]["name"]
        (prev / "manifest.json").write_text("{}")
        before = _state(ns)
        with _exits(1):
            rollback(ns)
        assert _state(ns) == before

    def test_rollback_refuses_incomplete_previous(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        update(ns, version=V2)
        prev = root_of(ns) / "releases" / _state(ns)["previous"]["name"]
        (prev / ".complete").unlink()
        with _exits(1):
            rollback(ns)

    def test_failed_adapter_apply_changes_nothing(self, tmp_path):
        ns = render_ns(tmp_path, entrypoint="adapter.py")
        ok_code = b"import sys\nsys.exit(0)\n"
        bad_code = b"import sys\nsys.exit(7)\n"
        bundles = tmp_path / "b"
        make_bundle(bundles, V1, files={"adapter.py": ok_code}, hash_files=["adapter.py"])
        make_bundle(bundles, V2, files={"adapter.py": bad_code}, hash_files=["adapter.py"])
        use_bundles(ns, bundles)
        install(ns, version=V1)
        before = snapshot(root_of(ns))
        with _exits(1):
            update(ns, version=V2)
        assert snapshot(root_of(ns)) == before

    def test_failure_at_the_swap_removes_the_new_release(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        before = snapshot(root_of(ns))

        def boom(root, release):
            raise RuntimeError("swap failed")

        ns["_atomic_swap_current"] = boom
        with pytest.raises(RuntimeError):
            update(ns, version=V2)
        assert snapshot(root_of(ns)) == before

    def test_crash_mid_install_is_cleaned_on_next_run(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        root = root_of(ns)
        # simulate a SIGKILLed run: a half-built release dir with the incomplete marker
        dead = root / "releases" / "demo-v2.0.0-deadbeef0000"
        (dead / "tree").mkdir(parents=True)
        (dead / ".incomplete").write_text("")
        (dead / "tree" / "partial").write_text("x")
        v1_dir = (root / "current").resolve()
        update(ns, version=V2)
        assert not dead.exists()
        assert v1_dir.is_dir() and _state(ns)["previous"]["name"] == v1_dir.name
        assert _state(ns)["current"]["tag"] == V2

    def test_crash_cleanup_never_touches_current_when_update_fails(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        make_bundle(bundles, "demo-v9.9.9", manifest=None)
        install(ns, version=V1)
        root = root_of(ns)
        dead = root / "releases" / "junk"
        (dead).mkdir()
        (dead / ".incomplete").write_text("")
        cur = (root / "current").resolve()
        ino = cur.stat().st_ino
        with _exits(1):
            update(ns, version="9.9.9")  # no such bundle -> download fails
        assert not dead.exists() and cur.is_dir() and cur.stat().st_ino == ino

    def test_variant_switch_never_removes_what_current_points_at(self, tmp_path):
        ns = render_ns(tmp_path, variants=[{"name": "py39", "label": None},
                                           {"name": "py311", "label": None}])
        bundles = tmp_path / "b"
        for variant in ("py39", "py311"):
            make_bundle(bundles, V1, files={"variant.txt": variant.encode()}, variant=variant)
        use_bundles(ns, bundles)
        install(ns, version=V1, variant="py39")
        first = (root_of(ns) / "current").resolve()
        ino = first.stat().st_ino
        update(ns, version=V1, variant="py311")
        assert first.is_dir() and first.stat().st_ino == ino  # still there (as previous)
        assert (root_of(ns) / "current" / "tree" / "variant.txt").read_text() == "py311"
        assert _state(ns)["previous"]["variant"] == "py39"
        assert (root_of(ns) / "shared" / ".variant").read_text().strip() == "py311"

    def test_reusing_an_existing_complete_release(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        update(ns, version=V2)
        update(ns, version=V1)  # v1 is `previous`: reuse its verified dir, no rebuild
        state = _state(ns)
        assert state["current"]["tag"] == V1 and state["previous"]["tag"] == V2

    def test_state_behind_current_is_reconciled(self, tmp_path):
        """Crash between the symlink swap and the state write."""
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        stale = (root_of(ns) / "state.json").read_text()
        update(ns, version=V2)
        (root_of(ns) / "state.json").write_text(stale)
        state = ns["_read_state"](root_of(ns))
        assert state["current"]["tag"] == V2 and state["previous"]["tag"] == V1

    def test_corrupt_state_is_refused(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        (root_of(ns) / "state.json").write_text("{nope")
        with _exits(1):
            ns["_read_state"](root_of(ns))

    def test_dangling_current_is_refused(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        shutil.rmtree(root_of(ns) / "releases" / _state(ns)["current"]["name"])
        with _exits(1):
            ns["_read_state"](root_of(ns))

    def test_current_pointing_at_incomplete_release_refused(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        junk = root_of(ns) / "releases" / "junk-1"
        junk.mkdir()
        os.symlink("releases/junk-1", root_of(ns) / "current.new")
        os.replace(root_of(ns) / "current.new", root_of(ns) / "current")
        with _exits(1):
            ns["_read_state"](root_of(ns))

    def test_prune_keeps_only_current_and_previous(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        make_bundle(bundles, "demo-v3.0.0", files={"VERSION": b"3"})
        install(ns, version=V1)
        update(ns, version=V2)
        update(ns, version="3.0.0")
        names = {p.name for p in (root_of(ns) / "releases").iterdir()}
        state = _state(ns)
        assert names == {state["current"]["name"], state["previous"]["name"]}

    def test_status_output(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        ns["do_status"](args())
        assert "not installed" in capsys.readouterr().out
        install(ns, version=V1)
        update(ns, version=V2)
        capsys.readouterr()
        ns["do_status"](args())
        out = capsys.readouterr().out
        assert V2 in out and "previous: " + V1 in out and "signed:   no" in out

    def test_status_runs_adapter_health(self, tmp_path, capsys):
        ns = render_ns(tmp_path, entrypoint="adapter.py")
        code = b"import sys\nsys.exit(0 if sys.argv[1] != 'health' else 5)\n"
        make_bundle(tmp_path / "b", V1, files={"adapter.py": code}, hash_files=["adapter.py"])
        use_bundles(ns, tmp_path / "b")
        install(ns, version=V1)
        capsys.readouterr()
        ns["do_status"](args())
        assert "health check exited 5" in capsys.readouterr().err


class TestLegacyMigration:
    """A pre-W1 host (releases/<tag>/ content at the release root, shared <root>/venv) is
    migrated by the next install/update: nothing legacy is touched before the atomic swap."""

    def _legacy(self, ns):
        root = root_of(ns)
        old = root / "releases" / V1
        old.mkdir(parents=True)
        (old / "VERSION").write_text("legacy")
        (root / "venv" / "bin").mkdir(parents=True)
        (root / "venv" / "bin" / "legacy-tool").write_text("x")
        os.symlink(old, root / "current")  # the old template used an absolute target
        (root / "releases" / "demo-v0.0.1").mkdir()
        return old

    def test_update_migrates_atomically(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        old = self._legacy(ns)
        assert ns["_current_version"](root_of(ns)) == V1  # legacy current is recognised
        seen = []

        real_swap = ns["_atomic_swap_current"]

        def spy(root, release):
            seen.append((old.exists(), (root / "venv").exists()))  # legacy intact AT the swap
            real_swap(root, release)

        ns["_atomic_swap_current"] = spy
        update(ns, version=V2)
        assert seen == [(True, True)]
        root = root_of(ns)
        state = _state(ns)
        assert state["current"]["tag"] == V2 and state["previous"] is None
        assert not old.exists() and not (root / "venv").exists()
        assert not (root / "releases" / "demo-v0.0.1").exists()
        with _exits(1):
            rollback(ns)  # legacy has no recorded rollback target
        assert "not a rollback target" in capsys.readouterr().err

    def test_install_on_legacy_same_tag_still_migrates(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        self._legacy(ns)
        install(ns, version=V1)
        assert _state(ns)["current"]["name"].startswith(V1 + "-")
        assert (root_of(ns) / "current" / "tree" / "VERSION").read_text() == V1

    def test_failed_migration_leaves_the_legacy_install_working(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        old = self._legacy(ns)
        make_bundle(bundles, V2, manifest=None, files={"VERSION": b"2"})
        with _exits(1):
            update(ns, version=V2)
        assert (root_of(ns) / "current").resolve() == old and (old / "VERSION").exists()
        assert (root_of(ns) / "venv" / "bin" / "legacy-tool").exists()
        assert not (root_of(ns) / "state.json").exists()

    def test_status_on_legacy(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        self._legacy(ns)
        ns["do_status"](args())
        assert "pre-W1 layout" in capsys.readouterr().out

    def test_status_health_uses_legacy_paths(self, tmp_path):
        ns = render_ns(tmp_path, entrypoint="adapter.py")
        old = self._legacy(ns)
        (old / "adapter.py").write_text("")
        calls = []
        with mock.patch.object(ns["subprocess"], "run",
                               side_effect=lambda cmd, **k: calls.append(cmd) or mock.Mock(returncode=0)):
            ns["do_status"](args())
        cmd = calls[0]
        assert cmd[1] == str(old / "adapter.py") and "--manifest" in cmd


# ─── R8/R9: real venv + offline hash-locked wheels ────────────────────────────

class TestWheels:
    def _project(self, tmp_path, *, declared=("cli-extended", "demotool"), **kw):
        specs = {"cli-extended": ("vendor/cli_extended-*.whl", "cli-extended"),
                 "demotool": ("vendor/demotool-*.whl", "demotool")}
        ns = render_ns(tmp_path, wheel_specs=[specs[d] for d in declared],
                       launchers=["demotool"], **kw)
        return ns

    def _release(self, tmp_path, tag, version, *, requires=("cli-extended>=1",),
                 ship_cx=True, extra_manifest=None):
        wd = tmp_path / "w" / tag
        wheels = []
        if ship_cx:
            wheels.append(("cli-extended", make_wheel(wd, "cli-extended", "1.0.0")))
        wheels.append(("demotool", make_wheel(wd, "demotool", version, requires=requires,
                                              console="demotool")))
        make_bundle(tmp_path / "b", tag, wheels=wheels, manifest_extra=extra_manifest)

    def _run(self, root, *cmd):
        return subprocess.run([str(root / "bin" / cmd[0]), *cmd[1:]], capture_output=True,
                              text=True, env={"PATH": os.environ["PATH"]})

    def test_install_launcher_rollback_and_lock(self, tmp_path):
        ns = self._project(tmp_path, declared=("demotool", "cli-extended"))  # wrong order
        self._release(tmp_path, V1, "1.0.0")
        self._release(tmp_path, V2, "2.0.0")
        use_bundles(ns, tmp_path / "b")
        install(ns, version=V1)
        root = root_of(ns)
        assert self._run(root, "demotool").stdout.strip() == "demotool 1.0.0"
        cur = root / "current"
        lock = (cur.resolve() / "requirements.lock").read_text().splitlines()
        # KI-51: cli-extended first, hash-locked
        assert lock[0].startswith("cli-extended==1.0.0 --hash=sha256:")
        assert lock[1].startswith("demotool==1.0.0 --hash=sha256:")
        assert (cur.resolve() / "wheelhouse").is_dir()
        assert (root / "bin" / "demotool").is_symlink()
        assert os.readlink(root / "bin" / "demotool") == "../current/venv/bin/demotool"
        update(ns, version=V2)
        assert self._run(root, "demotool").stdout.strip() == "demotool 2.0.0"
        rollback(ns)
        assert self._run(root, "demotool").stdout.strip() == "demotool 1.0.0"
        rollback(ns)
        assert self._run(root, "demotool").stdout.strip() == "demotool 2.0.0"

    def test_pip_runs_offline_isolated_and_hash_locked(self, tmp_path):
        """The install command is the contract (S6.16): pip's own behaviour for each flag is not
        observable offline, so the argv of the one pip install is pinned."""
        ns = self._project(tmp_path)
        self._release(tmp_path, V1, "1.0.0")
        use_bundles(ns, tmp_path / "b")
        seen = []
        real = ns["_run_checked"]
        ns["_run_checked"] = lambda cmd, what: (seen.append(cmd), real(cmd, what))[1]
        install(ns, version=V1)
        pip = next(c for c in seen if c[1:3] == ["-m", "pip"] and "install" in c)
        for flag in ("--isolated", "--no-index", "--require-hashes"):
            assert flag in pip
        assert "-r" in pip and pip[pip.index("-r") + 1].endswith("requirements.lock")
        assert any(c[1:3] == ["-m", "pip"] and "check" in c for c in seen)

    def test_missing_dependency_fails_at_pip_offline_and_changes_nothing(self, tmp_path,
                                                                         monkeypatch):
        monkeypatch.setenv("PIP_INDEX_URL", "http://127.0.0.1:9/simple")  # unroutable
        ns = self._project(tmp_path)
        self._release(tmp_path, V1, "1.0.0", requires=("cli-extended>=1", "missingdep>=1"))
        use_bundles(ns, tmp_path / "b")
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_wheel_swapped_after_sha_check_fails_require_hashes(self, tmp_path):
        ns = self._project(tmp_path)
        self._release(tmp_path, V1, "1.0.0")
        use_bundles(ns, tmp_path / "b")
        real = ns["_verify_wheel_sha256"]

        def verify_then_swap(wheel, manifest, dist):
            digest = real(wheel, manifest, dist)
            wheel.write_bytes(wheel.read_bytes() + b"\0")  # swapped after the check
            return digest

        ns["_verify_wheel_sha256"] = verify_then_swap
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_wheel_glob_matching_two_files_refused(self, tmp_path):
        ns = render_ns(tmp_path, wheel_specs=[("vendor/demotool-*.whl", "demotool")])
        wd = tmp_path / "w" / "extra"
        extra = make_wheel(wd, "demotool", "1.0.1")
        make_bundle(tmp_path / "b", V1, files={f"vendor/{extra.name}": extra.read_bytes()},
                    wheels=[("demotool", make_wheel(wd, "demotool", "1.0.2"))])
        use_bundles(ns, tmp_path / "b")
        with _exits(1):
            install(ns, version=V1)

    def test_launcher_missing_from_venv_refused(self, tmp_path):
        ns = render_ns(tmp_path, wheel_specs=[("vendor/demotool-*.whl", "demotool")],
                       launchers=["nope"])
        make_bundle(tmp_path / "b", V1, wheels=[
            ("demotool", make_wheel(tmp_path / "w", "demotool", "1.0.0"))])
        use_bundles(ns, tmp_path / "b")
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_pip_env_is_cleared_of_pip_vars(self, tmp_path, monkeypatch):
        ns = render_ns(tmp_path)
        monkeypatch.setenv("PIP_INDEX_URL", "http://example.invalid")
        monkeypatch.setenv("KEEP", "1")
        env = ns["_pip_env"]()
        assert "PIP_INDEX_URL" not in env and env["KEEP"] == "1"

    def test_missing_venv_module_is_prereq_exit_3_before_network(self, tmp_path):
        ns = self._project(tmp_path)
        ns["_download_asset"] = mock.Mock(side_effect=AssertionError("network"))
        with mock.patch.dict(sys.modules, {"ensurepip": None}):
            with _exits(3):
                install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_wheel_order_hoists_cli_extended(self, tmp_path):
        ns = self._project(tmp_path, declared=("demotool", "cli-extended"))
        assert [d for _g, d in ns["_wheel_order"]()] == ["cli-extended", "demotool"]

    def test_bad_wheel_file_name_refused(self, tmp_path):
        ns = render_ns(tmp_path)
        with _exits(1):
            ns["_wheel_version"](Path("broken.whl"))
