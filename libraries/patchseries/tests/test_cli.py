from __future__ import annotations

import runpy
from pathlib import Path

import pytest
from patchseries.cli import main


def write_patch(root: Path, change_id: str, *, requires: str = "none") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    text = (
        "From 0123456789abcdef0123456789abcdef01234567 Mon Sep 17 00:00:00 2001\n"
        "From: CLI Tester <cli@example.test>\n"
        "Subject: [PATCH] Add CLI sample\n\n"
        "CLI sample.\n\n"
        f"Change-Id: {change_id}\n"
        "Capability: placement\n"
        "Role: enable\n"
        "Risk: low\n"
        f"Requires: {requires}\n\n"
        "---\n"
        " x.go | 1 +\n"
        " 1 file changed, 1 insertion(+)\n"
        "diff --git a/x.go b/x.go\n"
        "--- a/x.go\n"
        "+++ b/x.go\n"
        "@@ -0,0 +1 @@\n"
        "+line\n"
    )
    (root / f"0001-{change_id}.patch").write_text(text, encoding="utf-8")
    return root


def write_vocabulary(path: Path) -> Path:
    path.write_text(
        "schema_version = 1\n"
        'capabilities = ["placement"]\n'
        'roles = ["enable"]\n'
        'risks = ["low"]\n',
        encoding="utf-8",
    )
    return path


def test_check_and_closure_commands(tmp_path: Path, capsys) -> None:
    series = write_patch(tmp_path / "series", "base")
    vocabulary = write_vocabulary(tmp_path / "vocabulary.toml")

    assert main(["check", str(series), "--vocabulary", str(vocabulary)]) == 0
    assert "OK: 1 changes" in capsys.readouterr().out
    assert main(["closure", str(series), "base"]) == 0
    assert "base" in capsys.readouterr().out


def test_check_reports_series_errors(tmp_path: Path, capsys) -> None:
    series = write_patch(tmp_path / "series", "base")
    vocabulary = write_vocabulary(tmp_path / "vocabulary.toml")
    (series / "0001-base.patch").write_text("bad patch", encoding="utf-8")

    assert main(["check", str(series), "--vocabulary", str(vocabulary)]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_loads_vocabulary_and_rejects_non_arrays(tmp_path: Path, capsys) -> None:
    series = write_patch(tmp_path / "series", "base")
    vocabulary = tmp_path / "bad.toml"
    vocabulary.write_text('capabilities = "placement"\n', encoding="utf-8")

    assert main(["check", str(series), "--vocabulary", str(vocabulary)]) == 2
    assert "vocabulary" in capsys.readouterr().err


def test_cli_rejects_wrong_or_missing_vocabulary_schema(tmp_path: Path, capsys) -> None:
    series = write_patch(tmp_path / "series", "base")
    vocabulary = tmp_path / "old.toml"
    vocabulary.write_text(
        'schema_version = 2\ncapabilities = ["placement"]\n'
        'roles = ["enable"]\nrisks = ["low"]\n',
        encoding="utf-8",
    )

    assert main(["check", str(series), "--vocabulary", str(vocabulary)]) == 2
    assert "schema_version = 1" in capsys.readouterr().err


def test_closure_rejects_unknown_ids(tmp_path: Path, capsys) -> None:
    series = write_patch(tmp_path / "series", "base")

    assert main(["closure", str(series), "missing"]) == 2
    assert "unknown selected Change-Id" in capsys.readouterr().err


def test_render_and_delta_commands(tmp_path: Path, capsys) -> None:
    old = write_patch(tmp_path / "old", "base")
    new = write_patch(tmp_path / "new", "base")
    write_patch(old, "removed")
    write_patch(new, "added")

    assert main(["render", "--column", f"old={old}", "--column", f"new={new}"]) == 0
    assert "## Capabilities" in capsys.readouterr().out
    assert main(["delta", str(old), str(new)]) == 0
    assert "unchanged" in capsys.readouterr().out


def test_render_rejects_invalid_column_syntax(tmp_path: Path, capsys) -> None:
    series = write_patch(tmp_path / "series", "base")

    assert main(["render", "--column", f"invalid={series}=extra"]) == 2
    assert "column" in capsys.readouterr().err


@pytest.mark.parametrize("column", ["=series", "label="])
def test_render_rejects_empty_column_parts(tmp_path: Path, capsys, column: str) -> None:
    assert main(["render", "--column", column]) == 2
    assert "must not be empty" in capsys.readouterr().err


def test_help_and_unknown_command_use_argparse() -> None:
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0

    with pytest.raises(SystemExit) as unknown_exit:
        main(["not-a-command"])
    assert unknown_exit.value.code == 2

    with pytest.raises(SystemExit) as missing_exit:
        main([])
    assert missing_exit.value.code == 2


def test_python_module_entrypoint_executes_cli(capsys) -> None:
    with pytest.raises(SystemExit) as exit_status:
        runpy.run_module("patchseries.__main__", run_name="__main__")

    assert exit_status.value.code == 2
    assert "usage:" in capsys.readouterr().err
