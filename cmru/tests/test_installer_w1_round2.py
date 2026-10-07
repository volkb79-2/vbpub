"""W1-INSTALLER review fix round 2: manifest key grammar (N16), `install_dir_*` shape,
`cmru handler bundle-manifest` error handling. Each test asserts the observable refusal."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from cmru.getpy import RenderError, render_get_py
from tests.installer_fakes import render_ns
from tests.test_installer_w1 import _exits

OK_ENTRY = {"sha256": "0" * 64}


class TestManifestFilesKeyGrammar:
    """N16: refused by manifest validation itself, not by a later failed lookup."""

    @pytest.mark.parametrize("key", [
        "../x", "..", "a/../../x", "/abs", "a//b", "./a", "a/./b", "a/", ".",
        "a\0b", "a\\b", "..\\x", "",
    ])
    def test_bad_key_is_refused(self, tmp_path, key):
        ns = render_ns(tmp_path)
        with _exits(1):
            ns["_manifest_files"]({"files": {key: OK_ENTRY}})

    @pytest.mark.parametrize("key", ["VERSION", "scripts/render.sh", "a/b/c.txt", "..x", "a..b"])
    def test_good_key_is_accepted(self, tmp_path, key):
        ns = render_ns(tmp_path)
        assert key in ns["_manifest_files"]({"files": {key: OK_ENTRY}})

    def test_non_object_files_is_refused(self, tmp_path):
        ns = render_ns(tmp_path)
        with _exits(1):
            ns["_manifest_files"]({"files": ["VERSION"]})


def _render(system: str, user: str = "demo") -> str:
    return render_get_py(project_name="demo", repo_owner="o", repo_name="r",
                         tag_prefix="demo-v", install_dir_system=system,
                         install_dir_user=user)


class TestInstallDirShape:
    @pytest.mark.parametrize("path", ["/opt", "/srv", "/tmp", "/root", "/home", "/x", "/opt/",
                                      "/", "//opt/demo", "/opt//demo", "/opt/./demo",
                                      "/opt/demo/", "/opt/../etc", "opt/demo"])
    def test_system_dir_must_be_absolute_normalised_with_two_components(self, path):
        with pytest.raises(RenderError, match="install_dir_system"):
            _render(path)

    @pytest.mark.parametrize("path", ["/opt/demo", "/srv/demo", "/usr/local/demo", "/a/b/c"])
    def test_two_or_more_components_are_fine(self, path):
        _render(path)


def _bm_args(root: Path, tag: str = "demo-v1.0.0", **kw) -> argparse.Namespace:
    return argparse.Namespace(name="demo", tag=tag, root=str(root),
                              manifest_name="manifest.json", **kw)


class TestBundleManifestErrors:
    def test_symlink_in_tree_is_a_one_line_error_not_a_traceback(
            self, tmp_path, capsys, monkeypatch):
        from cmru.handlers import cmd_bundle_manifest
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
        stage = tmp_path / "stage"
        stage.mkdir()
        (stage / "VERSION").write_text("1\n")
        (stage / "alias").symlink_to("VERSION")
        with pytest.raises(SystemExit) as exc:
            cmd_bundle_manifest(_bm_args(stage))
        err = capsys.readouterr().err
        assert exc.value.code == 1
        assert err.startswith("[ERROR]") and err.count("\n") == 1
        assert "Traceback" not in err and "symlink" in err
        assert not (stage / "manifest.json").exists()

    def test_missing_root_is_a_clean_error(self, tmp_path, capsys, monkeypatch):
        from cmru.handlers import cmd_bundle_manifest
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
        with pytest.raises(SystemExit) as exc:
            cmd_bundle_manifest(_bm_args(tmp_path / "nope"))
        err = capsys.readouterr().err
        assert exc.value.code == 1 and "not a directory" in err and "Traceback" not in err

    @pytest.mark.parametrize("tag", ["", "../x", "a..b", "-x", "a b", "a/b", "v1\n", "a\\b"])
    def test_invalid_tag_is_exit_2_and_writes_nothing(self, tmp_path, capsys, tag):
        from cmru.handlers import cmd_bundle_manifest
        stage = tmp_path / "stage"
        stage.mkdir()
        (stage / "VERSION").write_text("1\n")
        with pytest.raises(SystemExit) as exc:
            cmd_bundle_manifest(_bm_args(stage, tag=tag))
        err = capsys.readouterr().err
        assert exc.value.code == 2 and "invalid --tag" in err and "Traceback" not in err
        assert not (stage / "manifest.json").exists()

    def test_valid_tag_still_writes_the_manifest(self, tmp_path, monkeypatch):
        from cmru.handlers import cmd_bundle_manifest
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
        stage = tmp_path / "stage"
        stage.mkdir()
        (stage / "VERSION").write_text("1\n")
        cmd_bundle_manifest(_bm_args(stage, tag="demo-v1.0.0+build.1"))
        assert "VERSION" in json.loads((stage / "manifest.json").read_text())["files"]
