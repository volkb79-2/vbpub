"""Tests for multi-variant bundle/tarball publish + variant-selecting installer (S-REL.6).

Covers:
  - Config:    [[project.X.variants]] parsing; unknown key / missing name / duplicate /
               invalid name rejected; no variants ⇒ [] (single-asset path unchanged).
  - Publish:   publish_versioned_variants asset naming (<tag>-<variant><suffix>), per-variant
               .sha256 sidecars + manifest/minisig extras, latest.json variant list + hashes.
  - find_artifact: resolve-by-(tag, variant) narrows a multi-variant dist to one file;
               a no-variant call still errors on >1 match (regression of the old guard).
  - Installer: render injects VARIANTS; --variant selection (given / remembered / missing→fail
               / invalid→fail); the selected variant drives the download asset name.
  - Regression: a no-variant project/render is unchanged, and the single-asset keystone
               publish_versioned still emits one asset + a latest.json with no "variants" key.

Stdlib + tmp files only — no network, no git.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path
from unittest import mock

import pytest

from cmru import release
from cmru.config import load_forge_config
from cmru.getpy import render_get_py, render_from_config


# ─── helpers ─────────────────────────────────────────────────────────────────

MINIMAL_GITHUB = """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"

[targets]
host = "github"
registry = []
"""


def _base_toml(extra_project: str = "") -> str:
    return (
        MINIMAL_GITHUB
        + """
[project.naf]
prefix    = "naf-v"
artifacts = ["bundle"]
cwd       = "naf"
[project.naf.version]
strategy = "file:VERSION"
"""
        + extra_project
    )


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cmru.toml"
    p.write_text(body)
    return p


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_tar_xz(path: Path, payload: bytes) -> Path:
    """Write a tiny .tar.xz so sha256/sidecar behaviour is exercised on a real archive."""
    import io
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:xz") as tf:
        info = tarfile.TarInfo(name="VERSION")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return path


class _FakeGH:
    """Records publish() calls; asset_download_url is deterministic (no network)."""

    def __init__(self) -> None:
        self.published: list = []

    def asset_download_url(self, tag: str, asset_name: str) -> str:
        return f"https://example.test/{tag}/{asset_name}"

    def publish(self, tag, title, notes, assets, *, recreate=False, target_commitish=None):
        self.published.append({
            "tag": tag,
            "notes": notes,
            "asset_names": [Path(a).name for a in assets],
            "asset_paths": [Path(a) for a in assets],
            "recreate": recreate,
            "target_commitish": target_commitish,
        })
        return {"id": len(self.published)}


# ─── Config: variant parsing ─────────────────────────────────────────────────

class TestVariantConfig:
    def test_variants_parsed(self, tmp_path):
        cfg = _write(tmp_path, _base_toml("""
[[project.naf.variants]]
name      = "py39"
build_arg = "PYTHON=3.9"
label     = "Python 3.9"

[[project.naf.variants]]
name = "py311"
"""))
        proj = load_forge_config(cfg).projects["naf"]
        assert [v.name for v in proj.variants] == ["py39", "py311"]
        assert proj.variants[0].build_arg == "PYTHON=3.9"
        assert proj.variants[0].label == "Python 3.9"
        assert proj.variants[1].build_arg is None
        assert proj.variants[1].label is None

    def test_no_variants_is_empty_list(self, tmp_path):
        """Regression: a project without [[variants]] keeps the single-asset shape."""
        cfg = _write(tmp_path, _base_toml())
        assert load_forge_config(cfg).projects["naf"].variants == []

    def test_unknown_variant_key_rejected(self, tmp_path):
        cfg = _write(tmp_path, _base_toml("""
[[project.naf.variants]]
name  = "py39"
bogus = "nope"
"""))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_missing_name_rejected(self, tmp_path):
        cfg = _write(tmp_path, _base_toml("""
[[project.naf.variants]]
label = "no name here"
"""))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_duplicate_variant_name_rejected(self, tmp_path):
        cfg = _write(tmp_path, _base_toml("""
