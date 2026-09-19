"""Native release logging replaces the removed shell wrapper."""
from __future__ import annotations

from pathlib import Path

from cmru import cli


def test_native_release_log_overwrites_then_appends_with_divider(tmp_path, monkeypatch):
    log = tmp_path / "cmru.release.log"
    monkeypatch.setenv("CMRU_RELEASE_LOG", str(log))
    log.write_text("old run\n", encoding="utf-8")

    assert cli._prepare_native_release_log(tmp_path, append=False) == log.resolve()
    assert log.read_text(encoding="utf-8") == ""
    assert cli.os.environ["CMRU_RUN_LOG"] == str(log.resolve())
    assert cli.os.environ["PYTHONUNBUFFERED"] == "1"

    cli._prepare_native_release_log(tmp_path, append=True)
    assert log.read_text(encoding="utf-8") == "\n---\n"

    nested = tmp_path / "new-parent" / "cmru.log"
    monkeypatch.setenv("CMRU_RELEASE_LOG", str(nested))
    cli._prepare_native_release_log(tmp_path, append=False)
    cli._prepare_native_release_log(tmp_path, append=True)
    assert nested.read_text(encoding="utf-8") == "\n---\n"


def test_native_release_logging_reuses_an_existing_log_directory(tmp_path, monkeypatch):
    parent = tmp_path / "existing-parent"
    parent.mkdir()
    monkeypatch.setenv("CMRU_RELEASE_LOG", str(parent / "cmru.log"))

    assert cli._prepare_native_release_log(tmp_path, append=False) == (
        parent / "cmru.log"
    ).resolve()


def test_shell_release_wrapper_is_retired():
    assert not (Path(__file__).resolve().parents[2] / "cmru.release.sh").exists()
