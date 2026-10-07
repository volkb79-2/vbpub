pytest_plugins = ["cli_extended.pytest_plugin"]


import pytest


@pytest.fixture(autouse=True)
def _no_network_git(monkeypatch):
    """build-customscript reports the fetch source via `git ls-remote`; tests
    must never reach the network. Tests of that report pass their own runner."""
    from debian_install_v2 import customscript
    monkeypatch.setattr(customscript, "_git_output", lambda argv, cwd=None: "")


@pytest.fixture(autouse=True)
def _reset_notify_breaker():
    """The Mattermost circuit breaker is per-process state; isolate tests."""
    from debian_install_v2 import notify
    notify.reset_breaker()
    yield
    notify.reset_breaker()
