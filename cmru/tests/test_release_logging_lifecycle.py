from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


def test_release_failure_closes_and_waits_for_the_native_log_tee(monkeypatch, tmp_path):
    log_path = tmp_path / "release.log"
    monkeypatch.setenv("CMRU_RELEASE_LOG", str(log_path))
    monkeypatch.delenv("CMRU_NATIVE_RELEASE_LOGGING", raising=False)
    args = SimpleNamespace(
        resume=False,
        skip_gates=None,
        config=None,
        secret_file=None,
        log_append=False,
        target=None,
        set_version=None,
    )
    monkeypatch.setattr(cli, "_resolve_ahead_check_ref", lambda _args: None)
    monkeypatch.setattr(cli, "_apply_output_options", lambda _args: None)
    monkeypatch.setattr(cli, "_resolve_config", lambda _config: tmp_path / "cmru.toml")
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda _path: (tmp_path, {}, [], None, None, {"token_source": "cmru"}, {}),
    )
    monkeypatch.setattr(cli, "refuse_secret_file_under_gh", lambda *_args: None)
    monkeypatch.setattr(cli, "_git_auth_for_repository", lambda _config: None)
    monkeypatch.setattr(cli.transaction, "is_transaction_child", lambda _root: False)

    def fail_after_logging(*_args, **_kwargs):
        raise RuntimeError("release stopped after log setup")

    monkeypatch.setattr(cli, "apply_release_env", fail_after_logging)
    with pytest.raises(RuntimeError, match="release stopped"):
        cli._release_or_status("release", args, [])

    assert log_path.exists()
    assert cli._ACTIVE_NATIVE_RELEASE_LOGGING is None


def test_native_release_logging_scope_restores_output_and_flushes_log(monkeypatch, tmp_path):
    log_path = tmp_path / "release.log"
    monkeypatch.setenv("CMRU_RELEASE_LOG", str(log_path))
    monkeypatch.delenv("CMRU_NATIVE_RELEASE_LOGGING", raising=False)
    original_stdout = os.fstat(1)

    with cli._native_release_logging_scope():
        cli._configure_native_release_logging(tmp_path, append=False)
        tee = cli._ACTIVE_NATIVE_RELEASE_LOGGING.process
        os.write(1, b"release-log-lifecycle-probe\n")
        assert tee.poll() is None

    assert tee.poll() == 0
    assert os.fstat(1).st_ino == original_stdout.st_ino
    assert "release-log-lifecycle-probe" in log_path.read_text(encoding="utf-8")


def test_native_release_logging_scope_flushes_buffered_python_streams_to_log(tmp_path):
    log_path = tmp_path / "release.log"
    source = """
import sys
from pathlib import Path
from cmru import cli

assert not sys.stdout.isatty()
assert not sys.stderr.isatty()
with cli._native_release_logging_scope():
    cli._configure_native_release_logging(Path.cwd(), append=False)
    print("bare-buffered-release-stdout")
    sys.stderr.write("bare-buffered-release-stderr")
"""
    env = os.environ.copy()
    env.pop("CMRU_NATIVE_RELEASE_LOGGING", None)
    env["CMRU_RELEASE_LOG"] = str(log_path)
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    log = log_path.read_text(encoding="utf-8")
    assert "bare-buffered-release-stdout" in log
    assert "bare-buffered-release-stderr" in log
    assert "bare-buffered-release-stdout" in completed.stdout


def test_native_release_logging_scope_is_empty_when_logging_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_NATIVE_RELEASE_LOGGING", "0")
    with cli._native_release_logging_scope():
        cli._configure_native_release_logging(tmp_path, append=False)
        assert cli._ACTIVE_NATIVE_RELEASE_LOGGING is None


def test_nested_logging_scope_leaves_the_outer_release_tee_open(tmp_path):
    log_path = tmp_path / "nested-release.log"
    source = """
import sys
from pathlib import Path
from cmru import cli

with cli._native_release_logging_scope():
    cli._configure_native_release_logging(Path.cwd(), append=False)
    with cli._native_release_logging_scope():
        pass
    print("release-output-after-inner-scope")
"""
    env = os.environ.copy()
    env.pop("CMRU_NATIVE_RELEASE_LOGGING", None)
    env["CMRU_RELEASE_LOG"] = str(log_path)
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "release-output-after-inner-scope" in completed.stdout
    assert "release-output-after-inner-scope" in log_path.read_text(encoding="utf-8")


