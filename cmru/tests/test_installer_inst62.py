"""CMRU-INST62: the cmru 6.2 installer features.

1. ``cmru installer render|check`` (+ the deprecated ``get-py`` alias);
2. ``stable_dir``: the active release's file set synced into ``<root>/<stable_dir>/``;
3. ``stub_launchers``: shell stubs inside the stable dir;
4. the installer's self-copy to ``<root>/bin/<project>-installer``;
5. ``[[project.installer.questions]]``: the install/update wizard and its answers file.

The installer tests run the RENDERED get.py (exec'd into a namespace) against fixture
bundles, as the other installer tests do: no network, no real root.
"""
from __future__ import annotations

import io
import json
import os
import stat
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from cmru import cli, config as cmru_config, getpy
from cmru.config import question_problems

from tests.installer_fakes import (
    install, make_bundle, make_wheel, render_ns, rollback, root_of, sha, snapshot, update,
    use_bundles,
)
from tests.test_installer import _minimal_toml, _write

V1, V2, V3 = "demo-v1.0.0", "demo-v2.0.0", "demo-v3.0.0"


def _exits(code):
    return pytest.raises(SystemExit, match=f"^{code}$")


def _bundle(tmp_path, tag, files, *, modes=None, **kw):
    """A bundle whose manifest `files` carry an explicit `mode` per file (the real
    manifest builder records one); `modes` maps relpath -> permission bits."""
    modes = modes or {}
    listing = {
        rel: {"sha256": sha(data), "size": len(data), "mode": modes.get(rel, 0o644)}
        for rel, data in files.items()
    }
    return make_bundle(tmp_path / "b", tag, files=files, manifest_extra={"files": listing},
                       modes=modes, **kw)


def _stack(tmp_path, **kw):
    """A no-wheel project with a stable dir `stack`; v1/v2/v3 bundles are served."""
    ns = render_ns(tmp_path, stable_dir="stack", **kw)
    use_bundles(ns, tmp_path / "b")
    return ns


def _files(tag, **extra):
    base = {"README": f"readme {tag}\n".encode(), "conf/app.toml": f"app = '{tag}'\n".encode(),
            "run.sh": b"#!/bin/sh\necho hi\n"}
    base.update({k: v for k, v in extra.items()})
    return base


def _stable(ns) -> Path:
    return root_of(ns) / "stack"


def _record(ns) -> dict:
    return json.loads((_stable(ns) / ".cmru-installer-sync.json").read_text())


def _tree(path: Path) -> dict:
    out = {}
    for p in sorted(path.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[str(p.relative_to(path))] = p.read_bytes()
    return out


# ─── 1. CLI: installer render / check / deprecated get-py ─────────────────────

_INSTALLER = """
[project.installer]
install_dir_system = "/opt/demo"
install_dir_user   = "demo"
"""


class TestInstallerCli:
    def test_render_is_byte_identical_to_the_deprecated_alias(self, tmp_path, capsys):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER))
        new, old = tmp_path / "new.py", tmp_path / "old.py"
        assert getpy.installer_main(["render", "demo", "--config", str(cfg),
                                     "--output", str(new)]) == 0
        assert capsys.readouterr().err == ""
        assert getpy.getpy_main(["demo", "--config", str(cfg), "--output", str(old)]) == 0
        assert new.read_bytes() == old.read_bytes()
        assert new.stat().st_mode & 0o111

    def test_the_alias_prints_exactly_one_deprecation_warning_on_stderr(self, tmp_path, capsys):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER))
        assert getpy.getpy_main(["demo", "--config", str(cfg),
                                 "--output", str(tmp_path / "o.py")]) == 0
        captured = capsys.readouterr()
        warning = ("[WARN] 'cmru get-py' is deprecated; use 'cmru installer render' "
                   "(removal in a later major release)")
        assert captured.err.splitlines().count(warning) == 1
        assert captured.err.count("deprecated") == 1
        assert "deprecated" not in captured.out

    def test_the_root_cli_routes_both_verbs(self, tmp_path, capsys):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER))
        assert cli.main(["installer", "render", "demo", "--config", str(cfg)]) == 0
        rendered = capsys.readouterr().out
        assert rendered.startswith("#!/usr/bin/env python3")
        assert cli.main(["get-py", "demo", "--config", str(cfg)]) == 0
        captured = capsys.readouterr()
        assert captured.out == rendered and "deprecated" in captured.err

    def test_check_passes_on_identical_bytes(self, tmp_path, capsys):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER))
        committed = tmp_path / "get.py"
        getpy.installer_main(["render", "demo", "--config", str(cfg), "--output", str(committed)])
        capsys.readouterr()
        assert getpy.installer_main(["check", "demo", "--config", str(cfg),
                                     "--installer", str(committed)]) == 0
        assert "identical" in capsys.readouterr().out

    def test_check_names_the_file_and_caps_the_diff_at_forty_lines(self, tmp_path, capsys):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER))
        committed = tmp_path / "get.py"
        getpy.installer_main(["render", "demo", "--config", str(cfg), "--output", str(committed)])
        committed.write_text("\n".join(f"junk {i}" for i in range(200)) + "\n")
        capsys.readouterr()
        assert getpy.installer_main(["check", "demo", "--config", str(cfg),
                                     "--installer", str(committed)]) == 1
        lines = capsys.readouterr().err.splitlines()
        assert lines[0].startswith("[ERROR]") and str(committed) in lines[0]
        assert "first 40 of" in lines[0]
        assert len(lines) - 1 == 40  # the diff, capped

    def test_check_with_a_small_diff_shows_all_of_it(self, tmp_path, capsys):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER))
        committed = tmp_path / "get.py"
        getpy.installer_main(["render", "demo", "--config", str(cfg), "--output", str(committed)])
        committed.write_text(committed.read_text().replace("Linux", "Plan9", 1))
        capsys.readouterr()
        assert getpy.installer_main(["check", "demo", "--config", str(cfg),
                                     "--installer", str(committed)]) == 1
        err = capsys.readouterr().err
        assert "first 40" not in err and "-" in err and "+" in err

    def test_check_missing_file_and_refusals(self, tmp_path, capsys):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER))
        missing = tmp_path / "nope.py"
        assert getpy.installer_main(["check", "demo", "--config", str(cfg),
                                     "--installer", str(missing)]) == 1
        assert f"[ERROR] cannot read installer {missing}" in capsys.readouterr().err
        # --installer is required
        assert getpy.installer_main(["check", "demo", "--config", str(cfg)]) == 2
        capsys.readouterr()
        # a project without an installer section is a render error (exit 2)
        (tmp_path / "bare").mkdir()
        bare = _write(tmp_path / "bare", _minimal_toml(""))
        assert getpy.installer_main(["check", "demo", "--config", str(bare),
                                     "--installer", str(missing)]) == 2
        assert "no [project.demo.installer] section" in capsys.readouterr().err

    def test_check_refuses_more_than_one_project(self, monkeypatch, tmp_path, capsys):
        config = tmp_path / "cmru.orchestration.toml"
        names = ("alpha", "beta")
        monkeypatch.setattr(cli, "_resolve_config", lambda _p: config)
        monkeypatch.setattr(cli, "load_config",
                            lambda _p: (tmp_path, {n: object() for n in names}, list(names)))
        assert getpy.installer_main(["check", "all", "--installer", "x.py"]) == 2
        assert "compares one project" in capsys.readouterr().err