[[project.naf.variants]]
name = "py39"
[[project.naf.variants]]
name = "py39"
"""))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_invalid_variant_name_rejected(self, tmp_path):
        cfg = _write(tmp_path, _base_toml("""
[[project.naf.variants]]
name = "py 3.9/../etc"
"""))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2


# ─── find_artifact: resolve by (tag, variant) ────────────────────────────────

class TestFindArtifactVariant:
    def _dist_with_two_variants(self, tmp_path) -> Path:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "naf-v1.0.0-py39.tar.xz").write_bytes(b"a")
        (dist / "naf-v1.0.0-py311.tar.xz").write_bytes(b"b")
        return dist

    def test_resolves_selected_variant(self, tmp_path):
        dist = self._dist_with_two_variants(tmp_path)
        got = release.find_artifact(dist, "naf-v*.tar.xz", variant="py311", suffix=".tar.xz")
        assert got.name == "naf-v1.0.0-py311.tar.xz"

    def test_no_variant_still_errors_on_multiple(self, tmp_path):
        """The old >1 guard is intact when no variant is requested."""
        dist = self._dist_with_two_variants(tmp_path)
        with pytest.raises(SystemExit):
            release.find_artifact(dist, "naf-v*.tar.xz")

    def test_unknown_variant_errors(self, tmp_path):
        dist = self._dist_with_two_variants(tmp_path)
        with pytest.raises(SystemExit):
            release.find_artifact(dist, "naf-v*.tar.xz", variant="py27", suffix=".tar.xz")

    def test_dotted_version_not_mistaken_for_suffix(self, tmp_path):
        """A dotted version (1.0.0) must not be stripped as a file extension."""
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "naf-v1.0.0-py39.tar.xz").write_bytes(b"a")
        got = release.find_artifact(dist, "naf-v*.tar.xz", variant="py39", suffix=".tar.xz")
        assert got.name == "naf-v1.0.0-py39.tar.xz"

    def test_variant_asset_name_helper(self):
        assert release.variant_asset_name("naf", "1.0.0", "py39", ".tar.xz") == \
            "naf-v1.0.0-py39.tar.xz"

    def test_overlapping_variant_names_disambiguated_not_misresolved(self, tmp_path):
        """A variant whose name is a dash-suffix of another's must never silently
        mis-resolve: find_artifact returns the exact file when unambiguous and
        refuses (errors) rather than picking the wrong one when ambiguous."""
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "naf-v1.0.0-py39.tar.xz").write_bytes(b"a")
        (dist / "naf-v1.0.0-musl-py39.tar.xz").write_bytes(b"b")
        # 'musl-py39' is unambiguous (nothing else ends in -musl-py39).
        got = release.find_artifact(dist, "naf-v*.tar.xz",
                                    variant="musl-py39", suffix=".tar.xz")
        assert got.name == "naf-v1.0.0-musl-py39.tar.xz"
        # 'py39' matches BOTH names' tails → fail-safe error, never the wrong file.
        with pytest.raises(SystemExit):
            release.find_artifact(dist, "naf-v*.tar.xz", variant="py39", suffix=".tar.xz")

    def test_sidecar_and_extras_excluded_by_suffix(self, tmp_path):
        """With suffix given, a variant's own .sha256 / manifest sidecars are not
        mistaken for the artifact — resolution stays exactly one file."""
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "naf-v1.0.0-py39.tar.xz").write_bytes(b"a")
        (dist / "naf-v1.0.0-py39.tar.xz.sha256").write_text("x  naf-v1.0.0-py39.tar.xz\n")
        (dist / "naf-v1.0.0-py39.manifest.json").write_text("{}")
        got = release.find_artifact(dist, "naf-v1.0.0-py39*",
                                    variant="py39", suffix=".tar.xz")
        assert got.name == "naf-v1.0.0-py39.tar.xz"


# ─── Publish: multi-variant ──────────────────────────────────────────────────

class TestPublishVariants:
    def _variants(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        variants = []
        for name, payload, label in [("py39", b"thirty-nine", "Python 3.9"),
                                     ("py311", b"three-eleven", "Python 3.11")]:
            tar = _make_tar_xz(dist / f"naf-v1.0.0-{name}.tar.xz", payload)
            vdir = dist / name
            vdir.mkdir()
            manifest = vdir / "manifest.json"
            manifest.write_text('{"schema_version":1}\n')
            sig = vdir / "manifest.json.minisig"
            sig.write_text("signature\n")
            variants.append(release.VariantArtifact(
                name=name, asset_path=tar, extra_assets=[manifest, sig], label=label,
            ))
        return dist, variants

    def test_per_variant_assets_and_sidecars(self, tmp_path):
        dist, variants = self._variants(tmp_path)
        gh = _FakeGH()
        result = release.publish_versioned_variants(
            gh, prefix="naf", version="1.0.0", variants=variants,
            asset_suffix=".tar.xz", notes="naf 1.0.0",
        )
        # First publish = the immutable versioned release.
        rel = gh.published[0]
        assert rel["tag"] == "naf-v1.0.0"
        # Every variant contributes tar + .sha256 + manifest + minisig.
        for name in ("py39", "py311"):
            assert f"naf-v1.0.0-{name}.tar.xz" in rel["asset_names"]
            assert f"naf-v1.0.0-{name}.tar.xz.sha256" in rel["asset_names"]
            assert f"naf-v1.0.0-{name}.manifest.json" in rel["asset_names"]
            assert f"naf-v1.0.0-{name}.manifest.json.minisig" in rel["asset_names"]

        # Sidecars on disk carry the true hash of the (canonically-named) tarball.
        for v in variants:
            asset = dist / f"naf-v1.0.0-{v.name}.tar.xz"
            sidecar = dist / f"naf-v1.0.0-{v.name}.tar.xz.sha256"
            assert sidecar.read_text().split()[0] == _sha256(asset)

        # Return records carry name/asset/sha256/url/label per variant.
        by_name = {r["name"]: r for r in result["variants"]}
        assert by_name["py39"]["asset"] == "naf-v1.0.0-py39.tar.xz"
        assert by_name["py39"]["label"] == "Python 3.9"
        assert by_name["py39"]["url"] == "https://example.test/naf-v1.0.0/naf-v1.0.0-py39.tar.xz"
        assert by_name["py39"]["sha256"] == _sha256(dist / "naf-v1.0.0-py39.tar.xz")

    def test_latest_json_records_variants_and_hashes(self, tmp_path):
        dist, variants = self._variants(tmp_path)
        gh = _FakeGH()
        release.publish_versioned_variants(
            gh, prefix="naf", version="1.0.0", variants=variants, asset_suffix=".tar.xz",
        )
        # Second publish = the thin -latest pointer (recreate), carrying latest.json only.
        latest = gh.published[1]
        assert latest["tag"] == "naf-latest"
        assert latest["recreate"] is True
        assert latest["asset_names"] == ["latest.json"]

        latest_json = json.loads((dist / "latest.json").read_text())
        assert latest_json["project"] == "naf"
        assert latest_json["version"] == "1.0.0"
        assert latest_json["tag"] == "naf-v1.0.0"
        names = [v["name"] for v in latest_json["variants"]]
        assert names == ["py39", "py311"]
        for v in latest_json["variants"]:
            asset = dist / v["asset"]
            assert v["sha256"] == _sha256(asset)
            assert v["url"].endswith(v["asset"])

    def test_canonical_rename_when_build_name_differs(self, tmp_path):
        """A build output not yet canonically named is uploaded as <tag>-<variant><suffix>."""
        dist = tmp_path / "dist"
        dist.mkdir()
        raw = _make_tar_xz(dist / "naf-py39-build.tar.xz", b"x")
        gh = _FakeGH()
        release.publish_versioned_variants(
            gh, prefix="naf", version="2.0.0",
            variants=[release.VariantArtifact(name="py39", asset_path=raw)],
            asset_suffix=".tar.xz",
        )
        assert (dist / "naf-v2.0.0-py39.tar.xz").exists()
        assert "naf-v2.0.0-py39.tar.xz" in gh.published[0]["asset_names"]

    def test_empty_variants_rejected(self, tmp_path):
        gh = _FakeGH()
        with pytest.raises(SystemExit):
            release.publish_versioned_variants(
                gh, prefix="naf", version="1.0.0", variants=[], asset_suffix=".tar.xz",
            )


# ─── Regression: single-asset keystone is untouched ──────────────────────────

class TestSingleAssetUnchanged:
    def test_publish_versioned_single_asset_no_variants_key(self, tmp_path):
        """publish_versioned (the untouched single path) still emits one asset + a
        latest.json WITHOUT a 'variants' key."""
        dist = tmp_path / "dist"
        dist.mkdir()
        asset = _make_tar_xz(dist / "tls-edge-v1.0.0.tar.xz", b"single")
        gh = _FakeGH()
        result = release.publish_versioned(
            gh, prefix="tls-edge", version="1.0.0", asset_path=asset, notes="tls-edge 1.0.0",
        )
        assert result["release_tag"] == "tls-edge-v1.0.0"
        assert "variants" not in result
        rel = gh.published[0]
        assert rel["asset_names"] == ["tls-edge-v1.0.0.tar.xz", "tls-edge-v1.0.0.tar.xz.sha256"]
        latest_json = json.loads((dist / "latest.json").read_text())
        assert latest_json["asset"] == "tls-edge-v1.0.0.tar.xz"
        assert "variants" not in latest_json


# ─── Installer: render + variant selection ───────────────────────────────────

def _render(variants=None, **kw) -> str:
    defaults = dict(
        project_name="naf", repo_owner="o", repo_name="r", tag_prefix="naf-v",
        install_dir_system="/opt/naf", install_dir_user="naf",
    )
    defaults.update(kw)
    return render_get_py(variants=variants, **defaults)


def _ns_from(src: str) -> dict:
    ns: dict = {}
    exec(compile(src, "<rendered-get.py>", "exec"), ns)
    return ns


class TestInstallerVariantRender:
    def test_no_variants_empty_and_no_placeholders(self):
        src = _render()
        assert not re.findall(r"\[\[[A-Z_]+\]\]", src)
        ns = _ns_from(src)
        assert ns["VARIANTS"] == []

    def test_variants_rendered(self):
        src = _render(variants=[{"name": "py39", "label": "Python 3.9"},
                                {"name": "py311", "label": None}])
        assert not re.findall(r"\[\[[A-Z_]+\]\]", src)
        ns = _ns_from(src)
        assert ns["VARIANTS"] == [{"name": "py39", "label": "Python 3.9"},
                                  {"name": "py311", "label": None}]

    def test_render_from_config_injects_variants(self, tmp_path):
        cfg = _write(tmp_path, _base_toml("""