def test_native_release_logging_refuses_a_second_active_tee(monkeypatch, tmp_path):
    monkeypatch.delenv("CMRU_NATIVE_RELEASE_LOGGING", raising=False)
    with cli._native_release_logging_scope():
        cli._configure_native_release_logging(tmp_path, append=False)
        with pytest.raises(RuntimeError, match="already active"):
            cli._configure_native_release_logging(tmp_path, append=False)


def test_native_release_logging_restores_descriptors_when_tee_start_fails(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv("CMRU_NATIVE_RELEASE_LOGGING", raising=False)
    original_stdout = os.fstat(1)
    original_stderr = os.fstat(2)

    def fail_to_start(*_args, **_kwargs):
        raise OSError("tee is unavailable")

    monkeypatch.setattr(cli.subprocess, "Popen", fail_to_start)
    with pytest.raises(OSError, match="tee is unavailable"):
        cli._configure_native_release_logging(tmp_path, append=False)

    assert os.fstat(1).st_ino == original_stdout.st_ino
    assert os.fstat(2).st_ino == original_stderr.st_ino


@pytest.mark.parametrize("poll_result", [None, 0])
def test_native_release_logging_waits_when_pipe_setup_fails(
    monkeypatch, tmp_path, poll_result,
):
    monkeypatch.delenv("CMRU_NATIVE_RELEASE_LOGGING", raising=False)

    class FakeStdin:
        @staticmethod
        def fileno():
            return 0

    class FakeTee:
        stdin = FakeStdin()

        def __init__(self):
            self.killed = False
            self.waited = False

        @staticmethod
        def poll():
            return poll_result

        def kill(self):
            self.killed = True

        def wait(self):
            self.waited = True

    tee = FakeTee()
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_args, **_kwargs: tee)
    original_os = cli.os
    patched_os = types.SimpleNamespace(**vars(original_os))
    real_dup2 = original_os.dup2
    first_dup = True

    def fail_first_dup2(source, destination):
        nonlocal first_dup
        if first_dup:
            first_dup = False
            raise OSError("pipe setup failed")
        return real_dup2(source, destination)

    patched_os.dup2 = fail_first_dup2
    monkeypatch.setattr(cli, "os", patched_os)
    with pytest.raises(OSError, match="pipe setup failed"):
        cli._configure_native_release_logging(tmp_path, append=False)

    assert tee.killed is (poll_result is None)
    assert tee.waited


def test_native_release_logging_timeout_kills_and_waits_for_tee(monkeypatch):
    class TimedOutTee:
        def __init__(self):
            self.wait_calls = 0
            self.killed = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise cli.subprocess.TimeoutExpired("tee", timeout)
            return 0

        def kill(self):
            self.killed = True

    tee = TimedOutTee()
    previous_stdout = os.dup(1)
    previous_stderr = os.dup(2)
    with pytest.raises(RuntimeError, match="did not exit"), cli._native_release_logging_scope():
        cli._ACTIVE_NATIVE_RELEASE_LOGGING = cli._NativeReleaseLogging(
            tee, previous_stdout, previous_stderr,
        )
    assert tee.killed and tee.wait_calls == 2
    assert cli._ACTIVE_NATIVE_RELEASE_LOGGING is None


def test_native_release_logging_timeout_preserves_an_in_flight_exception():
    class TimedOutTee:
        def __init__(self):
            self.wait_calls = 0
            self.killed = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise cli.subprocess.TimeoutExpired("tee", timeout)
            return 0

        def kill(self):
            self.killed = True

    tee = TimedOutTee()
    previous_stdout = os.dup(1)
    previous_stderr = os.dup(2)
    with pytest.raises(ValueError, match="release refused") as raised:
        with cli._native_release_logging_scope():
            cli._ACTIVE_NATIVE_RELEASE_LOGGING = cli._NativeReleaseLogging(
                tee, previous_stdout, previous_stderr,
            )
            raise ValueError("release refused")

    assert tee.killed and tee.wait_calls == 2
    assert cli._ACTIVE_NATIVE_RELEASE_LOGGING is None
    assert any("native release log tee did not exit" in note for note in raised.value.__notes__)