# ─── 2. stable_dir ────────────────────────────────────────────────────────────

class TestStableDir:
    def test_install_update_rollback_keep_the_stable_dir_in_step(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1, **{"old.txt": b"only v1\n", "d/gone.txt": b"x\n"}),
                modes={"run.sh": 0o755, "conf/app.toml": 0o600})
        _bundle(tmp_path, V2, _files(V2, **{"new.txt": b"only v2\n"}),
                modes={"run.sh": 0o755, "conf/app.toml": 0o600})
        install(ns, version=V1)
        stable = _stable(ns)
        assert _tree(stable)["README"] == f"readme {V1}\n".encode()
        assert stat.S_IMODE((stable / "run.sh").stat().st_mode) == 0o755
        assert stat.S_IMODE((stable / "conf" / "app.toml").stat().st_mode) == 0o600
        record = _record(ns)
        assert record["schema_version"] == 1 and record["tag"] == V1
        assert record["files"]["README"] == {"sha256": sha(f"readme {V1}\n".encode()),
                                             "mode": 0o644}
        # runtime state the installer never listed
        (stable / ".ciu").mkdir()
        (stable / ".ciu" / "secret").write_text("keep")
        (stable / "vol-data").mkdir()
        (stable / "vol-data" / "db").write_text("keep")
        (stable / "d" / "keep.txt").write_text("user file")  # stops d/ from being removed
        update(ns, version=V2)
        tree = _tree(stable)
        assert tree["README"] == f"readme {V2}\n".encode()
        assert "old.txt" not in tree and "d/gone.txt" not in tree and "new.txt" in tree
        assert (stable / "d" / "keep.txt").read_text() == "user file"
        assert (stable / ".ciu" / "secret").read_text() == "keep"
        assert (stable / "vol-data" / "db").read_text() == "keep"
        assert _record(ns)["tag"] == V2
        rollback(ns)
        tree = _tree(stable)
        assert tree["README"] == f"readme {V1}\n".encode()
        assert "new.txt" not in tree and "old.txt" in tree and "d/gone.txt" in tree
        assert _record(ns)["tag"] == V1
        assert (stable / ".ciu" / "secret").read_text() == "keep"

    def test_a_directory_left_empty_by_a_deletion_is_removed(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a/b/c.txt": b"c\n", "keep": b"k\n"})
        _bundle(tmp_path, V2, {"keep": b"k\n"})
        install(ns, version=V1)
        assert (_stable(ns) / "a" / "b" / "c.txt").exists()
        update(ns, version=V2)
        assert not (_stable(ns) / "a").exists()
        assert (_stable(ns) / "keep").exists()

    def test_wheels_and_preserved_paths_are_not_synced(self, tmp_path):
        ns = _stack(tmp_path, preserve_paths=["conf/app.toml"],
                    wheel_specs=[("vendor/demotool-*.whl", "demotool")])
        wheel = make_wheel(tmp_path / "w", "demotool", "1.0.0", console="demotool")
        make_bundle(tmp_path / "b", V1, files={"README": b"r\n", "conf/app.toml": b"c\n"},
                    wheels=[("demotool", wheel)])
        install(ns, version=V1)
        assert sorted(_tree(_stable(ns))) == [".cmru-installer-sync.json", "README"]

    # -- the local-modification guard ---------------------------------------

    def test_a_modified_file_refuses_and_leaves_current_and_the_tree_alone(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        _bundle(tmp_path, V2, _files(V2))
        install(ns, version=V1)
        stable = _stable(ns)
        (stable / "README").write_text("edited by the operator\n")
        before_tree, before_root = _tree(stable), snapshot(root_of(ns))
        with _exits(2):
            update(ns, version=V2)
        assert _tree(stable) == before_tree  # no partial sync
        assert snapshot(root_of(ns)) == before_root  # current, state, releases untouched
        assert (root_of(ns) / "current").resolve().name.startswith(V1)

    def test_the_refusal_lists_at_most_twenty_paths(self, tmp_path, capsys):
        ns = _stack(tmp_path)
        many = {f"f{i:02d}": f"v1 {i}\n".encode() for i in range(25)}
        _bundle(tmp_path, V1, many)
        _bundle(tmp_path, V2, {k: v + b"v2\n" for k, v in many.items()})
        install(ns, version=V1)
        for name in many:
            (_stable(ns) / name).write_text("mine\n")
        capsys.readouterr()
        with _exits(2):
            update(ns, version=V2)
        err = capsys.readouterr().err
        assert "25 file(s)" in err and "were modified locally" in err
        assert err.count("\n  - stack/f") == 20 and "and 5 more" in err
        assert "--overwrite-local" in err

    def test_overwrite_local_saves_the_original_then_replaces_it(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        _bundle(tmp_path, V2, {k: v for k, v in _files(V2).items() if k != "run.sh"})
        install(ns, version=V1)
        stable = _stable(ns)
        (stable / "conf" / "app.toml").write_text("operator edit\n")
        (stable / "run.sh").write_text("operator script\n")  # will be deleted by v2
        update(ns, version=V2, overwrite_local=True)
        assert (stable / "conf" / "app.toml").read_text() == "app = 'demo-v2.0.0'\n"
        assert not (stable / "run.sh").exists()
        (stamp,) = (root_of(ns) / "shared" / "overwritten").iterdir()
        assert len(stamp.name) == 16 and stamp.name.endswith("Z")
        assert (stamp / "conf" / "app.toml").read_text() == "operator edit\n"
        assert (stamp / "run.sh").read_text() == "operator script\n"

    def test_a_first_sync_refuses_a_pre_existing_file_with_other_content(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        (_stable(ns) / "conf").mkdir(parents=True)
        (_stable(ns) / "conf" / "app.toml").write_text("someone's file\n")
        with _exits(2):
            install(ns, version=V1)
        assert not (root_of(ns) / "current").exists()  # nothing was installed
        assert not (root_of(ns) / "state.json").exists()
        assert not (root_of(ns) / "releases").exists()
        assert (_stable(ns) / "conf" / "app.toml").read_text() == "someone's file\n"
        install(ns, version=V1, overwrite_local=True)
        assert (_stable(ns) / "conf" / "app.toml").read_text() == f"app = '{V1}'\n"
        assert (root_of(ns) / "shared" / "overwritten").is_dir()

    def test_a_pre_existing_file_with_identical_content_is_adopted(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        (_stable(ns) / "conf").mkdir(parents=True)
        (_stable(ns) / "conf" / "app.toml").write_text(f"app = '{V1}'\n")
        install(ns, version=V1)
        assert _record(ns)["tag"] == V1

    def test_a_non_regular_target_and_a_symlinked_parent_are_refused(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        (_stable(ns) / "README").mkdir(parents=True)  # a directory where a file belongs
        with _exits(2):
            install(ns, version=V1)
        (_stable(ns) / "README").rmdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (_stable(ns) / "conf").symlink_to(outside)
        with _exits(2):
            install(ns, version=V1)
        assert list(outside.iterdir()) == []

    def test_a_malformed_record_is_refused(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        _bundle(tmp_path, V2, _files(V2))
        install(ns, version=V1)
        for bad in ("not json", json.dumps({"schema_version": 2, "tag": "x", "files": {}}),
                    json.dumps({"schema_version": 1, "tag": "x", "files": {"../x": {"sha256": "a"}}})):
            (_stable(ns) / ".cmru-installer-sync.json").write_text(bad)
            with _exits(1):
                update(ns, version=V2)

    def test_the_bundle_may_not_ship_the_record_name(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {".cmru-installer-sync.json": b"{}"})
        with _exits(1):
            install(ns, version=V1)

    # -- rollback on a failed sync ------------------------------------------

    @staticmethod
    def _fail_replace_onto(suffix, times=1):
        """`os.replace` onto a path ending in `suffix` fails `times` times (None: always)."""
        real = os.replace
        left = [times]

        def replace(src, dst, *a, **k):
            if str(dst).endswith(suffix) and (left[0] is None or left[0] > 0):
                if left[0] is not None:
                    left[0] -= 1
                raise OSError(28, "No space left on device")
            return real(src, dst, *a, **k)
        return mock.patch("os.replace", replace)

    def test_an_undo_that_itself_fails_still_restores_the_rest_and_says_so(
            self, tmp_path, capsys):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a.txt": b"a1\n", "b.txt": b"b1\n"})
        _bundle(tmp_path, V2, {"a.txt": b"a2\n", "b.txt": b"b2\n"})
        install(ns, version=V1)
        capsys.readouterr()
        with self._fail_replace_onto("stack/b.txt", times=None), pytest.raises(OSError):
            update(ns, version=V2)
        assert (_stable(ns) / "a.txt").read_bytes() == b"a1\n"  # restored despite b.txt
        assert "Could not fully undo the stable-dir sync" in capsys.readouterr().err
        assert (root_of(ns) / "current").resolve().name.startswith(V1)

    def test_a_failed_sync_restores_current_and_every_file(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a.txt": b"a1\n", "b.txt": b"b1\n", "gone.txt": b"g\n"})
        _bundle(tmp_path, V2, {"a.txt": b"a2\n", "b.txt": b"b2\n", "new.txt": b"n\n"})
        install(ns, version=V1)
        stable = _stable(ns)
        before = _tree(stable)
        with self._fail_replace_onto("stack/b.txt"), pytest.raises(OSError):
            update(ns, version=V2)
        assert _tree(stable) == before  # a.txt (already swapped) came back; no new.txt
        assert not [p for p in stable.rglob("*")
                    if p.name.endswith((".cmru-old", ".cmru-new"))]  # no leftovers
        assert (root_of(ns) / "current").resolve().name.startswith(V1)
        assert json.loads((root_of(ns) / "state.json").read_text())["current"]["tag"] == V1
        assert _record(ns)["tag"] == V1
        update(ns, version=V2)  # and the transaction is retryable
        assert _tree(stable)["b.txt"] == b"b2\n"

    def test_a_failed_deletion_step_undoes_the_earlier_writes(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a.txt": b"a1\n", "gone.txt": b"g\n"})
        _bundle(tmp_path, V2, {"a.txt": b"a2\n"})
        install(ns, version=V1)
        before = _tree(_stable(ns))
        with self._fail_replace_onto(".gone.txt.cmru-old"), pytest.raises(OSError):
            update(ns, version=V2)
        assert _tree(_stable(ns)) == before
        assert (root_of(ns) / "current").resolve().name.startswith(V1)

    def test_a_failure_while_staging_changes_nothing(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a.txt": b"a1\n", "b.txt": b"b1\n"})
        _bundle(tmp_path, V2, {"a.txt": b"a2\n", "b.txt": b"b2\n"})
        install(ns, version=V1)
        before = _tree(_stable(ns))
        real = ns["_stage_one"]
        calls = []

        def flaky(tmp, item):
            calls.append(tmp.name)
            if len(calls) == 2:
                raise OSError(5, "I/O error")
            real(tmp, item)
        ns["_stage_one"] = flaky
        with pytest.raises(OSError):
            update(ns, version=V2)
        assert _tree(_stable(ns)) == before
        assert (root_of(ns) / "current").resolve().name.startswith(V1)

    def test_a_failed_first_sync_leaves_no_install(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a.txt": b"a1\n", "sub/b.txt": b"b1\n"})
        with self._fail_replace_onto("stack/sub/b.txt"), pytest.raises(OSError):
            install(ns, version=V1)
        root = root_of(ns)
        assert not (root / "current").exists() and not (root / "current").is_symlink()
        assert not (root / "state.json").exists() and not (root / "releases").exists()
        assert not _stable(ns).exists()

    def test_a_failed_rollback_sync_keeps_the_newer_release_current(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a.txt": b"a1\n"})
        _bundle(tmp_path, V2, {"a.txt": b"a2\n"})
        install(ns, version=V1)
        update(ns, version=V2)
        with self._fail_replace_onto("stack/a.txt"), pytest.raises(OSError):
            rollback(ns)
        assert (root_of(ns) / "current").resolve().name.startswith(V2)
        assert (_stable(ns) / "a.txt").read_bytes() == b"a2\n"

    def test_a_modified_file_refuses_the_rollback_before_anything_moves(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a.txt": b"a1\n"})
        _bundle(tmp_path, V2, {"a.txt": b"a2\n"})
        install(ns, version=V1)
        update(ns, version=V2)
        (_stable(ns) / "a.txt").write_text("mine\n")
        before = snapshot(root_of(ns))
        with _exits(2):
            rollback(ns)
        assert snapshot(root_of(ns)) == before
        rollback(ns, overwrite_local=True)
        assert (_stable(ns) / "a.txt").read_bytes() == b"a1\n"

    # -- already at the tag ---------------------------------------------------

    def test_an_update_with_nothing_new_ignores_local_edits_but_heals_a_missing_record(
            self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        install(ns, version=V1)
        (_stable(ns) / "README").write_text("edit\n")
        update(ns, version=V1)  # same tag, record in step: nothing to do, no refusal
        assert (_stable(ns) / "README").read_text() == "edit\n"
        (_stable(ns) / "README").write_text(f"readme {V1}\n")
        (_stable(ns) / ".cmru-installer-sync.json").unlink()
        update(ns, version=V1)  # no record: re-sync (the files already match)
        assert _record(ns)["tag"] == V1

    def test_a_record_at_another_tag_is_resynced_with_the_guard(self, tmp_path):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, {"a.txt": b"a1\n"})
        install(ns, version=V1)
        rec = _record(ns)
        rec["tag"] = "demo-v0.0.1"
        (_stable(ns) / ".cmru-installer-sync.json").write_text(json.dumps(rec))
        (_stable(ns) / "a.txt").write_text("mine\n")
        with _exits(2):
            update(ns, version=V1)
        (_stable(ns) / "a.txt").write_bytes(b"a1\n")
        update(ns, version=V1)
        assert _record(ns)["tag"] == V1

    # -- status ---------------------------------------------------------------

    def test_status_reports_the_stable_dir_tag_and_modified_count(self, tmp_path, capsys):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        capsys.readouterr()
        install(ns, version=V1)
        capsys.readouterr()
        ns["do_status"](mock.Mock(scope="system"))
        out = capsys.readouterr().out
        assert f"stable dir: {_stable(ns)}" in out
        assert f"synced tag: {V1}" in out and "locally modified files: 0" in out
        (_stable(ns) / "README").write_text("x\n")
        (_stable(ns) / "run.sh").unlink()
        ns["do_status"](mock.Mock(scope="system"))
        assert "locally modified files: 2" in capsys.readouterr().out

    def test_status_without_a_record_says_never_synced(self, tmp_path, capsys):
        ns = _stack(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        install(ns, version=V1)
        (_stable(ns) / ".cmru-installer-sync.json").unlink()
        capsys.readouterr()
        ns["do_status"](mock.Mock(scope="system"))
        assert "synced tag: (never synced)" in capsys.readouterr().out

    def test_without_a_stable_dir_nothing_is_created_and_status_is_silent(
            self, tmp_path, capsys):
        ns = render_ns(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        use_bundles(ns, tmp_path / "b")
        install(ns, version=V1)
        assert not _stable(ns).exists()
        capsys.readouterr()
        ns["do_status"](mock.Mock(scope="system"))
        assert "stable dir" not in capsys.readouterr().out


class TestStableDirConfig:
    @pytest.mark.parametrize("value", ["a/b", "/abs", "..", ".hidden", "", "x y", "releases",
                                       "shared", "bin", "current", "state.json", "venv"])
    def test_a_bad_stable_dir_is_a_render_error(self, tmp_path, value):
        if value == "":
            ns = render_ns(tmp_path, stable_dir=value)  # "" = disabled, not an error
            assert ns["STABLE_DIR"] == ""
            return
        with pytest.raises(getpy.RenderError, match="stable_dir"):
            render_ns(tmp_path, stable_dir=value)

    def test_stub_launchers_need_a_stable_dir_and_a_source(self, tmp_path):
        with pytest.raises(getpy.RenderError, match="need stable_dir"):
            render_ns(tmp_path, stub_launchers=["ciu"], launchers=["ciu"])
        with pytest.raises(getpy.RenderError, match="neither in launchers"):
            render_ns(tmp_path, stable_dir="stack", stub_launchers=["ciu"])
        with pytest.raises(getpy.RenderError, match="plain command name"):
            render_ns(tmp_path, stable_dir="stack", stub_launchers=["a b"])
        with pytest.raises(getpy.RenderError, match="listed twice"):
            render_ns(tmp_path, stable_dir="stack", stub_launchers=["ciu", "ciu"],
                      launchers=["ciu"])
        # a console script of a configured wheel is allowed (checked at install time)
        ns = render_ns(tmp_path, stable_dir="stack", stub_launchers=["ciu"],
                       wheel_specs=[("vendor/ciu-*.whl", "ciu")])
        assert ns["STUB_LAUNCHERS"] == ["ciu"]

    def test_the_toml_keys_parse_and_render(self, tmp_path):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER + """
stable_dir = "stack"
launchers = ["ciu"]
stub_launchers = ["ciu"]
[[project.installer.wheels]]
path = "vendor/ciu-*.whl"
distribution = "ciu"
"""))
        installer = cmru_config.load_forge_config(cfg).projects["demo"].installer
        assert installer.stable_dir == "stack" and installer.stub_launchers == ["ciu"]
        rendered = getpy.render_from_config("demo", cfg)
        assert 'STABLE_DIR = "stack"' in rendered

    @pytest.mark.parametrize("body, message", [
        ('stable_dir = 3', "stable_dir must be a string"),
        ('stub_launchers = "ciu"', "stub_launchers must be a list"),
        ('questions = ["x"]', "questions must be an array of tables"),
        ('stable_dir = "a/b"', "stable_dir must be one relative path component"),
    ])
    def test_toml_shape_errors(self, tmp_path, capsys, body, message):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER + body + "\n"))
        with _exits(2):
            cmru_config.load_forge_config(cfg)
        assert message in capsys.readouterr().err


# ─── 3. stub_launchers ────────────────────────────────────────────────────────

class TestStubLaunchers:
    def _project(self, tmp_path, **kw):
        ns = render_ns(tmp_path, stable_dir="stack", stub_launchers=["demotool"],
                       launchers=["demotool"],
                       wheel_specs=[("vendor/demotool-*.whl", "demotool")], **kw)
        use_bundles(ns, tmp_path / "b")
        return ns

    def _release(self, tmp_path, tag, version, files=None):
        wheel = make_wheel(tmp_path / "w" / tag, "demotool", version, console="demotool")
        make_bundle(tmp_path / "b", tag, files=files or {"README": b"r\n"},
                    wheels=[("demotool", wheel)])

    def test_the_stub_has_the_exact_content_mode_and_runs_the_pinned_tool(self, tmp_path):
        ns = self._project(tmp_path)
        self._release(tmp_path, V1, "1.0.0")
        self._release(tmp_path, V2, "2.0.0")
        install(ns, version=V1)
        stub = _stable(ns) / "demotool"
        root = root_of(ns)
        assert stub.read_text() == (
            "#!/bin/sh\n"
            f"# cmru installer stub: runs the demotool pinned by this installation "
            f"({root}/current); do not edit\n"
            f'exec "{root}/current/venv/bin/demotool" "$@"\n')
        assert stat.S_IMODE(stub.stat().st_mode) == 0o755
        assert _record(ns)["files"]["demotool"]["stub"] is True
        assert "stub" not in _record(ns)["files"]["README"]
        env = {"PATH": os.environ["PATH"]}
        run = lambda: subprocess.run([str(stub)], capture_output=True, text=True, env=env)
        assert run().stdout.strip() == "demotool 1.0.0"
        update(ns, version=V2)  # the path stays; the pin moves with `current`
        assert run().stdout.strip() == "demotool 2.0.0"
        rollback(ns)
        assert run().stdout.strip() == "demotool 1.0.0"

    def test_a_locally_edited_stub_is_guarded_like_any_other_file(self, tmp_path):
        ns = self._project(tmp_path)
        self._release(tmp_path, V1, "1.0.0")
        self._release(tmp_path, V2, "2.0.0")
        install(ns, version=V1)
        (_stable(ns) / "demotool").write_text("#!/bin/sh\necho hijacked\n")
        before = snapshot(root_of(ns))
        with _exits(2):
            update(ns, version=V2)
        assert snapshot(root_of(ns)) == before
        update(ns, version=V2, overwrite_local=True)
        assert "exec" in (_stable(ns) / "demotool").read_text()

    def test_removing_a_stub_from_the_config_deletes_it(self, tmp_path):
        ns = self._project(tmp_path)
        self._release(tmp_path, V1, "1.0.0")
        install(ns, version=V1)
        assert (_stable(ns) / "demotool").exists()
        slim = render_ns(tmp_path, stable_dir="stack", launchers=["demotool"],
                         wheel_specs=[("vendor/demotool-*.whl", "demotool")])
        use_bundles(slim, tmp_path / "b")
        update(slim, version=V1)  # same tag: the re-rendered installer notices the stub set
        assert not (_stable(slim) / "demotool").exists()
        assert "demotool" not in _record(slim)["files"]
        assert (_stable(slim) / "README").exists()

    def test_a_stub_needs_its_console_script_in_the_release_venv(self, tmp_path):
        ns = render_ns(tmp_path, stable_dir="stack", stub_launchers=["ghost"],
                       wheel_specs=[("vendor/demotool-*.whl", "demotool")])
        use_bundles(ns, tmp_path / "b")
        self._release(tmp_path, V1, "1.0.0")
        with _exits(1):
            install(ns, version=V1)
        assert not (root_of(ns) / "current").exists()

    def test_a_stub_may_not_collide_with_a_bundle_file(self, tmp_path):
        ns = self._project(tmp_path)
        self._release(tmp_path, V1, "1.0.0", files={"demotool/readme": b"x\n"})
        with _exits(1):
            install(ns, version=V1)

    def test_the_stub_quotes_a_hostile_root(self, tmp_path):
        ns = self._project(tmp_path)
        data = ns["_stub_bytes"](Path('/opt/we"ird$`x'), "demotool").decode()
        assert 'exec "/opt/we\\"ird\\$\\`x/current/venv/bin/demotool" "$@"' in data
        with _exits(2):
            ns["_stub_bytes"](Path("/opt/a\nb"), "demotool")


# ─── 4. installer self-copy ───────────────────────────────────────────────────

class TestSelfCopy:
    def _setup(self, tmp_path):
        ns = render_ns(tmp_path)
        _bundle(tmp_path, V1, _files(V1))
        use_bundles(ns, tmp_path / "b")
        return ns

    def _installer_file(self, tmp_path, ns):
        path = tmp_path / "get.py"
        path.write_text(getpy.render_get_py(
            project_name="demo", repo_owner="o", repo_name="r", tag_prefix="demo-v",
            install_dir_system=ns["INSTALL_DIR_SYSTEM"], install_dir_user="demo"))
        return path

    def test_running_without_a_file_skips_the_copy_with_a_warning(self, tmp_path, capsys):
        ns = self._setup(tmp_path)
        assert "__file__" not in ns  # the `curl ... | python3 -` situation
        install(ns, version=V1)
        err = capsys.readouterr().err
        assert "Not keeping a copy of the installer" in err and "`python3 -`" in err
        assert "Download get.py" in err
        assert not (root_of(ns) / "bin" / "demo-installer").exists()

    def test_install_and_update_copy_the_verified_source(self, tmp_path):
        ns = self._setup(tmp_path)
        _bundle(tmp_path, V2, _files(V2))
        src = self._installer_file(tmp_path, ns)
        ns["__file__"] = str(src)
        install(ns, version=V1)
        copy = root_of(ns) / "bin" / "demo-installer"
        assert copy.read_bytes() == src.read_bytes()
        assert stat.S_IMODE(copy.stat().st_mode) == 0o755
        src.write_text(src.read_text() + "\n# newer\n")
        update(ns, version=V2)
        assert copy.read_bytes() == src.read_bytes()

    def test_running_the_copy_itself_is_a_quiet_no_op(self, tmp_path, capsys):
        ns = self._setup(tmp_path)
        ns["__file__"] = str(self._installer_file(tmp_path, ns))
        install(ns, version=V1)
        copy = root_of(ns) / "bin" / "demo-installer"
        ns["__file__"] = str(copy)
        capsys.readouterr()
        before = copy.read_bytes()
        install(ns, version=V1)
        assert copy.read_bytes() == before and "Not keeping" not in capsys.readouterr().err

    @pytest.mark.parametrize("content, reason", [
        ("def broken(:\n", "invalid syntax"),
        ("print('another project')\n", "not this project's installer"),
    ])
    def test_a_source_that_is_not_this_installer_is_not_copied(
            self, tmp_path, capsys, content, reason):
        ns = self._setup(tmp_path)
        bad = tmp_path / "other.py"
        bad.write_text(content)
        ns["__file__"] = str(bad)
        install(ns, version=V1)  # the install itself still succeeds
        err = capsys.readouterr().err
        assert "Not keeping a copy" in err and reason in err
        assert not (root_of(ns) / "bin" / "demo-installer").exists()
        assert (root_of(ns) / "current").is_symlink()

    def test_a_missing_source_file_is_only_a_warning(self, tmp_path, capsys):
        ns = self._setup(tmp_path)
        ns["__file__"] = str(tmp_path / "vanished.py")
        install(ns, version=V1)
        assert "Not keeping a copy" in capsys.readouterr().err


# ─── 5. wizard questions ──────────────────────────────────────────────────────

QUESTIONS = [
    {"id": "host", "prompt": "Public host name", "type": "string", "default": None,
     "choices": [], "required": True},
    {"id": "tls", "prompt": "Enable TLS", "type": "bool", "default": True,
     "choices": [], "required": True},
    {"id": "port", "prompt": "Port", "type": "int", "default": 8065,
     "choices": [], "required": False},
    {"id": "tier", "prompt": "Tier", "type": "choice", "default": None,
     "choices": ["small", "large"], "required": True},
    {"id": "note", "prompt": "Note", "type": "string", "default": None,
     "choices": [], "required": False},
]


class _Tty(io.StringIO):
    def isatty(self):
        return True


def _asks(tmp_path, questions=None, **kw):
    ns = render_ns(tmp_path, questions=QUESTIONS if questions is None else questions, **kw)
    _bundle(tmp_path, V1, {"README": b"r\n"})
    _bundle(tmp_path, V2, {"README": b"r2\n"})
    use_bundles(ns, tmp_path / "b")
    return ns


def _answers(ns) -> str:
    return (root_of(ns) / "shared" / "answers.toml").read_text()


class TestQuestionsConfig:
    def test_a_valid_set_has_no_problems(self):
        assert question_problems(QUESTIONS) == []

    @pytest.mark.parametrize("question, fragment", [
        ({"id": "Bad", "prompt": "p", "type": "string"}, "id must match"),
        ({"id": "x", "prompt": "p", "type": "string", "wat": 1}, "unknown keys"),
        ({"id": "x", "prompt": "", "type": "string"}, "prompt must be"),
        ({"id": "x", "prompt": "a\nb", "type": "string"}, "prompt must be"),
        ({"id": "x", "prompt": "p", "type": "float"}, "type must be one of"),
        ({"id": "x", "prompt": "p", "type": "choice"}, "choices must be"),
        ({"id": "x", "prompt": "p", "type": "choice", "choices": ["a", "a"]}, "choices must be"),
        ({"id": "x", "prompt": "p", "type": "choice", "choices": ["a", 1]}, "choices must be"),
        ({"id": "x", "prompt": "p", "type": "string", "choices": ["a"]}, "only valid for a choice"),
        ({"id": "x", "prompt": "p", "type": "string", "required": "yes"}, "required must be"),
        ({"id": "x", "prompt": "p", "type": "string", "default": 3}, "default does not fit"),
        ({"id": "x", "prompt": "p", "type": "bool", "default": "y"}, "default does not fit"),
        ({"id": "x", "prompt": "p", "type": "int", "default": True}, "default does not fit"),
        ({"id": "x", "prompt": "p", "type": "choice", "choices": ["a"], "default": "b"},
         "default does not fit"),
    ])
    def test_each_rule_is_enforced(self, question, fragment):
        problems = question_problems([question])
        assert problems and fragment in problems[0]

    def test_duplicate_ids_are_refused(self):
        q = {"id": "x", "prompt": "p", "type": "string"}
        assert "duplicate id" in question_problems([q, dict(q)])[0]

    def test_the_toml_table_parses_renders_and_executes(self, tmp_path):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER + """
[[project.installer.questions]]
id = "host"
prompt = "Host \\"name\\""
type = "string"
[[project.installer.questions]]
id = "tls"
prompt = "TLS"
type = "bool"
default = false
required = false
[[project.installer.questions]]
id = "tier"
prompt = "Tier"
type = "choice"
choices = ["small", "large"]
default = "small"
[[project.installer.questions]]
id = "port"
prompt = "Port"
type = "int"
default = 8065
"""))
        installer = cmru_config.load_forge_config(cfg).projects["demo"].installer
        assert [q.id for q in installer.questions] == ["host", "tls", "tier", "port"]
        ns: dict = {}
        exec(compile(getpy.render_from_config("demo", cfg), "<r>", "exec"), ns)
        by_id = {q["id"]: q for q in ns["QUESTIONS"]}
        assert by_id["host"]["prompt"] == 'Host "name"' and by_id["host"]["required"] is True
        assert by_id["tls"]["default"] is False and by_id["tls"]["required"] is False
        assert by_id["port"]["default"] == 8065 and by_id["tier"]["choices"] == ["small", "large"]

    def test_a_bad_toml_question_fails_loading(self, tmp_path, capsys):
        cfg = _write(tmp_path, _minimal_toml(_INSTALLER + """
[[project.installer.questions]]
id = "BAD"
prompt = "p"
type = "string"
"""))
        with _exits(2):
            cmru_config.load_forge_config(cfg)
        assert "id must match" in capsys.readouterr().err

    def test_a_placeholder_lookalike_in_a_prompt_is_inert(self, tmp_path):
        ns = render_ns(tmp_path, questions=[
            {"id": "x", "prompt": "[[PROJECT_NAME]] \\ \" '", "type": "string"}])
        assert ns["QUESTIONS"][0]["prompt"] == "[[PROJECT_NAME]] \\ \" '"


class TestWizardUnattended:
    def test_non_tty_without_unattended_refuses_before_any_change(self, tmp_path, capsys):
        ns = _asks(tmp_path)
        with mock.patch("sys.stdin", io.StringIO("")), _exits(2):
            install(ns, version=V1)
        err = capsys.readouterr().err
        assert "--unattended" in err and "no silent defaults" in err
        assert "host" in err and "tier" in err
        assert not root_of(ns).exists()  # refused before the lock created anything

    def test_an_update_that_has_every_answer_stored_needs_no_terminal(self, tmp_path):
        ns = _asks(tmp_path)
        answers = tmp_path / "a.toml"
        answers.write_text('host = "h"\ntier = "small"\n')
        install(ns, version=V1, unattended=True, answers=str(answers))
        with mock.patch("sys.stdin", io.StringIO("")):
            update(ns, version=V2)  # everything stored (tls/port have defaults only) ...
        # ... except the defaults-only ones, which were stored by the unattended run
        assert 'tls = true' in _answers(ns) and 'port = 8065' in _answers(ns)

    def test_unattended_takes_file_then_stored_then_defaults_and_stores_0600(self, tmp_path):
        ns = _asks(tmp_path)
        answers = tmp_path / "a.toml"
        answers.write_text(
            '# comment\nhost = "mm.example.org"  # trailing\ntls = false\n'
            "tier = 'large'\nnote = \"a \\\"q\\\" \\n tab\\t\"\nport = 1_000\n")
        install(ns, version=V1, unattended=True, answers=str(answers))
        path = root_of(ns) / "shared" / "answers.toml"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        stored = ns["_read_toml_file"](path)
        assert stored == {"host": "mm.example.org", "tls": False, "port": 1000,
                          "tier": "large", "note": 'a "q" \n tab\t'}
        assert os.environ["CMRU_INSTALLER_ANSWERS"] == str(path)
        # update: stored answers win over defaults; a file value overrides the stored one
        other = tmp_path / "b.toml"
        other.write_text('host = "new.example.org"\n')
        update(ns, version=V2, unattended=True, answers=str(other))
        stored = ns["_read_toml_file"](path)
        assert stored["host"] == "new.example.org" and stored["tls"] is False
        assert stored["tier"] == "large"

    def test_a_required_question_with_no_answer_lists_every_missing_id(self, tmp_path, capsys):
        ns = _asks(tmp_path)
        with _exits(2):
            install(ns, version=V1, unattended=True)
        err = capsys.readouterr().err
        assert "required question(s): host, tier" in err
        assert not root_of(ns).exists() or snapshot(root_of(ns)) == {}

    def test_unknown_and_invalid_ids_in_the_file_exit_config(self, tmp_path, capsys):
        ns = _asks(tmp_path)
        bad = tmp_path / "bad.toml"
        bad.write_text('host = "h"\ntier = "small"\nbogus = 1\nalso = 2\n')
        with _exits(2):
            install(ns, version=V1, unattended=True, answers=str(bad))
        assert "unknown question id(s): also, bogus" in capsys.readouterr().err
        bad.write_text('host = 5\ntier = "huge"\ntls = "yes"\nport = true\n')
        with _exits(2):
            install(ns, version=V1, unattended=True, answers=str(bad))
        err = capsys.readouterr().err
        assert "host: expected string" in err and "tier: expected choice (one of small, large)" in err
        assert "tls: expected bool" in err and "port: expected int" in err
        assert snapshot(root_of(ns)) == {} or not root_of(ns).exists()

    def test_answers_without_unattended_and_a_missing_file_are_refused(self, tmp_path, capsys):
        ns = _asks(tmp_path)
        with _exits(2):
            install(ns, version=V1, answers=str(tmp_path / "x.toml"))
        assert "--answers requires --unattended" in capsys.readouterr().err
        with _exits(2):
            install(ns, version=V1, unattended=True, answers=str(tmp_path / "missing.toml"))
        assert "not found" in capsys.readouterr().err

    @pytest.mark.parametrize("text, fragment", [
        ("[table]\n", "flat `key = value`"),
        ("a = [1, 2]\n", "only string, true/false and integer"),
        ("a = 1.5\n", "only string, true/false and integer"),
        ('a = "bad \\q"\n', "only string, true/false and integer"),
        ("a = 1\na = 2\n", "duplicate key"),
        ("no equals here\n", "flat `key = value`"),
    ])
    def test_the_minimal_toml_reader_refuses_what_it_cannot_read(self, tmp_path, text, fragment,
                                                                  capsys):
        ns = _asks(tmp_path)
        bad = tmp_path / "bad.toml"
        bad.write_text(text)
        with _exits(2):
            ns["_read_toml_file"](bad)
        assert fragment in capsys.readouterr().err

    def test_an_unreadable_file_is_exit_config(self, tmp_path):
        ns = _asks(tmp_path)
        binary = tmp_path / "bin.toml"
        binary.write_bytes(b"\xff\xfe\x00")
        with _exits(2):
            ns["_read_toml_file"](binary)

    def test_the_writer_and_reader_round_trip_awkward_strings(self, tmp_path):
        ns = _asks(tmp_path)
        tricky = 'quote " back \\ nl \n tab \t ctl \x01 del \x7f unicode ü'
        text = ns["_toml_dumps"]({"host": tricky, "tls": True, "port": -3, "note": ""})
        assert ns["_toml_loads"](text, "t") == {"host": tricky, "tls": True, "port": -3,
                                                "note": ""}
        # and it is real TOML
        import tomllib
        assert tomllib.loads(text) == {"host": tricky, "tls": True, "port": -3, "note": ""}

    def test_the_adapter_and_extensions_see_the_answers_path(self, tmp_path):
        seen = tmp_path / "seen.txt"
        adapter = (
            "import os, sys, pathlib\n"
            f"pathlib.Path({str(seen)!r}).write_text(os.environ['CMRU_INSTALLER_ANSWERS'])\n")
        ns = render_ns(tmp_path, questions=[QUESTIONS[0]], entrypoint="adapter.py")
        _bundle(tmp_path, V1, {"adapter.py": adapter.encode()})
        use_bundles(ns, tmp_path / "b")
        answers = tmp_path / "a.toml"
        answers.write_text('host = "h"\n')
        install(ns, version=V1, unattended=True, answers=str(answers))
        assert seen.read_text() == str(root_of(ns) / "shared" / "answers.toml")
        assert "answers_path" not in ns["EXTENSION_API"]  # the env var is the interface

    def test_without_questions_the_flags_are_inert_and_no_file_is_written(self, tmp_path):
        ns = render_ns(tmp_path)
        _bundle(tmp_path, V1, {"README": b"r\n"})
        use_bundles(ns, tmp_path / "b")
        install(ns, version=V1, unattended=True)
        assert not (root_of(ns) / "shared" / "answers.toml").exists()
        other = tmp_path / "x.toml"
        other.write_text("x = 1\n")
        with _exits(2):
            install(ns, version=V1, unattended=True, answers=str(other))  # unknown id

    def test_update_without_an_install_refuses_before_asking(self, tmp_path, capsys):
        ns = _asks(tmp_path)
        with mock.patch("builtins.input", side_effect=AssertionError("asked")), _exits(1):
            update(ns, version=V1)
        assert "No install found" in capsys.readouterr().err


class TestWizardInteractive:
    def _run(self, ns, replies, **kw):
        with mock.patch("sys.stdin", _Tty()), \
                mock.patch("builtins.input", side_effect=list(replies)) as asked:
            install(ns, version=V1, **kw)
        return asked

    def test_prompts_show_defaults_validate_and_reprompt(self, tmp_path, capsys):
        ns = _asks(tmp_path)
        asked = self._run(ns, [
            "", "mm.example.org",          # host: required, empty re-prompts
            "maybe", "n",                  # tls: invalid then no
            "abc", "9000",                 # port: not a number, then 9000
            "huge", "2",                   # tier: invalid, then 2 -> large
            "",                            # note: optional, skipped
        ])
        prompts = [c.args[0] for c in asked.call_args_list]
        assert prompts[0] == "Public host name: "
        assert prompts[2] == "Enable TLS (yes/no) [yes]: "
        assert prompts[4] == "Port [8065]: "
        assert prompts[6] == "Tier (small/large): "
        assert prompts[8] == "Note (optional): "
        err = capsys.readouterr().err
        assert "An answer is required." in err and "Please answer yes or no." in err
        assert "Please enter a whole number." in err and "Please choose one of: small, large." in err
        assert ns["_read_toml_file"](root_of(ns) / "shared" / "answers.toml") == {
            "host": "mm.example.org", "tls": False, "port": 9000, "tier": "large"}

    def test_empty_replies_take_the_default_and_a_name_picks_a_choice(self, tmp_path):
        ns = _asks(tmp_path)
        self._run(ns, ["h", "", "", "small", "a note"])
        assert ns["_read_toml_file"](root_of(ns) / "shared" / "answers.toml") == {
            "host": "h", "tls": True, "port": 8065, "tier": "small", "note": "a note"}

    def test_update_asks_only_the_questions_without_a_stored_answer(self, tmp_path):
        ns = _asks(tmp_path)
        self._run(ns, ["h", "", "", "small", ""])
        newer = _asks(tmp_path, questions=QUESTIONS + [
            {"id": "mail", "prompt": "Mail host", "type": "string", "default": None,
             "choices": [], "required": True}])
        with mock.patch("sys.stdin", _Tty()), \
                mock.patch("builtins.input", side_effect=["smtp"]) as asked:
            update(newer, version=V2)
        assert asked.call_count == 1 and asked.call_args.args[0] == "Mail host: "
        stored = newer["_read_toml_file"](root_of(newer) / "shared" / "answers.toml")
        assert stored["mail"] == "smtp" and stored["host"] == "h"

    def test_a_stored_answer_that_no_longer_fits_is_asked_again(self, tmp_path):
        ns = _asks(tmp_path)
        self._run(ns, ["h", "", "", "small", ""])
        (root_of(ns) / "shared" / "answers.toml").write_text(
            '# skipped: note\nhost = "h"\ntls = true\nport = 1\ntier = "gone"\n')
        with mock.patch("sys.stdin", _Tty()), \
                mock.patch("builtins.input", side_effect=["large"]) as asked:
            update(ns, version=V2)
        assert asked.call_count == 1 and asked.call_args.args[0].startswith("Tier")

    def test_closed_input_is_exit_config(self, tmp_path):
        ns = _asks(tmp_path)
        with mock.patch("sys.stdin", _Tty()), \
                mock.patch("builtins.input", side_effect=EOFError), _exits(2):
            install(ns, version=V1)
        assert not (root_of(ns) / "current").exists()

    def test_the_answers_file_is_rewritten_only_when_it_changes(self, tmp_path):
        ns = _asks(tmp_path)
        self._run(ns, ["h", "", "", "small", ""])
        path = root_of(ns) / "shared" / "answers.toml"
        inode = path.stat().st_ino
        update(ns, version=V2)  # nothing pending: stdin is not a terminal and that is fine
        assert path.stat().st_ino == inode
        path.chmod(0o644)
        update(ns, version=V1)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
