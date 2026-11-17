from __future__ import annotations

import json
import runpy
import sys

import pytest
from cowtree import cli
from cowtree.detect import Area, Capabilities
from cowtree.provision import LevelUnavailable
from cowtree.errors import CacheBusy, KeyMissing, TreeInvalid, Unrepairable, BackendFailed, CowError, LeaseHeld, PathRefused, RootInvalid, SpaceRefused
from cowtree.gc import GcItem

from .fakes import FakeBtrfs, FakeOps, btrfs_area, write_repo


def _area(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    private.mkdir()
    runs = private / "runs"
    runs.mkdir()
    shared = root / "shared"
    shared.mkdir()
    from cowtree.backends import CopyBackend
    return Area(str(root), Capabilities("copy", "ext4", False, False, True, False, False), CopyBackend(), FakeOps(), str(private), str(runs), str(shared))


def test_detect_cli_off_and_valid_area(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "detect", lambda: None)
    assert cli.main(["detect", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"backend": "off", "root": None}
    area = _area(tmp_path)
    monkeypatch.setattr(cli, "detect", lambda: area)
    assert cli.main(["detect", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"backend": "copy", "fs_type": "ext4", "root": area.root}


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (CowError("generic"), 1),
        (RootInvalid("broken"), 3),
        (SpaceRefused(3, 2, {}), 4),
        (PathRefused("name"), 5),
        (BackendFailed("clone", 5), 6),
        (LeaseHeld("held"), 7),
        (CacheBusy("busy"), 8),
        (KeyMissing("missing"), 9),
        (TreeInvalid("bad"), 10),
        (Unrepairable("dir"), 11),
        (LevelUnavailable("none"), 12),
    ],
)
def test_detect_cli_maps_typed_errors_to_distinct_statuses(monkeypatch, capsys, error, status):
    monkeypatch.setattr(cli, "detect", lambda: (_ for _ in ()).throw(error))
    assert cli.main(["detect", "--json"]) == status
    assert json.loads(capsys.readouterr().err)["error"] == error.code


def test_cli_usage_error_keeps_argparse_status_two(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["unknown-command"])
    assert caught.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_survey_cli_off_and_area(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "detect", lambda: None)
    assert cli.main(["survey", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["backend"] == "off"
    area = _area(tmp_path)
    monkeypatch.setattr(cli, "detect", lambda: area)
    monkeypatch.setattr(cli, "survey", lambda _area: {"backend": "copy", "runs": []})
    assert cli.main(["survey"]) == 0
    assert "backend: copy" in capsys.readouterr().out


def test_gc_cli_scan_and_off(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "gc", lambda path: [GcItem("one", "deleted")])
    assert cli.main(["gc", "--scan", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "deleted"
    assert cli.main(["gc", "--scan", str(tmp_path)]) == 0
    assert "one: deleted" in capsys.readouterr().out
    monkeypatch.setattr(cli, "detect", lambda: None)
    assert cli.main(["gc", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_gc_cli_uses_area_runs(monkeypatch, tmp_path, capsys):
    area = _area(tmp_path)
    monkeypatch.setattr(cli, "detect", lambda: area)
    seen = []
    monkeypatch.setattr(cli, "gc", lambda path: seen.append(path) or [])
    assert cli.main(["gc"]) == 0
    assert seen == [area.runs_dir]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("as_json", [False, True])
def test_gc_cli_plain_mode_prints_scan_instruction(monkeypatch, capsys, as_json):
    monkeypatch.setattr(cli, "detect", lambda: None)
    argv = ["gc"] + (["--json"] if as_json else [])
    assert cli.main(argv) == 0
    output, error = capsys.readouterr()
    assert output == ("[]\n" if as_json else "")
    assert error == (
        "cowtree: INFO: plain-mode runs are not included in default gc; "
        "use cowtree gc --scan DIR with the directory that contains them\n"
    )


def test_make_shared_cli_off_and_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "detect", lambda: None)
    assert cli.main(["make-shared", "cache"]) == 1
    assert "requires a valid MDT_COW_ROOT" in capsys.readouterr().err
    area = _area(tmp_path)
    monkeypatch.setattr(cli, "detect", lambda: area)
    monkeypatch.setattr(cli, "make_shared", lambda _area, name, compression=None: f"/shared/{name}:{compression}")
    assert cli.main(["make-shared", "cache", "--compression", "zstd:3", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"path": "/shared/cache:zstd:3"}


def test_output_text_and_json(capsys):
    cli._output("plain", False)
    assert capsys.readouterr().out == "plain\n"
    cli._output({"key": "value"}, True)
    assert json.loads(capsys.readouterr().out) == {"key": "value"}


def test_module_entrypoint_runs_cli(monkeypatch, capsys):
    monkeypatch.setattr(cli, "detect", lambda: None)
    monkeypatch.setattr(sys, "argv", ["cowtree", "detect", "--json"])
    try:
        runpy.run_module("cowtree.__main__", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
    assert json.loads(capsys.readouterr().out) == {"backend": "off", "root": None}


def test_main_returns_unknown_command_status_if_parser_is_extended(monkeypatch):
    class Parser:
        @staticmethod
        def parse_args(_argv):
            return type("Args", (), {"command": "unrecognized", "json": False})()

    monkeypatch.setattr(cli, "_parser", lambda: Parser())
    assert cli.main([]) == 2


def _cache_env(tmp_path):
    root = tmp_path / "tree-cache"
    root.mkdir(mode=0o770)
    from cowtree.cache import Cache
    cache = Cache(str(root), require_tmpfs=False)
    cache.ensure("a" * 16, lambda tree: open(tree + "/f", "w").write("x"))
    return str(root)


def test_cache_cli_survey_gc_verify(tmp_path, capsys):
    root = _cache_env(tmp_path)
    assert cli.main(["cache", "survey", "--root", root, "--allow-non-tmpfs"]) == 0
    assert "kind=key" in capsys.readouterr().out
    assert cli.main(["cache", "survey", "--root", root, "--allow-non-tmpfs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["files"] == 1
    assert cli.main(["cache", "verify", "--root", root, "--allow-non-tmpfs", "a" * 16]) == 0
    assert "digest: " in capsys.readouterr().out
    assert cli.main(["cache", "verify", "--root", root, "--allow-non-tmpfs", "a" * 16, "--shallow", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["cache", "verify", "--root", root, "--allow-non-tmpfs", "b" * 16, "--json"]) == 9
    assert json.loads(capsys.readouterr().err)["error"] == "key-missing"
    assert cli.main(["cache", "gc", "--root", root, "--allow-non-tmpfs"]) == 0
    assert "a" * 16 + ": kept: idle" in capsys.readouterr().out
    assert cli.main(["cache", "gc", "--root", root, "--allow-non-tmpfs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "kept"
    assert cli.main(["cache", "gc", "--root", root, "--allow-non-tmpfs", "--idle", "0"]) == 0
    assert "removed" in capsys.readouterr().out


def test_cache_cli_refuses_a_non_tmpfs_root_by_default(tmp_path, monkeypatch, capsys):
    root = _cache_env(tmp_path)
    monkeypatch.setattr("cowtree.cache.mount_fstype", lambda path, text: "ext4")
    assert cli.main(["cache", "gc", "--root", root, "--json"]) == 3
    assert "not a tmpfs" in capsys.readouterr().err


def test_cache_cli_requires_explicit_root(capsys):
    with pytest.raises(SystemExit):
        cli.main(["cache", "gc"])


def test_probe_cli_reports_level_and_failure(tmp_path, capsys):
    copy = tmp_path / "copy"
    copy.mkdir(mode=0o770)
    assert cli.main(["probe", "--copy-dir", str(copy), "--level", "D"]) == 0
    out = capsys.readouterr().out
    assert "level: D (requested D)" in out and "  D: ok" in out
    assert cli.main(["probe", "--copy-dir", str(copy), "--json", "--shared-root", str(copy), "--overlay-via", "mount"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["level"] in ("B", "C", "D") and report["checks"]["A"]
    assert cli.main(["probe", "--json"]) == 12
    assert json.loads(capsys.readouterr().err)["error"] == "level-unavailable"


def _plain_environment(monkeypatch):
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)


def _cow_area(tmp_path, monkeypatch):
    from cowtree import repo_paths
    from cowtree.detect import detect as real_detect

    root, environ, ops = btrfs_area(tmp_path)
    area = real_detect(environ=environ, ops=ops)
    monkeypatch.setattr(repo_paths, "detect", lambda **_kwargs: area)
    return root


def test_path_cli_prints_the_plain_path_or_json(tmp_path, monkeypatch, capsys):
    _plain_environment(monkeypatch)
    root = write_repo(tmp_path)
    assert cli.main(["path", "iso", "--repo-root", str(root)]) == 0
    assert capsys.readouterr().out == f"{root}/downloads/iso\n"
    argv = ["path", "vm-lab", "--project", "scripts/x", "--repo-root", str(root), "--json"]
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {
        "name": "vm-lab", "project": "scripts/x", "path": f"{root}/scripts/x/vm-lab", "mode": "plain",
        "repo": "myrepo",
    }


def test_path_cli_searches_upward_from_the_current_directory_without_repo_root(tmp_path, monkeypatch, capsys):
    _plain_environment(monkeypatch)
    root = write_repo(tmp_path)
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert cli.main(["path", "downloads"]) == 0
    assert capsys.readouterr().out == f"{root}/downloads\n"


def test_path_cli_reports_cow_mode_for_an_existing_cow_folder(tmp_path, monkeypatch, capsys):
    root = write_repo(tmp_path)
    area = _cow_area(tmp_path, monkeypatch)
    (area / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    assert cli.main(["path", "iso", "--repo-root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "name": "iso", "project": None, "path": f"{area}/myrepo/downloads/iso", "mode": "cow", "repo": "myrepo",
    }


def test_path_cli_provision_creates_the_subvolume_first(tmp_path, monkeypatch, capsys):
    from cowtree import repo_paths

    root = write_repo(tmp_path)
    area = _cow_area(tmp_path, monkeypatch)
    fake = FakeBtrfs()
    monkeypatch.setattr(repo_paths, "Btrfs", fake.tools)
    assert cli.main(["path", "iso", "--repo-root", str(root), "--provision"]) == 0
    assert capsys.readouterr().out == f"{area}/myrepo/downloads/iso\n"
    assert cli.main(["path", "iso", "--repo-root", str(root), "--provision", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "cow"
    assert [call[1:3] for call in fake.calls] == [("subvolume", "create"), ("property", "set"), ("property", "get")]


def test_path_cli_maps_failures_to_the_typed_statuses(tmp_path, monkeypatch, capsys):
    from cowtree import repo_paths

    _plain_environment(monkeypatch)
    root = write_repo(tmp_path)
    base = ["--repo-root", str(root), "--json"]
    cases = [
        (["path", "nope", *base], 2, "usage"),
        (["path", "vm-lab", *base], 2, "usage"),
        (["path", "iso", "--project", "../x", *base], 2, "usage"),
        (["path", "iso", "--repo-root", str(write_repo(tmp_path, "schema = 9\n", "bad")), "--json"], 5, "path"),
    ]
    for argv, status, code in cases:
        assert cli.main(argv) == status
        assert json.loads(capsys.readouterr().err)["error"] == code
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert cli.main(["path", "iso"]) == 2
    assert "no repo-paths.toml at or above" in capsys.readouterr().err

    area = _cow_area(tmp_path, monkeypatch)
    (area / "myrepo" / "downloads" / "iso").mkdir(parents=True)
    fake = FakeBtrfs()
    monkeypatch.setattr(repo_paths, "Btrfs", fake.tools)
    assert cli.main(["path", "iso", "--provision", *base]) == 5
    assert json.loads(capsys.readouterr().err)["error"] == "path"
    fake_failing = FakeBtrfs(fail=("subvolume", "create"))
    monkeypatch.setattr(repo_paths, "Btrfs", fake_failing.tools)
    assert cli.main(["path", "vm-images", "--provision", *base]) == 6
    assert json.loads(capsys.readouterr().err)["error"] == "backend"
    monkeypatch.setattr(repo_paths, "detect", lambda **_kwargs: (_ for _ in ()).throw(RootInvalid("broken")))
    assert cli.main(["path", "iso", *base]) == 3
    assert json.loads(capsys.readouterr().err)["error"] == "root-invalid"


def test_path_cli_requires_a_name(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["path"])
    assert caught.value.code == 2
