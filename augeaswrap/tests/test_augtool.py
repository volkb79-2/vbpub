"""Augeas argv/script boundary tests; the runner never starts a real binary."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import PlanRefused
from augeaswrap import augtool


def test_augtool_quote_keeps_hostile_values_inside_one_script_string():
    value = ' -leading /slash [brackets] = spaces "double" \\ backslash '
    assert augtool.quote(value) == '" -leading /slash [brackets] = spaces \\"double\\" \\\\ backslash "'


@pytest.mark.parametrize("value", ["line\nbreak", "carriage\rreturn", "nul\x00byte"])
def test_augtool_quote_refuses_script_line_breaks_and_nul(value):
    with pytest.raises(ValueError, match="without newline or NUL"):
        augtool.quote(value)


def test_augtool_quote_refuses_non_string_values():
    with pytest.raises(ValueError, match="without newline or NUL"):
        augtool.quote(123)


def test_staged_runner_argv_script_save_mode_and_private_copy(monkeypatch):
    calls = []
    target_data = b"original\n"
    output_data = b"changed\n"

    def fake_run(argv, *, timeout):
        argv = tuple(argv)
        script_path = Path(argv[-1])
        script = script_path.read_text(encoding="utf-8")
        root = Path(argv[1].split("=", 1)[1])
        copied = root / "etc/fstab"
        private = script_path.parent
        calls.append((argv, script, copied.read_bytes(), private.stat().st_mode & 0o777, copied.stat().st_mode & 0o777, timeout))
        if "--new" in argv:
            copied.with_name("fstab.augnew").write_bytes(output_data)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(augtool.shadow, "run_command", fake_run)
    result = augtool.run(
        path="/etc/fstab",
        lens="Fstab",
        source=target_data,
        script='load\nset "/files/etc/fstab/01[1]/spec" " -disk/[x]=\\"quoted\\""\nsave\n',
        save_new=True,
    )

    argv, script, copied, private_mode, target_mode, timeout = calls[0]
    assert result.saved == output_data
    assert script == 'load\nset "/files/etc/fstab/01[1]/spec" " -disk/[x]=\\"quoted\\""\nsave\n'
    assert copied == target_data
    assert private_mode == 0o700 and target_mode == 0o600
    assert argv[0] == "augtool"
    assert argv[1].startswith("--root=")
    assert argv[2:8] == ("--noautoload", "--nostdinc", "-I", str(Path(argv[-1]).parent / "lenses"), "-I", augtool._DIST_LENSES)
    assert argv[8:11] == ("-t", 'Fstab incl "/etc/fstab"', "--new")
    assert argv[11] == "-f" and argv[12].endswith("commands.aug")
    assert timeout == 30.0


def test_runner_refuses_non_bytes_and_returns_no_new_file_without_save_mode(monkeypatch):
    with pytest.raises(TypeError, match="source must be bytes"):
        augtool.run(path="/etc/fstab", lens="Fstab", source="text", script="load\n")

    def fake_run(argv, *, timeout):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(augtool.shadow, "run_command", fake_run)
    result = augtool.run(path="/etc/fstab", lens="Fstab", source=b"data", script="load\n", save_new=False)
    assert result.saved is None


def test_transform_lens_spelling_is_one_named_choice(monkeypatch):
    assert augtool.AUGTOOL_TRANSFORM_LENS_SPELLING == "module"
    assert augtool.transform_spec("Fstab.lns", "/etc/fstab") == 'Fstab incl "/etc/fstab"'
    monkeypatch.setattr(augtool, "AUGTOOL_TRANSFORM_LENS_SPELLING", "lns")
    assert augtool.transform_spec("Fstab", "/etc/fstab") == 'Fstab.lns incl "/etc/fstab"'
    monkeypatch.setattr(augtool, "AUGTOOL_TRANSFORM_LENS_SPELLING", "other")
    with pytest.raises(ValueError, match="unsupported augtool transform"):
        augtool.transform_spec("Fstab", "/etc/fstab")


def test_augtool_parse_error_message_is_preserved(monkeypatch):
    monkeypatch.setattr(augtool, "require_available", lambda _path: None)
    monkeypatch.setattr(
        augtool.shadow,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="/augeas/files/etc/fstab/error = parse_failed\n/augeas/files/etc/fstab/error/pos = 17\n",
            stderr="",
        ),
    )
    with pytest.raises(PlanRefused) as failure:
        augtool.run(path="/etc/fstab", lens="Fstab", source=b"bad", script="load\n")
    assert failure.value.problems[0].code == "edit.augtool.failed"
    assert "/augeas/files/etc/fstab/error = parse_failed" in failure.value.problems[0].message


def test_augtool_runner_exceptions_and_nonzero_exit_refuse(monkeypatch):
    def failed_run(*_args, **_kwargs):
        raise RuntimeError("runner unavailable")

    monkeypatch.setattr(augtool.shadow, "run_command", failed_run)
    with pytest.raises(PlanRefused, match="runner unavailable"):
        augtool.run(path="/etc/fstab", lens="Fstab", source=b"", script="load\n")

    monkeypatch.setattr(
        augtool.shadow,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="", stderr="invalid lens"),
    )
    with pytest.raises(PlanRefused, match="invalid lens"):
        augtool.run(path="/etc/fstab", lens="Fstab", source=b"", script="load\n")


@pytest.mark.parametrize("missing", ["augtool", "augparse"])
def test_availability_refusal_names_the_package_remedy(monkeypatch, missing):
    monkeypatch.setattr(augtool.shutil, "which", lambda name: None if name == missing else f"/usr/bin/{name}")
    with pytest.raises(PlanRefused) as failure:
        augtool.require_available("/etc/fstab")
    assert failure.value.problems[0].code == "edit.augtool.missing"
    assert "install augeas-tools" in failure.value.problems[0].message


def test_tree_and_match_output_parsers_keep_option_order_and_paths():
    escaped = 'with "quote" and \\slash'
    stdout = "\n".join(
        [
            "/files/etc/fstab",
            "/files/etc/fstab/1",
            '/files/etc/fstab/1/spec = "UUID=x"',
            f"/files/etc/fstab/1/file = {json.dumps(escaped)}",
            '/files/etc/fstab/1/opt[1] = "errors"',
            '/files/etc/fstab/1/opt[1]/value = "remount-ro"',
            '/files/etc/fstab/1/opt[2] = "noatime"',
            "",
        ]
    )
    assert augtool.tree_values(stdout, "/files/etc/fstab") == {
        "1": [
            ("spec", "UUID=x"),
            ("file", escaped),
            ("opt[1]", "errors=remount-ro"),
            ("opt[2]", "noatime"),
        ]
    }
    assert augtool.tree_values(
        '/files/etc/passwd/1/name = "outside"\n/files/etc/fstab/1/sub/child = "deep"\n',
        "/files/etc/fstab",
    ) == {}
    assert augtool.match_paths("/files/etc/fstab/1\nnot a path\n", "/files/etc/fstab") == (
        "/files/etc/fstab/1",
    )
    assert augtool.match_paths("/files/etc/fstab\n", "/files/etc/fstab") == ("/files/etc/fstab",)
    with pytest.raises(ValueError, match="invalid quoted value"):
        augtool.tree_values('/files/etc/fstab/1/spec = "unterminated\n', "/files/etc/fstab")


def test_copy_tree_copies_nested_overlay_files_into_private_modes(tmp_path):
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "lens.aug").write_text("module Lens =\n", encoding="utf-8")
    (nested / "test.aug").write_text("test Lens =\n", encoding="utf-8")
    destination = tmp_path / "private" / "lenses"
    destination.parent.mkdir()

    augtool._copy_tree(source, destination)

    assert (destination / "lens.aug").read_text(encoding="utf-8") == "module Lens =\n"
    assert (destination / "nested/test.aug").read_text(encoding="utf-8") == "test Lens =\n"
    assert destination.stat().st_mode & 0o777 == 0o700
    assert (destination / "lens.aug").stat().st_mode & 0o777 == 0o600
    os.mkfifo(source / "ignored.fifo")
    ignored_copy = tmp_path / "private" / "ignored-lenses"
    augtool._copy_tree(source, ignored_copy)
    assert not (ignored_copy / "ignored.fifo").exists()
