"""Reader-visible contracts for R2 survivors in CMRU's core paths."""
from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import release_preconditions, runner, version


class _ReaderVisibleStream:
    """Model a reader that receives buffered writes only when the stream flushes."""

    def __init__(self):
        self.pending = ""
        self.visible = ""

    def write(self, text: str) -> int:
        self.pending += text
        return len(text)

    def flush(self) -> None:
        self.visible += self.pending
        self.pending = ""


def _run_command_with_stream_result(monkeypatch, result, *, on_line=None, stream):
    def fake_run_streaming(_argv, **kwargs):
        if on_line is not None:
            kwargs["on_line"](on_line)
        return result

    monkeypatch.setattr(runner.procio, "run_streaming", fake_run_streaming)
    with contextlib.redirect_stdout(stream):
        runner.run_command(["child"], Path("."), _LogSink())


class _LogSink:
    def write(self, _text: str) -> int:
        return len(_text)

    def flush(self) -> None:
        return None


def test_zero_no_progress_timeout_is_refused_as_invalid_configuration():
    config = {
        "steps": {
            "build": {
                "quiet": False,
                "no_progress_timeout_seconds": 0,
                "commands": [{"label": "build", "argv": ["true"], "cwd": "."}],
            },
        },
    }

    with pytest.raises(ValueError, match="greater than 0"):
        runner.parse_step(config, "build")


def test_streamed_child_line_is_visible_before_run_command_returns(monkeypatch):
    stream = _ReaderVisibleStream()
    result = SimpleNamespace(returncode=0, warning=None, watchdog=None)

    _run_command_with_stream_result(
        monkeypatch, result, on_line="child progress\n", stream=stream,
    )

    assert "child progress\n" in stream.visible


def test_process_holder_warning_is_visible_to_the_live_console(monkeypatch):
    stream = _ReaderVisibleStream()
    result = SimpleNamespace(
        returncode=0, warning="child still holds output pipe (pid 42)", watchdog=None,
    )

    _run_command_with_stream_result(monkeypatch, result, stream=stream)

    assert "[WARN] child still holds output pipe (pid 42)\n" in stream.visible


def test_watchdog_process_tree_line_is_visible_before_failure_returns(monkeypatch):
    stream = _ReaderVisibleStream()
    watchdog = SimpleNamespace(
        render=lambda _source: "step stopped after no output",
        tree_lines=["python child (pid 42)"],
    )
    result = SimpleNamespace(returncode=-9, warning=None, watchdog=watchdog)

    monkeypatch.setattr(
        runner.procio, "run_streaming",
        lambda *_args, **_kwargs: result,
    )
    with contextlib.redirect_stderr(stream), pytest.raises(subprocess.CalledProcessError):
        runner.run_command(["child"], Path("."), _LogSink())

    assert "python child (pid 42)\n" in stream.visible


def test_dind_preflight_skips_when_docker_is_unavailable_by_default(monkeypatch):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _name: None)

    row = release_preconditions.check_dind_image_precondition(
        {release_preconditions.DIND_IMAGE_ENV: "registry.example/tester:local"},
    )

    assert row.status == "SKIPPED"
    assert "Docker CLI is unavailable" in row.detail


def _install_noisy_git(tmp_path: Path, monkeypatch, script: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "git"
    executable.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv(
        "PATH", os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))),
    )


def test_tag_ancestor_failure_diagnostic_remains_text_for_the_caller(tmp_path, monkeypatch):
    _install_noisy_git(
        tmp_path, monkeypatch,
        "printf 'internal git stdout\\n'\n"
        "printf 'git merge-base diagnostic\\n' >&2\n"
        "exit 2\n",
    )

    with pytest.raises(version.ReleasePlanRefused) as refused:
        version._tag_covers_head(tmp_path, "release-tag")

    assert str(refused.value) == (
        "cannot compare HEAD against tag 'release-tag' "
        "(git merge-base exited 2): git merge-base diagnostic"
    )


def test_tag_relationship_probe_keeps_second_git_result_out_of_the_callers_terminal(
    tmp_path, monkeypatch, capfd,
):
    _install_noisy_git(
        tmp_path, monkeypatch,
        "if [ \"$1\" = rev-parse ]; then\n"
        "  case \"$2\" in\n"
        "    tag*) printf 'tag-commit\\n' ;;\n"
        "    HEAD) printf 'head-commit\\n' ;;\n"
        "    *) exit 2 ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = merge-base ]; then\n"
        "  printf 'internal merge-base stdout\\n'\n"
        "  printf 'internal merge-base stderr\\n' >&2\n"
        "  if [ \"$3\" = HEAD ]; then exit 1; fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
    )

    assert version._tag_head_relationship(tmp_path, "tag") == "behind"

    output = capfd.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_tag_relationship_failure_diagnostic_remains_text_for_the_caller(
    tmp_path, monkeypatch,
):
    _install_noisy_git(
        tmp_path, monkeypatch,
        "if [ \"$1\" = rev-parse ]; then\n"
        "  case \"$2\" in\n"
        "    tag*) printf 'tag-commit\\n' ;;\n"
        "    HEAD) printf 'head-commit\\n' ;;\n"
        "    *) exit 2 ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = merge-base ]; then\n"
        "  if [ \"$3\" = HEAD ]; then exit 1; fi\n"
        "  printf 'internal merge-base stdout\\n'\n"
        "  printf 'second merge-base diagnostic\\n' >&2\n"
        "  exit 2\n"
        "fi\n"
        "exit 2\n",
    )

    with pytest.raises(version.ReleasePlanRefused) as refused:
        version._tag_head_relationship(tmp_path, "tag")

    assert str(refused.value) == (
        "cannot compare tag 'tag' with HEAD "
        "(git merge-base exited 2): second merge-base diagnostic"
    )
