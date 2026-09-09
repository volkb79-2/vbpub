"""Shared fixtures for scripts/netcup's pytest suite.

Both scp-api-install-host.py and scp-api-monitor-task.py are hyphenated
top-level scripts (not importable packages), so they're loaded by path via
importlib - same pattern as
debian-install-v2/debian_install_v2/tests/test_bootstrap_remote.py.
"""
from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

NETCUP_DIR = Path(__file__).resolve().parent.parent
INSTALL_HOST_PATH = NETCUP_DIR / "scp-api-install-host.py"
MONITOR_TASK_PATH = NETCUP_DIR / "scp-api-monitor-task.py"
EXPLORE_PATH = NETCUP_DIR / "scp-api-explore.py"


def _load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def install_host_mod():
    return _load_module(INSTALL_HOST_PATH, "scp_api_install_host")


@pytest.fixture()
def monitor_task_mod():
    return _load_module(MONITOR_TASK_PATH, "scp_api_monitor_task")


@pytest.fixture()
def explore_mod():
    return _load_module(EXPLORE_PATH, "scp_api_explore")


class FakeHTTPResponse:
    """Minimal stand-in for urllib.request's response context manager."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeClient:
    """Records calls instead of hitting the network - for --dry-run tests.

    Any of get/post/patch/put/delete/get_user_info raises AssertionError if called
    when the test didn't expect it (pass `allow=set()` to permit specific
    method names).
    """

    def __init__(self, *, get_responses=None, user_info=None, allow=("get", "get_user_info")):
        self.calls = []
        self._get_responses = list(get_responses or [])
        self._user_info = user_info or {"id": 1}
        self._allow = set(allow)

    def get(self, endpoint, params=None):
        if "get" not in self._allow:
            raise AssertionError(f"unexpected GET {endpoint}")
        self.calls.append(("get", endpoint, params))
        if self._get_responses:
            return self._get_responses.pop(0)
        return {}

    def post(self, endpoint, data):
        if "post" not in self._allow:
            raise AssertionError(f"unexpected mutating POST {endpoint} (dry-run must not call this)")
        self.calls.append(("post", endpoint, data))
        return {}

    def patch(self, endpoint, data, params=None):
        if "patch" not in self._allow:
            raise AssertionError(f"unexpected mutating PATCH {endpoint} (dry-run must not call this)")
        self.calls.append(("patch", endpoint, data, params))
        return {}

    def put(self, endpoint, data=None, params=None):
        if "put" not in self._allow:
            raise AssertionError(f"unexpected mutating PUT {endpoint} (dry-run/declined-confirm must not call this)")
        self.calls.append(("put", endpoint, data, params))
        return {}

    def delete(self, endpoint, params=None):
        if "delete" not in self._allow:
            raise AssertionError(f"unexpected mutating DELETE {endpoint} (dry-run/declined-confirm must not call this)")
        self.calls.append(("delete", endpoint, params))
        return {}

    def get_user_info(self):
        if "get_user_info" not in self._allow:
            raise AssertionError("unexpected get_user_info call")
        self.calls.append(("get_user_info",))
        return self._user_info


@pytest.fixture()
def fake_client():
    return FakeClient
