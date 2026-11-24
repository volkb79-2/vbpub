"""CIU-227 / CIU-230 — S6.5 preconditions are checked BEFORE the first hostdir.

Before this fix ``create_hostdirs`` created (and chowned) a directory and only
THEN let :func:`engine.privileged_fs_op` refuse for a missing
``CGROUP_PARENT_DEV_INTERACTIVE``, so the refusal left a hostdir the NEXT run
tripped ``[S6.3] Existing hostdir has incompatible ownership`` on. A `--dry-run`
created them too. CIU-230 is the refusal text: it must name the variable, the
expected value shape (a LOADED ``.slice`` unit name) and how to find one.

The process identity is always monkeypatched: a verdict may never depend on the
host's real uid/gid (an uid-1003 gate container and a root runner must agree).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402

FOREIGN_UID, FOREIGN_GID = 1000, 994   # ownership this test process cannot apply


def _config(hostdir: dict | None = None) -> dict:
    return {
        "deploy": {"env": {"shared": {
            "CONTAINER_UID": str(FOREIGN_UID), "DOCKER_GID": str(FOREIGN_GID),
        }}},
        "service": {"name": "api", "hostdir": hostdir or {"data": ""}},
    }


@pytest.fixture
def unprivileged(monkeypatch):
    """A non-root run whose target ownership is foreign, so the helper is needed."""
    monkeypatch.setattr(engine.os, "geteuid", lambda: 1003)
    monkeypatch.setattr(engine.os, "getegid", lambda: 1003)
    monkeypatch.setattr(engine.os, "getgroups", lambda: [27, 1003])


def test_ownership_need_is_derived_from_the_process_identity(monkeypatch):
    """The helper is needed exactly when this process cannot chown the target."""
    monkeypatch.setattr(engine.os, "geteuid", lambda: 1003)
    monkeypatch.setattr(engine.os, "getegid", lambda: 1003)
    monkeypatch.setattr(engine.os, "getgroups", lambda: [27, 1003])

    assert engine._hostdir_needs_privileged_ownership(1003, 1003) is False  # own
    assert engine._hostdir_needs_privileged_ownership(1003, 27) is False   # suppl. group
    assert engine._hostdir_needs_privileged_ownership(1003, 999) is True   # foreign gid
    assert engine._hostdir_needs_privileged_ownership(1000, 1003) is True  # foreign uid
    monkeypatch.setattr(engine.os, "geteuid", lambda: 0)
    assert engine._hostdir_needs_privileged_ownership(1000, 994) is False  # root


def test_refusal_creates_no_hostdir_and_the_next_run_succeeds(tmp_path, monkeypatch,
                                                             unprivileged):
    """(a)+(b): refuse, fix the precondition, run again — no manual cleanup.

    Also (a)'s dry-run half: the same unsatisfied precondition in ``plan_only``
    mode refuses and creates nothing.
    """
    monkeypatch.delenv("CGROUP_PARENT_DEV_INTERACTIVE", raising=False)
    target = tmp_path / "vol-api-data"

    for plan_only in (False, True):
        with pytest.raises(RuntimeError, match="CGROUP_PARENT_DEV_INTERACTIVE is required"):
            engine.create_hostdirs(
                _config(), tmp_path, repo_root=tmp_path, physical_root=tmp_path,
                plan_only=plan_only,
            )
        assert not target.exists()          # the refusal left NOTHING behind
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (True, "loaded"))
    chowned: list[tuple[int, int]] = []
    monkeypatch.setattr(engine.os, "chown", lambda _p, uid, gid: chowned.append((uid, gid)))

    engine.create_hostdirs(_config(), tmp_path, repo_root=tmp_path, physical_root=tmp_path)

    assert target.is_dir()
    assert chowned == [(FOREIGN_UID, FOREIGN_GID)]


def test_local_ownership_needs_no_cgroup_parent(tmp_path, monkeypatch, capsys):
    """A target ownership this process can apply itself needs no tier at all."""
    monkeypatch.setattr(engine.os, "geteuid", lambda: 1003)
    monkeypatch.setattr(engine.os, "getegid", lambda: 1003)
    monkeypatch.setattr(engine.os, "getgroups", lambda: [1003])
    monkeypatch.delenv("CGROUP_PARENT_DEV_INTERACTIVE", raising=False)
    monkeypatch.setattr(engine.os, "chown", lambda *_a: None)
    config = {
        "deploy": {"env": {"shared": {"CONTAINER_UID": "1003", "DOCKER_GID": "1003"}}},
        "service": {"name": "api", "hostdir": {"data": ""}},
    }

    engine.create_hostdirs(config, tmp_path, repo_root=tmp_path, physical_root=tmp_path)

    assert (tmp_path / "vol-api-data").is_dir()          # created, never refused
    assert "S6.5 root helper" not in capsys.readouterr().out


def test_plan_only_creates_no_hostdir_with_a_satisfiable_precondition(
    tmp_path, monkeypatch, capsys, unprivileged,
):
    """A satisfiable dry run still creates nothing, and reports what it would."""
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (True, "loaded"))
    monkeypatch.setattr(engine.os, "chown", lambda *_a: pytest.fail("a dry run must not chown"))
    monkeypatch.setattr(engine.os, "chmod", lambda *_a: pytest.fail("a dry run must not chmod"))
    (tmp_path / "seed-data").mkdir()

    config = engine.create_hostdirs(
        _config({"data": {"path": "", "seed": "seed-data"}}),
        tmp_path, repo_root=tmp_path, physical_root=tmp_path, plan_only=True,
    )

    target = tmp_path / "vol-api-data"
    assert not target.exists()
    assert config["service"]["hostdir"]["data"] == str(target.resolve())   # S6.2 rewrite
    out = capsys.readouterr().out
    assert f"[INFO]   Would create: {target} ({FOREIGN_UID}:{FOREIGN_GID}, 0o775)" in out
    assert "S6.5 root helper REQUIRED" in out


def test_plan_only_still_evaluates_the_seed_and_incompatible_refusals(
    tmp_path, monkeypatch, unprivileged,
):
    """A dry run proves the S6.6/S6.3 refusals a real run would hit."""
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(engine.governance, "check_slice_unit", lambda _name: (True, "loaded"))

    with pytest.raises(ValueError, match=r"\[S6\.6\] hostdir seed directory not found"):
        engine.create_hostdirs(
            _config({"data": {"path": "", "seed": "does-not-exist"}}),
            tmp_path, repo_root=tmp_path, physical_root=tmp_path, plan_only=True,
        )

    foreign = tmp_path / "vol-api-data"
    foreign.mkdir(mode=0o700)
    with pytest.raises(ValueError, match=r"\[S6\.3\] Existing hostdir has incompatible"):
        engine.create_hostdirs(
            _config(), tmp_path, repo_root=tmp_path, physical_root=tmp_path, plan_only=True,
        )
    assert foreign.is_dir()                                  # untouched


def test_ciu_230_every_refusal_names_the_variable_shape_and_remedy(monkeypatch):
    """CIU-230: each refusal carries all three remedy facts, including the finder.

    The negative control runs inline: the last block asserts that a message
    WITHOUT ``systemctl list-units --type=slice`` fails exactly these checks,
    so the oracle is sensitive to the text and not to \"some RuntimeError\".
    """
    remedy = "systemctl list-units --type=slice"

    def assert_full_remedy(message: str) -> None:
        assert "CGROUP_PARENT_DEV_INTERACTIVE" in message
        assert ".slice" in message
        assert remedy in message

    monkeypatch.delenv("CGROUP_PARENT_DEV_INTERACTIVE", raising=False)
    with pytest.raises(RuntimeError) as unset:
        engine._root_helper_cgroup_parent()
    assert_full_remedy(str(unset.value))

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "not/a.slice")
    with pytest.raises(RuntimeError) as malformed:
        engine._root_helper_cgroup_parent()
    assert_full_remedy(str(malformed.value))

    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(engine.governance, "check_slice_unit",
                        lambda _name: (False, "not-found"))
    with pytest.raises(RuntimeError, match="not verified loaded") as not_loaded:
        engine._root_helper_cgroup_parent()
    assert_full_remedy(str(not_loaded.value))

    bare = RuntimeError("CGROUP_PARENT_DEV_INTERACTIVE is required for the S6.5 root helper")
    with pytest.raises(AssertionError):
        assert_full_remedy(str(bare))                        # controlled-wrong message


def test_up_dry_run_reaches_step_8_in_plan_only_mode(monkeypatch, tmp_path):
    """The Step-8 wiring: the mode follows the run kind, not a constant.

    ``prepare_stack`` (a render for a REAL start) still creates its hostdirs;
    that is pinned by ``test_ciu_engine_execution_boundaries.py::
    test_prepare_stack_creates_declared_hostdirs_by_default``.
    """
    from tests.test_ciu_engine_up_dry_run_pin import _stub_pipeline

    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "compose.yml").write_text("services:\n  api:\n    image: x\n", encoding="utf-8")
    _stub_pipeline(monkeypatch)
    seen: list[bool] = []
    monkeypatch.setattr(
        engine, "create_hostdirs",
        lambda _config, _dir, *, repo_root, plan_only=False: seen.append(plan_only),
    )

    engine.main_execution(
        stack, compose_file="compose.yml", dry_run=True, define_root=tmp_path,
        skip_hooks=True, skip_secrets=True,
    )
    assert seen == [True]
