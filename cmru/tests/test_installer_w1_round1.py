"""W1-INSTALLER review fix round 1: one test per blocker, ruling and surviving mutant.

Same harness as ``test_installer_w1``: rendered ``get.py`` exec'd against temp roots, local
fakes, no network, no real root. Each test asserts the observable effect (what is on disk,
the exit code), and fails on the pre-fix template.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from cmru.getpy import RenderError, render_from_config, render_get_py

from tests.installer_fakes import (
    args, install, make_bundle, make_wheel, minisign_keypair, minisign_sign, render_ns,
    rollback, root_of, sha, snapshot, update, use_bundles,
)
from tests.test_installer_w1 import (
    V1, V2, TestLegacyMigration, TestWheels, _exits, _plain_setup, _state, needs_minisign,
)

REPO = Path(__file__).resolve().parents[2]


def raw_bundle(workdir: Path, tag: str, entries, *, sidecar: bool = True) -> Path:
    """A bundle from explicit tar entries: ``(name, kind, payload, mode)`` where kind is
    file|sym|hard|dir|fifo and payload is the bytes (file) or the link target. Names are
    used verbatim (no top-level prefix), so a test can build any shape."""
    workdir.mkdir(parents=True, exist_ok=True)
    asset = workdir / f"{tag}.tar.xz"
    with tarfile.open(asset, "w:xz") as tf:
        for name, kind, payload, mode in entries:
            info = tarfile.TarInfo(name)
            info.mode = mode
            # owned by the test user, so a (mocked-root) extraction's chown succeeds and the
            # chmod after it runs: that is what makes a kept setuid bit observable
            info.uid, info.gid = os.getuid(), os.getgid()
            if kind == "file":
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                tf.addfile(info)
            else:
                info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                info.linkname = payload
                tf.addfile(info)
    if sidecar:
        (workdir / f"{asset.name}.sha256").write_text(
            f"{sha(asset.read_bytes())}  {asset.name}\n")
    return asset


def manifest_for(tag: str, files: dict) -> bytes:
    return json.dumps({"schema_version": 1, "tag": tag, "files": {
        rel: {"sha256": sha(data)} for rel, data in files.items()}}).encode()


def _unchanged_after_failure(tmp_path, tag, entries_for_v2):
    """Install v1, then a bad v2 (raw entries): exit 1 and <root> byte-identical."""
    ns, bundles = _plain_setup(tmp_path)
    install(ns, version=V1)
    before = snapshot(root_of(ns))
    raw_bundle(bundles, V2, entries_for_v2)
    with _exits(1):
        update(ns, version=V2)
    assert snapshot(root_of(ns)) == before
    return ns


def _std(tag, files, extra=(), top=None, manifest=None):
    top = tag if top is None else top
    entries = [(f"{top}/{rel}", "file", data, 0o644) for rel, data in files.items()]
    entries.append((f"{top}/manifest.json", "file",
                    manifest if manifest is not None else manifest_for(tag, files), 0o644))
    entries.extend(extra)
    return entries


# ─── Blocker 1: every extracted member must be hashed ────────────────────────

class TestUnlistedMembers:
    def test_unlisted_regular_member_refused_and_root_unchanged(self, tmp_path):
        files = {"VERSION": b"2"}
        _unchanged_after_failure(tmp_path, V2, _std(
            V2, files, extra=[(f"{V2}/json.py", "file", b"print('EVIL')", 0o644)]))

    def test_unlisted_member_on_fresh_install_leaves_nothing(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        files = {"VERSION": b"1"}
        raw_bundle(bundles, V1, _std(V1, files, extra=[(f"{V1}/x.py", "file", b"x", 0o644)]))
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_symlink_to_unlisted_target_refused(self, tmp_path):
        files = {"VERSION": b"2"}
        _unchanged_after_failure(tmp_path, V2, _std(
            V2, files, extra=[(f"{V2}/alias", "sym", "secret", 0o777)]))

    def test_symlink_escaping_the_bundle_refused(self, tmp_path):
        files = {"VERSION": b"2"}
        _unchanged_after_failure(tmp_path, V2, _std(
            V2, files, extra=[(f"{V2}/alias", "sym", "../../etc/passwd", 0o777)]))

    def test_hardlink_to_unlisted_target_refused(self, tmp_path):
        files = {"VERSION": b"2"}
        _unchanged_after_failure(tmp_path, V2, _std(
            V2, files, extra=[(f"{V2}/hl", "hard", f"{V2}/nothing-listed", 0o644)]))

    def test_fifo_member_refused(self, tmp_path):
        files = {"VERSION": b"2"}
        _unchanged_after_failure(tmp_path, V2, _std(
            V2, files, extra=[(f"{V2}/pipe", "fifo", None, 0o644)]))

    def test_symlink_and_hardlink_to_a_listed_file_are_allowed(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        files = {"VERSION": b"1"}
        raw_bundle(bundles, V1, _std(V1, files, extra=[
            (f"{V1}/alias", "sym", "VERSION", 0o777),
            (f"{V1}/hl", "hard", f"{V1}/VERSION", 0o644)]))
        install(ns, version=V1)
        tree = root_of(ns) / "current" / "tree"
        assert (tree / "alias").is_symlink() and (tree / "alias").read_text() == "1"
        assert (tree / "hl").read_text() == "1"

    def test_listed_wheel_glob_member_is_not_treated_as_unlisted(self, tmp_path):
        """A wheel is hashed through manifest[dist], not `files`: the coverage rule honours
        WHEEL_SPECS (the whole wheel suite installs; this pins the unlisted-wheel refusal)."""
        t = TestWheels()
        ns = t._project(tmp_path)
        wd = tmp_path / "w"
        extra = make_wheel(wd, "stranger", "1.0.0")  # outside every WHEEL_SPECS glob
        # the stranger wheel is shipped but declared nowhere
        make_bundle(tmp_path / "b", V1, wheels=[
            ("cli-extended", make_wheel(wd, "cli-extended", "1.0.0")),
            ("demotool", make_wheel(wd, "demotool", "1.0.0", console="demotool"))],
            extra_members=[(f"vendor/{extra.name}", extra.read_bytes())])
        use_bundles(ns, tmp_path / "b")
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}


class TestExtractionPrivileges:
    def _setuid_bundle(self, tmp_path, tag=V1):
        ns, bundles = _plain_setup(tmp_path)
        files = {"VERSION": b"1", "tool": b"x"}
        entries = [(f"{tag}/VERSION", "file", b"1", 0o644),
                   (f"{tag}/tool", "file", b"x", 0o6777),
                   (f"{tag}/manifest.json", "file", manifest_for(tag, files), 0o644)]
        raw_bundle(bundles, tag, entries)
        return ns

    def _modes(self, ns):
        return (root_of(ns) / "current" / "tree" / "tool").stat().st_mode

    def test_setuid_and_group_write_stripped(self, tmp_path):
        ns = self._setuid_bundle(tmp_path)
        install(ns, version=V1)
        mode = self._modes(ns)
        assert not mode & (stat.S_ISUID | stat.S_ISGID) and not mode & 0o022

    def test_stripped_even_if_the_interpreter_default_filter_is_trusting(
            self, tmp_path, monkeypatch):
        """Python 3.14 makes `data` the default filter; on older Pythons the default was
        trusting. The installer must pass the filter itself (M21)."""
        monkeypatch.setattr(tarfile.TarFile, "extraction_filter",
                            staticmethod(tarfile.fully_trusted_filter), raising=False)
        ns = self._setuid_bundle(tmp_path)
        install(ns, version=V1)
        assert not self._modes(ns) & (stat.S_ISUID | stat.S_ISGID | 0o022)

    def test_manual_stripping_when_the_interpreter_lacks_data_filter(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(tarfile.TarFile, "extraction_filter",
                            staticmethod(tarfile.fully_trusted_filter), raising=False)
        monkeypatch.delattr(tarfile, "data_filter")
        ns = self._setuid_bundle(tmp_path)
        install(ns, version=V1)
        mode = self._modes(ns)
        assert not mode & (stat.S_ISUID | stat.S_ISGID | 0o022)
        assert (root_of(ns) / "current" / "tree" / "tool").stat().st_uid == os.getuid()


# ─── Blocker 2: a failed migration keeps every legacy release ────────────────

def test_failed_migration_keeps_all_legacy_releases(tmp_path):
    ns, bundles = _plain_setup(tmp_path)
    old = TestLegacyMigration()._legacy(ns)
    rollback_target = root_of(ns) / "releases" / "demo-v0.0.1"
    (rollback_target / "VERSION").write_text("old rollback target")
    make_bundle(bundles, V2, manifest=None, files={"VERSION": b"2"})
    with _exits(1):
        update(ns, version=V2)
    assert (rollback_target / "VERSION").read_text() == "old rollback target"
    assert (old / "VERSION").exists()


def test_successful_migration_still_prunes_old_legacy_releases(tmp_path):
    ns, _ = _plain_setup(tmp_path)
    TestLegacyMigration()._legacy(ns)
    update(ns, version=V2)
    assert not (root_of(ns) / "releases" / "demo-v0.0.1").exists()


# ─── Blocker 3: --version "" ─────────────────────────────────────────────────

@pytest.mark.parametrize("verb", [install, update])
def test_empty_version_is_a_config_error_never_latest(tmp_path, verb):
    ns, _ = _plain_setup(tmp_path)
    if verb is update:
        install(ns, version=V1)
    before = snapshot(root_of(ns))

    def poisoned(token=None):
        raise AssertionError("an empty --version must not resolve latest")

    ns["resolve_latest_tag"] = poisoned
    with _exits(2):
        verb(ns, version="")
    assert snapshot(root_of(ns)) == before


def test_empty_version_on_rollback_is_refused(tmp_path):
    ns, _ = _plain_setup(tmp_path)
    install(ns, version=V1)
    update(ns, version=V2)
    before = snapshot(root_of(ns))
    with _exits(2):
        rollback(ns, version="")
    assert snapshot(root_of(ns)) == before


# ─── Blocker 4: global pip config ────────────────────────────────────────────

class TestPipConfig:
    def test_pip_env_disables_every_config_file(self, monkeypatch):
        ns = render_ns(Path("/nonexistent"))
        monkeypatch.setenv("PIP_INDEX_URL", "http://evil/")
        env = ns["_pip_env"]()
        assert env["PIP_CONFIG_FILE"] == os.devnull
        assert "PIP_INDEX_URL" not in env

    def test_global_pip_conf_cannot_redirect_the_install(self, tmp_path, monkeypatch):
        xdg = tmp_path / "xdg"
        (xdg / "pip").mkdir(parents=True)
        elsewhere = tmp_path / "elsewhere"
        (xdg / "pip" / "pip.conf").write_text(f"[install]\ntarget = {elsewhere}\n")
        monkeypatch.setenv("XDG_CONFIG_DIRS", str(xdg))
        t = TestWheels()
        ns = t._project(tmp_path)
        t._release(tmp_path, V1, "1.0.0")
        use_bundles(ns, tmp_path / "b")
        install(ns, version=V1)
        assert (root_of(ns) / "current" / "venv" / "bin" / "demotool").exists()
        assert not elsewhere.exists()


def test_require_hashes_is_enforced_by_pip(tmp_path):
    """Behavioural (reviewer's probe): a lock stripped of its hashes just before pip runs
    must make the install fail, which only `--require-hashes` guarantees."""
    t = TestWheels()
    ns = t._project(tmp_path)
    t._release(tmp_path, V1, "1.0.0")
    use_bundles(ns, tmp_path / "b")
    real = ns["_run_checked"]

    def strip(cmd, what):
        if "install" in cmd and "-r" in cmd:
            lock = Path(cmd[cmd.index("-r") + 1])
            lock.write_text(re.sub(r" --hash=\S+", "", lock.read_text()))
        return real(cmd, what)

    ns["_run_checked"] = strip
    with _exits(1):
        install(ns, version=V1)
    assert snapshot(root_of(ns)) == {}


# ─── Blocker 5: no-op path still applies --config and launchers ─────────────

def test_noop_update_still_installs_config(tmp_path):
    ns, _ = _plain_setup(tmp_path)
    install(ns, version=V1)
    cfg = tmp_path / "h.toml"
    cfg.write_text("a = 1\n")
    install(ns, version=V1, config=str(cfg))
    host = root_of(ns) / "shared" / "host.toml"
    assert host.read_text() == "a = 1\n" and (host.stat().st_mode & 0o777) == 0o600


def test_noop_update_rewrites_missing_launchers(tmp_path):
    t = TestWheels()
    ns = t._project(tmp_path)
    t._release(tmp_path, V1, "1.0.0")
    use_bundles(ns, tmp_path / "b")
    install(ns, version=V1)
    launcher = root_of(ns) / "bin" / "demotool"
    launcher.unlink()
    install(ns, version=V1)
    assert launcher.is_symlink() and os.readlink(launcher) == "../current/venv/bin/demotool"


# ─── Blocker 6: a preserved path that the manifest also hashes ──────────────

class TestPreservedAndHashed:
    def _setup(self, tmp_path):
        ns = render_ns(tmp_path, preserve_paths=["conf.toml"])
        b = tmp_path / "b"
        for tag, body in ((V1, b"x"), (V2, b"y"), ("demo-v3.0.0", b"z")):
            make_bundle(b, tag, files={"conf.toml": body, "VERSION": tag.encode()})
        use_bundles(ns, b)
        install(ns, version=V1)
        return ns

    def test_update_repeat_and_rollback_all_verify(self, tmp_path):
        ns = self._setup(tmp_path)
        update(ns, version=V2)  # copies v1's conf.toml to shared, links it into v2
        tree = root_of(ns) / "current" / "tree"
        assert (tree / "conf.toml").is_symlink()
        (root_of(ns) / "shared" / "conf.toml").write_text("mine")  # the operator's edit
        update(ns, version=V2)  # no-op re-verification: the link is not "wrong content"
        rollback(ns)  # re-verifies v1
        assert _state(ns)["current"]["tag"] == V1
        rollback(ns)  # and back to v2, whose link into shared must still verify
        assert _state(ns)["current"]["tag"] == V2
        assert (root_of(ns) / "shared" / "conf.toml").read_text() == "mine"

    def test_second_update_does_not_copy_the_symlink_onto_itself(self, tmp_path):
        """M55: the current release's preserved path is already a link into shared."""
        ns = self._setup(tmp_path)
        update(ns, version=V2)
        (root_of(ns) / "shared" / "conf.toml").write_text("mine")
        update(ns, version="demo-v3.0.0")
        assert (root_of(ns) / "shared" / "conf.toml").read_text() == "mine"
        assert (root_of(ns) / "current" / "tree" / "conf.toml").read_text() == "mine"

    def test_a_preserved_path_symlinked_elsewhere_is_still_verified(self, tmp_path):
        """The skip applies only to links into <root>/shared, not any symlink."""
        ns = self._setup(tmp_path)
        update(ns, version=V2)
        link = root_of(ns) / "current" / "tree" / "conf.toml"
        other = tmp_path / "other"
        other.write_text("y")
        link.unlink()
        link.symlink_to(other)
        with _exits(1):
            update(ns, version=V2)


# ─── Rulings B: root-directory ownership ─────────────────────────────────────

class TestRootDirectories:
    def _ns(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        return ns

    def test_world_writable_root_refused_and_untouched(self, tmp_path):
        ns = self._ns(tmp_path)
        root = root_of(ns)
        root.mkdir(parents=True)
        os.chmod(root, 0o777)
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root) == {}

    def test_group_writable_releases_dir_refused(self, tmp_path):
        ns = self._ns(tmp_path)
        install(ns, version=V1)
        os.chmod(root_of(ns) / "releases", 0o775)
        before = snapshot(root_of(ns))
        with _exits(1):
            update(ns, version=V2)
        assert snapshot(root_of(ns)) == before

    def test_shared_symlink_refused_and_target_untouched(self, tmp_path):
        ns = self._ns(tmp_path)
        root = root_of(ns)
        root.mkdir(parents=True)
        ext = tmp_path / "ext"
        ext.mkdir()
        os.symlink(ext, root / "shared")
        cfg = tmp_path / "h.toml"
        cfg.write_text("secret = 1\n")
        with _exits(1):
            install(ns, version=V1, config=str(cfg))
        assert list(ext.iterdir()) == []

    def test_lock_symlink_refused_and_nothing_created(self, tmp_path):
        ns = self._ns(tmp_path)
        root = root_of(ns)
        root.mkdir(parents=True)
        victim = tmp_path / "newfile"
        os.symlink(victim, root / ".lock")
        with _exits(1):
            install(ns, version=V1)
        assert not victim.exists()

    def test_root_owned_by_someone_else_refused(self, tmp_path):
        ns = self._ns(tmp_path)
        ns["_expected_owner"] = lambda: os.getuid() + 12345
        with mock.patch.object(ns["os"], "geteuid", return_value=0):
            with _exits(1):
                ns["do_install"](args(version=V1), None)
        assert not (root_of(ns) / "releases").exists()

    def test_created_directories_are_0755_and_a_real_install_passes(self, tmp_path):
        ns = self._ns(tmp_path)
        install(ns, version=V1)
        root = root_of(ns)
        for d in (root, root / "releases"):
            assert stat.S_IMODE(d.stat().st_mode) == 0o755

    def test_unprivileged_scope_skips_the_ownership_rule(self, tmp_path, monkeypatch):
        """User scope is the caller's own tree: the root-only checks do not apply."""
        ns = self._ns(tmp_path)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        root = ns["_root_dir"]("user")
        root.mkdir(parents=True)
        os.chmod(root, 0o777)
        ns["_check_dir"](root, "Install root")  # no exception


# ─── Ruling C: downgrade guard ───────────────────────────────────────────────

class TestDowngradeGuard:
    def test_unpinned_update_to_an_older_latest_refused(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V2)
        ns["resolve_latest_tag"] = lambda token=None: V1
        before = snapshot(root_of(ns))
        with _exits(1):
            update(ns)
        assert snapshot(root_of(ns)) == before

    def test_message_says_to_pass_version(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V2)
        ns["resolve_latest_tag"] = lambda token=None: V1
        with _exits(1):
            update(ns)
        assert f"--version {V1}" in capsys.readouterr().err

    def test_explicit_older_version_is_allowed_with_a_notice(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V2)
        update(ns, version=V1)
        assert _state(ns)["current"]["tag"] == V1
        assert "Downgrade" in capsys.readouterr().err

    def test_unpinned_update_to_a_newer_latest_is_silent_and_works(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        ns["resolve_latest_tag"] = lambda token=None: V2
        update(ns)
        assert _state(ns)["current"]["tag"] == V2
        assert "Downgrade" not in capsys.readouterr().err


# ─── Ruling A: tls-edge installs from a bundle the real builder produced ─────

class TestTlsEdgeBundle:
    CENTRAL = REPO / "cmru.orchestration.toml"
    TAG = "tls-edge-v1.2.3"
    TAG2 = "tls-edge-v1.2.4"

    @pytest.fixture(autouse=True)
    def _epoch(self, monkeypatch):
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")  # the cmru runner sets this

    def _stage(self, tmp_path, conf: bytes, tag=None):
        tag = tag or self.TAG
        stage = tmp_path / "stage" / tag
        files = {
            "VERSION": tag.encode() + b"\n",
            "get.py": b"# shipped get.py\n",
            "scripts/render.sh": b"#!/bin/sh\necho hi\n",
            "ciu-stack/ciu.toml.j2": conf,
            "ciu-stack/conf.d/options.yml": b"a: 1\n",
            "edge-proxy/compose.yml": b"services: {}\n",
        }
        for rel, data in files.items():
            path = stage / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        os.chmod(stage / "scripts" / "render.sh", 0o755)
        return stage

    def _build(self, tmp_path, stage: Path) -> Path:
        """Manifest via the real `cmru handler bundle-manifest`, then tar the stage dir."""
        from cmru.handlers import cmd_bundle_manifest
        tag = stage.name
        cmd_bundle_manifest(args_ns(name="tls-edge", tag=tag, root=str(stage),
                                    manifest_name="manifest.json"))
        out = tmp_path / "bundles"
        out.mkdir(exist_ok=True)
        asset = out / f"{tag}.tar.xz"
        with tarfile.open(asset, "w:xz") as tf:
            tf.add(stage, arcname=tag)
        (out / f"{asset.name}.sha256").write_text(f"{sha(asset.read_bytes())}  {asset.name}\n")
        return out

    def _ns(self, tmp_path, bundles):
        if not (REPO / "tls-edge" / "cmru.toml").exists():
            pytest.skip("tls-edge not present")
        src = render_from_config("tls-edge", self.CENTRAL)
        ns: dict = {}
        exec(compile(src, "<tls-edge/get.py>", "exec"), ns)
        ns["INSTALL_DIR_SYSTEM"] = str(tmp_path / "opt-tls-edge")
        use_bundles(ns, bundles)
        return ns

    def test_installs_updates_and_preserves_operator_files(self, tmp_path):
        self._build(tmp_path, self._stage(tmp_path, b"default conf\n"))
        bundles = self._build(tmp_path, self._stage(tmp_path, b"default conf\n", self.TAG2))
        ns = self._ns(tmp_path, bundles)
        install(ns, version=self.TAG)
        tree = root_of(ns) / "current" / "tree"
        assert (tree / "VERSION").read_text() == self.TAG + "\n"
        assert os.access(tree / "scripts" / "render.sh", os.X_OK)
        update(ns, version=self.TAG2)  # the preserved + hashed conf is linked into shared
        conf = root_of(ns) / "current" / "tree" / "ciu-stack" / "ciu.toml.j2"
        assert conf.is_symlink() and conf.read_text() == "default conf\n"
        (root_of(ns) / "shared" / "ciu-stack" / "ciu.toml.j2").write_text("operator conf\n")
        update(ns, version=self.TAG2)  # re-verified no-op with the operator's edit
        rollback(ns)
        rollback(ns)
        assert _state(ns)["current"]["tag"] == self.TAG2
        assert conf.read_text() == "operator conf\n"

    def test_the_manifest_lists_every_regular_file_with_hash_size_mode(self, tmp_path):
        stage = self._stage(tmp_path, b"c\n")
        self._build(tmp_path, stage)
        manifest = json.loads((stage / "manifest.json").read_text())
        files = manifest["files"]
        assert set(files) == {"VERSION", "get.py", "scripts/render.sh",
                              "ciu-stack/ciu.toml.j2", "ciu-stack/conf.d/options.yml",
                              "edge-proxy/compose.yml"}
        entry = files["scripts/render.sh"]
        assert entry["sha256"] == sha(b"#!/bin/sh\necho hi\n")
        assert entry["size"] == len(b"#!/bin/sh\necho hi\n") and entry["mode"] == 0o755
        assert manifest["schema_version"] == 1 and manifest["tag"] == self.TAG

    def test_a_bundle_with_a_file_added_after_the_manifest_is_refused(self, tmp_path):
        stage = self._stage(tmp_path, b"c\n")
        self._build(tmp_path, stage)
        (stage / "late.sh").write_text("evil")
        out = tmp_path / "late"
        out.mkdir()
        asset = out / f"{self.TAG}.tar.xz"
        with tarfile.open(asset, "w:xz") as tf:
            tf.add(stage, arcname=self.TAG)
        (out / f"{asset.name}.sha256").write_text(f"{sha(asset.read_bytes())}  {asset.name}\n")
        ns = self._ns(tmp_path, out)
        with _exits(1):
            install(ns, version=self.TAG)

    def test_build_manifest_refuses_a_symlink_in_the_stage(self, tmp_path):
        stage = self._stage(tmp_path, b"c\n")
        os.symlink("VERSION", stage / "alias")
        from cmru.manifest import bundle_files
        with pytest.raises(ValueError, match="symlink"):
            bundle_files(stage, exclude=("manifest.json",))

    def test_build_manifest_embeds_files_when_given_a_bundle_root(self, tmp_path):
        stage = self._stage(tmp_path, b"c\n")
        from cmru.manifest import build_manifest
        from tests.test_bundle_manifest_sign import _fake_wheel
        common = dict(
            project="p", tag="p-v1.0.0", source_commit="a" * 40,
            cmru_wheel=_fake_wheel(tmp_path, "cmru", "0.9.0"),
            ciu_wheel=_fake_wheel(tmp_path, "ciu", "1.2.3"), images=None,
            installer_schema_version=1, host_config_schema_version=1,
            platform={"min_python": "3.11", "arch": ["amd64"]},
            upgrade={"min_from": "p-v0.9.0", "rollback_to": ["p-v0.9.0"]})
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1700000000"}):
            with_files = build_manifest(**common, bundle_root=stage)
            without = build_manifest(**common)
        assert "files" not in without
        assert with_files["files"]["VERSION"]["sha256"] == sha(self.TAG.encode() + b"\n")
        assert {k: v for k, v in with_files.items() if k != "files"} == without

    def test_build_artifact_script_calls_the_builder_after_clamping(self):
        script = (REPO / "tls-edge" / "scripts" / "build-artifact.sh").read_text()
        assert "handler bundle-manifest" in script
        call = script.index("cmru handler bundle-manifest --name")
        assert script.rindex("touch -t", 0, call) < call < script.index("tar -C")


def args_ns(**kw):
    import argparse
    return argparse.Namespace(**kw)


# ─── Surviving mutants: M04 M05 M08 M11 M12 M18 M22 M23 M24 M25 M49 M59 ────

class TestSurvivorsKilled:
    def test_m04_duplicate_member_refused(self, tmp_path):
        files = {"VERSION": b"2"}
        _unchanged_after_failure(tmp_path, V2, _std(
            V2, files, extra=[(f"{V2}/VERSION", "file", b"2", 0o644)]))

    def test_m05_manifest_that_is_a_symlink_to_a_valid_manifest_is_refused(self, tmp_path):
        """`manifest.json` -> `manifest.json.minisig`, whose bytes are a perfectly valid
        manifest. Both names are exempt from the unlisted-member rule (they are read
        separately), so only the manifest-must-be-a-regular-file rule stops it."""
        files = {"VERSION": b"2"}
        entries = [(f"{V2}/VERSION", "file", b"2", 0o644),
                   (f"{V2}/manifest.json", "sym", "manifest.json.minisig", 0o777),
                   (f"{V2}/manifest.json.minisig", "file", manifest_for(V2, files), 0o644)]
        _unchanged_after_failure(tmp_path, V2, entries)

    def test_m08_recorded_manifest_digest_is_compared_on_reuse(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        state_path = root_of(ns) / "state.json"
        state = json.loads(state_path.read_text())
        state["current"]["manifest_sha256"] = "0" * 64
        state_path.write_text(json.dumps(state))
        with _exits(1):
            install(ns, version=V1)

    def test_m11_a_tmp_symlink_planted_in_the_race_window_is_not_followed(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        victim = tmp_path / "victim"
        victim.write_text("keep")
        target = tmp_path / "out" / "f"
        target.parent.mkdir()
        real_open = os.open

        def racing_open(path, flags, *a, **k):
            if str(path).endswith(".f.tmp"):  # lands after the installer's unlink
                os.symlink(victim, path)
            return real_open(path, flags, *a, **k)

        with mock.patch.object(os, "open", racing_open):
            with pytest.raises(OSError):
                ns["_atomic_write"](target, b"new", 0o600)
        assert victim.read_text() == "keep" and not target.exists()

    def test_m12_a_listed_file_replaced_by_a_symlink_is_refused(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        tree = root_of(ns) / "current" / "tree"
        copy = tmp_path / "copy"
        copy.write_bytes((tree / "VERSION").read_bytes())  # identical content
        (tree / "VERSION").unlink()
        (tree / "VERSION").symlink_to(copy)
        with _exits(1):
            install(ns, version=V1)

    def test_m18_the_lock_excludes_a_second_process(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        root = root_of(ns)
        root.mkdir(parents=True)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, os, sys, time\n"
             f"fd = os.open({str(root / '.lock')!r}, os.O_CREAT | os.O_WRONLY, 0o600)\n"
             "fcntl.flock(fd, fcntl.LOCK_EX)\n"
             "print('held', flush=True)\n"
             "sys.stdin.readline()\n"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "held"
            entered = threading.Event()

            def take():
                with ns["_Lock"](root):
                    entered.set()

            worker = threading.Thread(target=take, daemon=True)
            worker.start()
            time.sleep(1.0)
            assert not entered.is_set(), "second process got the lock while it was held"
            holder.stdin.write("\n")
            holder.stdin.flush()
            worker.join(timeout=15)
            assert entered.is_set() and not worker.is_alive()
        finally:
            holder.kill()
            holder.wait()

    def test_m22_system_scope_without_root_exits_3_and_touches_nothing(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        with mock.patch.object(ns["os"], "geteuid", return_value=1000):
            for verb in ("do_install", "do_update", "do_rollback"):
                with _exits(3):
                    ns[verb](args(version=V1), None)
        assert not root_of(ns).exists()

    def test_m23_manifest_wheel_name_must_match_the_bundled_file(self, tmp_path):
        t = TestWheels()
        ns = t._project(tmp_path)
        wd = tmp_path / "w"
        cx = make_wheel(wd, "cli-extended", "1.0.0")
        tool = make_wheel(wd, "demotool", "1.0.0", console="demotool")
        entry = {"wheel": "demotool-9.9.9-py3-none-any.whl", "sha256": sha(tool.read_bytes()),
                 "size": tool.stat().st_size}
        make_bundle(tmp_path / "b", V1, wheels=[("cli-extended", cx), ("demotool", tool)],
                    manifest_extra={"demotool": entry})
        use_bundles(ns, tmp_path / "b")
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_m24_two_wheels_matching_one_glob_refused(self, tmp_path):
        t = TestWheels()
        ns = t._project(tmp_path)
        wd = tmp_path / "w"
        cx = make_wheel(wd, "cli-extended", "1.0.0")
        t1 = make_wheel(wd, "demotool", "1.0.0", console="demotool")
        # a byte-identical second copy under another name: the manifest's hash (no `wheel`
        # name given) fits BOTH, so only the exactly-one-match rule can refuse the bundle
        entry = {"sha256": sha(t1.read_bytes()), "size": t1.stat().st_size}
        make_bundle(tmp_path / "b", V1, wheels=[("cli-extended", cx), ("demotool", t1)],
                    manifest_extra={"demotool": entry},
                    extra_members=[("vendor/demotool-1.0.1-py3-none-any.whl", t1.read_bytes())])
        use_bundles(ns, tmp_path / "b")
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    def test_m25_rollback_invokes_the_adapter_with_action_rollback(self, tmp_path):
        ns = render_ns(tmp_path, entrypoint="adapter.py")
        log = tmp_path / "actions"
        code = (f"import sys\nopen({str(log)!r}, 'a').write(sys.argv[1] + '\\n')\n").encode()
        for tag in (V1, V2):
            make_bundle(tmp_path / "b", tag, files={"adapter.py": code})
        use_bundles(ns, tmp_path / "b")
        install(ns, version=V1)
        update(ns, version=V2)
        log.write_text("")
        rollback(ns)
        assert log.read_text().split() == ["rollback"]

    def test_m25_a_failing_rollback_adapter_aborts_the_rollback(self, tmp_path):
        ns = render_ns(tmp_path, entrypoint="adapter.py")
        ok_code = b"import sys\n"
        bad_on_rollback = b"import sys\nsys.exit(7 if sys.argv[1] == 'rollback' else 0)\n"
        make_bundle(tmp_path / "b", V1, files={"adapter.py": bad_on_rollback})
        make_bundle(tmp_path / "b", V2, files={"adapter.py": ok_code})
        use_bundles(ns, tmp_path / "b")
        install(ns, version=V1)
        update(ns, version=V2)
        with _exits(1):
            rollback(ns)
        assert _state(ns)["current"]["tag"] == V2

    def test_m49_two_top_level_directories_refused(self, tmp_path):
        files = {"VERSION": b"2"}
        entries = _std(V2, files, extra=[("other/VERSION", "file", b"2", 0o644)])
        _unchanged_after_failure(tmp_path, V2, entries)

    def test_m59_current_pointing_at_an_incomplete_release_is_refused(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        root = root_of(ns)
        half = root / "releases" / "demo-v9.9.9-halfway"
        shutil.copytree((root / "current").resolve(), half, symlinks=True)
        (half / ".complete").unlink()  # a valid release.json, but never completed
        (half / ".incomplete").write_text("")
        meta = json.loads((half / "release.json").read_text())
        meta.update(name=half.name, tag="demo-v9.9.9")  # not the version asked for below
        (half / "release.json").write_text(json.dumps(meta))
        (root / "current.new").symlink_to(half)
        os.replace(root / "current.new", root / "current")
        with _exits(1):
            install(ns, version=V1)
        assert os.readlink(root / "current").endswith("demo-v9.9.9-halfway")


# ─── Nits ────────────────────────────────────────────────────────────────────

class TestNits:
    @needs_minisign
    def test_minisign_trusted_comment_must_name_this_project(self, tmp_path):
        pub, sec = minisign_keypair(tmp_path / "keys")
        ns = render_ns(tmp_path, manifest_pubkey=pub)
        _a, original = make_bundle(tmp_path / "o", V1, files={"VERSION": b"1"})
        sig = minisign_sign(sec, original,
                            f"project=other tag={V1} manifest_sha256={sha(original)}",
                            tmp_path / "s")
        make_bundle(tmp_path / "b", V1, files={"VERSION": b"1"}, signature=sig)
        use_bundles(ns, tmp_path / "b")
        with _exits(1):
            install(ns, version=V1)
        assert snapshot(root_of(ns)) == {}

    @pytest.mark.parametrize("name", [
        "demotool-1.0 ;evil-py3-none-any.whl", "demotool-1.0\n-py3-none-any.whl",
        "demotool--py3-none-any.whl", "demotool-@x-py3-none-any.whl",
        "demotool--index-url-py3-none-any.whl"])
    def test_wheel_version_grammar_refused(self, name):
        ns = render_ns(Path("/nonexistent"))
        with _exits(1):
            ns["_wheel_version"](Path(name))

    def test_wheel_version_accepts_pep440_forms(self):
        ns = render_ns(Path("/nonexistent"))
        for ver in ("1.0.0", "2!1.0.0rc1", "1.0.0+local.1", "1.0.0.post2"):
            assert ns["_wheel_version"](Path(f"demotool-{ver}-py3-none-any.whl")) == ver

    @pytest.mark.parametrize("bad", [["."], ["a/.."], ["./"], ["x", "."]])
    def test_preserve_that_names_the_root_is_refused(self, bad):
        with pytest.raises(RenderError, match="preserve"):
            render_get_py(project_name="demo", repo_owner="o", repo_name="r",
                          tag_prefix="demo-v", install_dir_system="/opt/demo",
                          install_dir_user="demo", preserve_paths=bad)

    @pytest.mark.parametrize("path", ["/", "/usr", "/etc", "/bin", "/sbin", "/lib", "/var",
                                      "/boot", "/home", "/usr/", "//usr", "/./etc"])
    def test_system_install_dir_that_is_a_system_directory_refused(self, path):
        with pytest.raises(RenderError, match="install_dir_system"):
            render_get_py(project_name="demo", repo_owner="o", repo_name="r",
                          tag_prefix="demo-v", install_dir_system=path,
                          install_dir_user="demo")

    def test_system_install_dir_below_a_system_directory_is_fine(self):
        render_get_py(project_name="demo", repo_owner="o", repo_name="r",
                      tag_prefix="demo-v", install_dir_system="/usr/local/demo",
                      install_dir_user="demo")

    @pytest.mark.parametrize("leaf", [".", "a/..", "./"])
    def test_user_install_dir_that_is_the_data_dir_refused(self, leaf):
        with pytest.raises(RenderError, match="install_dir_user"):
            render_get_py(project_name="demo", repo_owner="o", repo_name="r",
                          tag_prefix="demo-v", install_dir_system="/opt/demo",
                          install_dir_user=leaf)

    def test_signal_handler_never_removes_a_staging_dir_that_became_current(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        live = (root_of(ns) / "current").resolve()
        ns["_active_staging"] = live
        with pytest.raises(SystemExit):
            ns["_cleanup_on_signal"](15, None)
        assert (live / ".complete").exists() and (live / "tree" / "VERSION").exists()

    def test_signal_handler_removes_a_genuine_half_built_staging_dir(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        half = root_of(ns) / "releases" / "demo-v2.0.0-half"
        half.mkdir()
        (half / ".incomplete").write_text("")
        ns["_active_staging"] = half
        with pytest.raises(SystemExit):
            ns["_cleanup_on_signal"](15, None)
        assert not half.exists()
        assert (root_of(ns) / "current").resolve().exists()

    def test_extract_size_limit_refused_before_anything_is_written(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        install(ns, version=V1)
        before = snapshot(root_of(ns))
        ns["_MAX_EXTRACT_BYTES"] = 5
        make_bundle(bundles, V2, files={"VERSION": b"0123456789"})
        with _exits(1):
            update(ns, version=V2)
        assert snapshot(root_of(ns)) == before

    def test_member_count_limit_refused(self, tmp_path):
        ns, bundles = _plain_setup(tmp_path)
        install(ns, version=V1)
        before = snapshot(root_of(ns))
        ns["_MAX_MEMBERS"] = 2
        make_bundle(bundles, V2, files={"VERSION": b"2", "a": b"a", "b": b"b"})
        with _exits(1):
            update(ns, version=V2)
        assert snapshot(root_of(ns)) == before

    def test_download_size_limit_refused_and_partial_file_removed(self, tmp_path):
        ns = render_ns(tmp_path)

        class Resp:
            status = 200
            def __init__(self):
                self.left = 10
            def read(self, n):
                chunk, self.left = b"x" * min(n, self.left), max(0, self.left - n)
                return chunk
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class Opener:
            def open(self, req, timeout=None):
                return Resp()

        dest = tmp_path / "dl"
        with mock.patch.object(ns["urllib"].request, "build_opener", lambda *a: Opener()):
            with _exits(1):
                ns["_gh_request"]("https://github.com/o/r/x", stream_to=dest, max_bytes=4)
        assert not dest.exists()

    def test_the_limits_are_documented(self):
        spec = (REPO / "cmru" / "docs" / "SPEC.md")
        text = spec.read_text(encoding="utf-8") if spec.exists() else ""
        assert "512 MiB" in text and "2 GiB" in text and "50,000" in text

    def test_status_never_writes_state(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        # fresh install that died between the swap and the state write
        (root_of(ns) / "state.json").unlink()
        before = snapshot(root_of(ns))
        ns["do_status"](args())
        assert snapshot(root_of(ns)) == before and not (root_of(ns) / "state.json").exists()

    def test_status_does_not_repair_a_state_that_is_behind_current(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        update(ns, version=V2)
        root = root_of(ns)
        stale = json.loads((root / "state.json").read_text())
        stale["current"] = stale["previous"]
        (root / "state.json").write_text(json.dumps(stale))
        before = snapshot(root)
        ns["do_status"](args())
        assert snapshot(root) == before

    def test_install_heals_a_missing_state(self, tmp_path):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        (root_of(ns) / "state.json").unlink()
        install(ns, version=V1)
        assert _state(ns)["current"]["tag"] == V1

    def test_unsigned_notice_says_the_sidecar_shares_the_origin(self, tmp_path, capsys):
        ns, _ = _plain_setup(tmp_path)
        install(ns, version=V1)
        out = capsys.readouterr().out + capsys.readouterr().err
        assert "same origin" in out and "NOT a compromised publisher" in out


# ─── Blocker 7: the authoring guide ──────────────────────────────────────────

class TestConsumersGuide:
    def _section(self):
        text = (REPO / "cmru" / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
        return text

    def test_chained_verified_form_leads_and_pipe_form_is_labelled_unverified(self):
        text = self._section()
        chained = ("d=$(mktemp -d) && cd \"$d\" && curl -fsSLo get.py --proto '=https'")
        assert chained in text and "sha256sum -c -" in text
        assert "sudo python3 get.py install --version" in text
        assert text.index(chained) < text.index("| sudo python3 - install")
        assert "unverified" in text.lower() and "exits 0" in text

    def test_no_claim_of_a_published_get_py_checksum(self):
        text = self._section()
        assert "published next to the release" not in text
