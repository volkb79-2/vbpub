from __future__ import annotations

import json
import runpy
import sys

from cowtree import cli
from cowtree.detect import Area, Capabilities
from cowtree.errors import PathRefused, RootInvalid
from cowtree.gc import GcItem

from .fakes import FakeOps


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


def test_detect_cli_error_codes_and_output(monkeypatch, capsys):
    monkeypatch.setattr(cli, "detect", lambda: (_ for _ in ()).throw(RootInvalid("broken")))
    assert cli.main(["detect", "--json"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "root-invalid"
    monkeypatch.setattr(cli, "detect", lambda: (_ for _ in ()).throw(PathRefused("name")))
    assert cli.main(["detect"]) == 1
    assert "cowtree: name" in capsys.readouterr().err


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
