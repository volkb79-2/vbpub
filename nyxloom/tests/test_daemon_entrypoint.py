from __future__ import annotations

import pytest

from nyxloom import config, daemon
from nyxloom.daemon_entrypoint import main


@pytest.mark.parametrize("argv", [["--help"], ["--version"]])
def test_help_and_version_exit_before_loading_daemon(monkeypatch, capsys, argv):
    def unexpected(*_args, **_kwargs):
        pytest.fail("informational daemon options must not load or start the daemon")

    monkeypatch.setattr(config, "load_registry", unexpected)
    monkeypatch.setattr(daemon.Daemon, "run", unexpected)

    with pytest.raises(SystemExit) as exc:
        main(argv)

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "nyxloomd" in output
    if argv == ["--help"]:
        assert "--help" in output
        assert "--version" in output


@pytest.mark.parametrize("argv", [["--unknown-option"], ["--ver"], ["-h"]])
def test_unknown_arguments_are_rejected_before_loading_daemon(monkeypatch, argv):
    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid daemon arguments must not load or start the daemon")

    monkeypatch.setattr(config, "load_registry", unexpected)
    monkeypatch.setattr(daemon.Daemon, "run", unexpected)

    with pytest.raises(SystemExit) as exc:
        main(argv)

    assert exc.value.code == 2


def test_bare_entrypoint_starts_daemon(monkeypatch):
    sentinel = object()
    calls: list[object] = []

    class FakeDaemon:
        def __init__(self, registry):
            calls.append(registry)

        def run(self):
            calls.append("run")

    monkeypatch.setattr(config, "load_registry", lambda: sentinel)
    monkeypatch.setattr(daemon, "Daemon", FakeDaemon)

    assert main([]) == 0
    assert calls == [sentinel, "run"]
