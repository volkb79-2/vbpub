pytest_plugins = ["cli_extended.pytest_plugin"]


import pytest


@pytest.fixture(autouse=True)
def _reset_notify_breaker():
    """The Mattermost circuit breaker is per-process state; isolate tests."""
    from debian_install_v2 import notify
    notify.reset_breaker()
    yield
    notify.reset_breaker()