[[project.naf.variants]]
name  = "py39"
label = "Python 3.9"
[[project.naf.variants]]
name = "py311"

[project.naf.installer]
install_dir_system = "/opt/naf"
install_dir_user   = "naf"
"""))
        src = render_from_config("naf", cfg)
        assert not re.findall(r"\[\[[A-Z_]+\]\]", src)
        ns = _ns_from(src)
        assert [v["name"] for v in ns["VARIANTS"]] == ["py39", "py311"]


class TestInstallerVariantSelection:
    def _ns(self):
        return _ns_from(_render(variants=[{"name": "py39", "label": "Python 3.9"},
                                          {"name": "py311", "label": None}]))

    def _args(self, variant):
        import argparse
        return argparse.Namespace(variant=variant)

    def test_no_variants_returns_none(self, tmp_path):
        ns = _ns_from(_render())  # VARIANTS == []
        assert ns["_select_variant"](self._args(None), tmp_path) is None

    def test_explicit_variant_selected(self, tmp_path):
        ns = self._ns()
        assert ns["_select_variant"](self._args("py311"), tmp_path) == "py311"

    def test_invalid_variant_fatal_exit_2(self, tmp_path):
        ns = self._ns()
        with pytest.raises(SystemExit) as exc:
            ns["_select_variant"](self._args("py27"), tmp_path)
        assert exc.value.code == 2

    def test_missing_variant_non_tty_fatal_lists_choices(self, tmp_path, capsys):
        ns = self._ns()
        with mock.patch.object(ns["sys"].stdin, "isatty", return_value=False):
            with pytest.raises(SystemExit) as exc:
                ns["_select_variant"](self._args(None), tmp_path)
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "py39" in err and "py311" in err

    def test_remembered_variant_used_on_update(self, tmp_path):
        ns = self._ns()
        marker = tmp_path / "shared" / ".variant"
        marker.parent.mkdir(parents=True)
        marker.write_text("py39\n")
        assert ns["_select_variant"](self._args(None), tmp_path) == "py39"

    def test_explicit_overrides_remembered(self, tmp_path):
        ns = self._ns()
        marker = tmp_path / "shared" / ".variant"
        marker.parent.mkdir(parents=True)
        marker.write_text("py39\n")
        assert ns["_select_variant"](self._args("py311"), tmp_path) == "py311"

    def test_persist_variant_writes_marker(self, tmp_path):
        ns = self._ns()
        ns["_persist_variant"](tmp_path, "py311")
        assert (tmp_path / "shared" / ".variant").read_text().strip() == "py311"

    def test_persist_none_is_noop(self, tmp_path):
        ns = self._ns()
        ns["_persist_variant"](tmp_path, None)
        assert not (tmp_path / "shared" / ".variant").exists()

    def test_installed_variant_reads_marker(self, tmp_path):
        ns = self._ns()
        assert ns["_installed_variant"](tmp_path) is None
        (tmp_path / "shared").mkdir()
        (tmp_path / "shared" / ".variant").write_text("py39\n")
        assert ns["_installed_variant"](tmp_path) == "py39"


class TestInstallerVariantDownloadName:
    """The selected variant drives the download asset name; None keeps the legacy name."""

    def _ns(self, variants):
        ns = _ns_from(_render(variants=variants))
        ns["MANIFEST_NAME"] = ""   # skip minisig extraction in this focused test
        return ns

    def _prepare_bundle(self, ns, workdir, asset_name):
        import io as _io
        src_dir = workdir / "src"
        src_dir.mkdir()
        asset = src_dir / asset_name
        with tarfile.open(asset, "w:xz") as tf:
            info = tarfile.TarInfo(name="VERSION")
            data = b"1.0.0\n"
            info.size = len(data)
            tf.addfile(info, _io.BytesIO(data))
        sidecar = src_dir / f"{asset_name}.sha256"
        sidecar.write_text(f"{_sha256(asset)}  {asset_name}\n")

        def fake_download(tag, name, dest, token):
            shutil.copy2(src_dir / name, dest)

        ns["_download_asset"] = fake_download
        return src_dir

    def test_variant_download_uses_variant_asset_name(self, tmp_path):
        ns = self._ns([{"name": "py311", "label": None}])
        self._prepare_bundle(ns, tmp_path, "naf-v1.0.0-py311.tar.xz")
        wd = tmp_path / "wd"
        wd.mkdir()
        got = ns["download_and_verify"]("naf-v1.0.0", wd, None, None, "py311")
        assert got.name == "naf-v1.0.0-py311.tar.xz"

    def test_no_variant_download_uses_legacy_name(self, tmp_path):
        ns = self._ns(None)
        self._prepare_bundle(ns, tmp_path, "naf-v1.0.0.tar.xz")
        wd = tmp_path / "wd"
        wd.mkdir()
        got = ns["download_and_verify"]("naf-v1.0.0", wd, None, None)
        assert got.name == "naf-v1.0.0.tar.xz"


# ─── Installer: variant transaction (install → same-version switch, S6.12) ────

class TestInstallerVariantTransaction:
    """End-to-end install/update proving a same-version ``--variant`` switch re-installs
    the selected variant instead of being short-circuited as 'nothing to do' (S6.12),
    while a same-version same-variant update stays a no-op."""

    def _ns(self, tmp_path):
        src = render_get_py(
            project_name="naf", repo_owner="o", repo_name="r", tag_prefix="naf-v",
            install_dir_system=str(tmp_path / "system"), install_dir_user="naf",
            entrypoint="", required_commands=[], preserve_paths=[],
            variants=[{"name": "py39", "label": "Python 3.9"},
                      {"name": "py311", "label": "Python 3.11"}],
        )
        ns: dict = {}
        exec(compile(src, "<rendered-get.py>", "exec"), ns)
        ns["MANIFEST_NAME"] = ""    # skip minisig extraction
        ns["SIGNATURE_NAME"] = ""
        return ns

    def _bundles(self, workdir, tag="naf-v1.0.0"):
        """One .tar.xz + .sha256 per variant; each carries a variant.txt marker so the
        installed release can be identified after a swap."""
        import io
        workdir.mkdir(parents=True, exist_ok=True)
        for variant in ("py39", "py311"):
            asset_name = f"{tag}-{variant}.tar.xz"
            asset = workdir / asset_name
            with tarfile.open(asset, "w:xz") as tf:
                for rel, content in (("VERSION", "1.0.0\n"), ("variant.txt", variant + "\n")):
                    data = content.encode()
                    ti = tarfile.TarInfo(name=f"{tag}/{rel}")
                    ti.size = len(data)
                    tf.addfile(ti, io.BytesIO(data))
            (workdir / f"{asset_name}.sha256").write_text(f"{_sha256(asset)}  {asset_name}\n")
        return workdir

    def _download_patch(self, ns, workdir):
        def fake_download(tag, name, dest, token):
            shutil.copy2(workdir / name, dest)
        ns["_download_asset"] = fake_download

    def _args(self, variant):
        import argparse
        return argparse.Namespace(version="naf-v1.0.0", scope="system",
                                  manifest_pubkey=None, variant=variant)

    def _installed_marker(self, ns):
        return (Path(ns["INSTALL_DIR_SYSTEM"]) / "current").resolve() / "variant.txt"

    def test_same_version_variant_switch_reinstalls(self, tmp_path):
        ns = self._ns(tmp_path)
        self._download_patch(ns, self._bundles(tmp_path / "bundles"))
        root = Path(ns["INSTALL_DIR_SYSTEM"])
        root.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(ns["os"], "geteuid", return_value=0):
            ns["do_install"](self._args("py39"), token=None)
        assert self._installed_marker(ns).read_text().strip() == "py39"
        assert (root / "shared" / ".variant").read_text().strip() == "py39"

        # Same version, DIFFERENT variant → must switch, not "nothing to do".
        with mock.patch.object(ns["os"], "geteuid", return_value=0):
            ns["do_update"](self._args("py311"), token=None)
        assert (root / "current").resolve().name == "naf-v1.0.0"
        assert self._installed_marker(ns).read_text().strip() == "py311"
        assert (root / "shared" / ".variant").read_text().strip() == "py311"

    def test_same_version_same_variant_is_noop(self, tmp_path, capsys):
        ns = self._ns(tmp_path)
        self._download_patch(ns, self._bundles(tmp_path / "bundles"))
        root = Path(ns["INSTALL_DIR_SYSTEM"])
        root.mkdir(parents=True, exist_ok=True)

        with mock.patch.object(ns["os"], "geteuid", return_value=0):
            ns["do_install"](self._args("py39"), token=None)
        capsys.readouterr()

        with mock.patch.object(ns["os"], "geteuid", return_value=0):
            ns["do_update"](self._args("py39"), token=None)
        assert "Nothing to do" in capsys.readouterr().out
        assert self._installed_marker(ns).read_text().strip() == "py39"
