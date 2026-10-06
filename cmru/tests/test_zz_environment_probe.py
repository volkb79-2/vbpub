"""Process-environment hygiene for the whole suite (W3-PREP).

``conftest._restore_process_environment`` snapshots ``os.environ`` around every test.
The ``test_zz`` name sorts this file last in a normal run; the probe below also holds in
any order, because it checks the session-start baseline recorded by ``conftest`` rather
than relying on position.
"""
from __future__ import annotations

import os

from tests import conftest

_LEAK_NAMES = ("PYTHONUNBUFFERED",)


def _leaked(env) -> dict[str, str]:
    return {
        key: value for key, value in env.items()
        if key.startswith("CMRU_INTERNAL_") or key in _LEAK_NAMES
    }


def test_preserved_environ_undoes_additions_changes_and_removals(monkeypatch):
    monkeypatch.setenv("W3PREP_KEEP", "orig")
    monkeypatch.setenv("W3PREP_REMOVE", "x")
    monkeypatch.delenv("W3PREP_ADD", raising=False)
    snapshot = dict(os.environ)

    with conftest.preserved_environ():
        os.environ["W3PREP_ADD"] = "1"
        os.environ["W3PREP_KEEP"] = "changed"
        del os.environ["W3PREP_REMOVE"]
        os.environ["CMRU_INTERNAL_RUN_LOG"] = "/tmp/leak.log"
        os.environ["PYTHONUNBUFFERED"] = "1"

    assert dict(os.environ) == snapshot


def test_preserved_environ_restores_even_when_the_body_raises():
    before = dict(os.environ)
    try:
        with conftest.preserved_environ():
            os.environ["CMRU_INTERNAL_SHOW_RUN_DETAILS"] = "1"
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert dict(os.environ) == before


def test_zz_a_test_that_writes_the_environment_directly_does_not_leak_to_the_next():
    os.environ["CMRU_INTERNAL_LOG_PREFIX_TIME_SHORT"] = "1"
    os.environ["PYTHONUNBUFFERED"] = "1"


def test_zz_no_internal_name_or_pythonunbuffered_leaked_from_any_earlier_test():
    leaked = {
        key: value for key, value in _leaked(os.environ).items()
        if conftest.SESSION_START_ENVIRON.get(key) != value
    }
    assert not leaked, f"an earlier test leaked into the process environment: {leaked}"
