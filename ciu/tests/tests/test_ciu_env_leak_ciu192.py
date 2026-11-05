"""CIU-192 — a leaked/empty process environment must not flake the gate.

Release-gate flake: `Missing required workspace environment variables:
CONTAINER_UID` in only some runs. Root cause, in two halves pinned here:

1. product: `generate_ciu_env` read `USER_UID`/`CONTAINER_UID`/... with
   `os.environ.get(key, default)`, so an exported EMPTY value (left behind by
   an earlier test's bootstrap of a facts file whose `user_uid` is "") was
   persisted as `container_uid = ""`, which `ensure_workspace_env` rejects.
2. suite: nothing restored `os.environ` between tests, so which tests saw such
   a value depended on which file the xdist worker ran before them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402

from test_ciu_workspace_env import _hermetic_generate  # noqa: E402

_ID_KEYS = ("USER_UID", "USER_GID", "DOCKER_UID", "CONTAINER_UID", "CONTAINER_GID")


@pytest.mark.parametrize("empty", _ID_KEYS)
def test_generate_treats_an_empty_id_export_as_unset(monkeypatch, tmp_path, empty):
    for key in _ID_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(empty, "")

    _hermetic_generate(monkeypatch, tmp_path)

    machine = workspace_env.read_generated_machine_facts(tmp_path)
    for fact in ("user_uid", "user_gid", "docker_uid", "container_uid", "container_gid"):
        assert machine[fact] != "", fact
    assert machine["container_uid"] == str(os.getuid())


def test_zero_is_still_a_valid_id(monkeypatch, tmp_path):
    monkeypatch.setenv("USER_UID", "0")
    monkeypatch.delenv("CONTAINER_UID", raising=False)
    _hermetic_generate(monkeypatch, tmp_path)
    assert workspace_env.read_generated_machine_facts(tmp_path)["container_uid"] == "0"


_LEAK_KEY = "CIU192_LEAK_PROBE"


_before_part_1: dict[str, str] = {}


def test_environment_leak_part_1_writes_the_process_environment():
    _before_part_1.update(os.environ)
    os.environ[_LEAK_KEY] = "leaked"
    os.environ["USER_UID"] = "leaked-empty-or-not"


def test_environment_leak_part_2_sees_the_pre_part_1_environment():
    # Same file, same worker, directly after part 1: on the old suite this saw
    # both writes.
    assert _LEAK_KEY not in os.environ
    assert os.environ.get("USER_UID") == _before_part_1.get("USER_UID")
