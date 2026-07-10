#!/usr/bin/env python3
"""CIU v2 create_hostdirs() tests (S6).

v2 signature:
    create_hostdirs(config, stack_dir, *, repo_root, physical_root=None, chown_fn=None)

Key v2 behaviours pinned here:
- S6.2: every hostdir value is rewritten to its ABSOLUTE PHYSICAL path string.
- S6.1: "" auto-generates ``<stack>/vol-<service-name>-<purpose>``; non-empty
  relative paths resolve against the stack dir; inline tables override uid/mode.
- S6.6: ``seed`` copies a tree on first creation only.
- S6.3: pre-existing incompatible ownership aborts (we inject a chown_fn and a
  fake stat to drive the incompatible path).

REPO_ROOT == PHYSICAL_REPO_ROOT == tmp so physical == logical (identity map).
"""

import os
import stat as stat_mod
from pathlib import Path
from unittest.mock import patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu.engine import create_hostdirs  # noqa: E402
from ciu.composefile import guard_config, render_compose  # noqa: E402


def _base_config() -> dict:
    return {
        "deploy": {
            "env": {
                "shared": {
                    "CONTAINER_UID": "1000",
                    "CONTAINER_GID": "1000",
                    "DOCKER_GID": "994",
                }
            }
        }
    }


def test_rewrites_value_to_absolute_physical_path(tmp_path):
    config = _base_config()
    config["service"] = {
        "name": "demo-service",
        "hostdir": {"data": "vol-x", "logs": ""},
    }

    calls = []
    create_hostdirs(
        config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
        chown_fn=lambda p, u, g: calls.append((str(p), u, g)),
    )

    data_val = config["service"]["hostdir"]["data"]
    logs_val = config["service"]["hostdir"]["logs"]
    # Absolute physical path strings (S6.2), resolved under the stack dir.
    assert data_val == str((tmp_path / "vol-x").resolve())
    assert logs_val == str((tmp_path / "vol-demo-service-logs").resolve())
    assert (tmp_path / "vol-x").is_dir()
    assert (tmp_path / "vol-demo-service-logs").is_dir()
    # Default ownership applied (1000:994) via injected chown_fn.
    assert all(u == 1000 and g == 994 for _, u, g in calls)


def test_generates_missing_hostdir_paths(tmp_path):
    config = _base_config()
    config["service"] = {"name": "demo-service", "hostdir": {"data": "", "logs": ""}}

    create_hostdirs(config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
                    chown_fn=lambda *a: None)

    assert config["service"]["hostdir"]["data"] == str((tmp_path / "vol-demo-service-data").resolve())
    assert config["service"]["hostdir"]["logs"] == str((tmp_path / "vol-demo-service-logs").resolve())


def test_inline_table_uid_and_mode(tmp_path):
    config = _base_config()
    config["service"] = {
        "name": "pg",
        "hostdir": {"data": {"path": "", "uid": 999, "gid": 994, "mode": "0770"}},
    }

    seen = {}
    create_hostdirs(
        config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
        chown_fn=lambda p, u, g: seen.update({"uid": u, "gid": g}),
    )

    created = tmp_path / "vol-pg-data"
    assert created.is_dir()
    assert seen == {"uid": 999, "gid": 994}
    # mode 0770 honoured on creation.
    assert stat_mod.S_IMODE(created.stat().st_mode) == 0o770


def test_seed_copies_tree_on_first_creation(tmp_path):
    seed_src = tmp_path / "seed-data"
    seed_src.mkdir()
    (seed_src / "bootstrap.cfg").write_text("hello")

    config = _base_config()
    config["service"] = {
        "name": "svc",
        "hostdir": {"data": {"path": "", "seed": "seed-data"}},
    }

    create_hostdirs(config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
                    chown_fn=lambda *a: None)

    seeded = tmp_path / "vol-svc-data" / "bootstrap.cfg"
    assert seeded.exists()
    assert seeded.read_text() == "hello"


def test_incompatible_ownership_aborts(tmp_path):
    config = _base_config()
    existing = tmp_path / "vol-svc-data"
    existing.mkdir(mode=0o700)
    config["service"] = {"name": "svc", "hostdir": {"data": "vol-svc-data"}}

    # Fake a stat reporting a foreign owner/group and a private mode so the
    # compatibility check (S6.3) fails.
    real_stat = Path.stat

    class FakeStat:
        st_uid = 4242
        st_gid = 4242
        st_mode = stat_mod.S_IFDIR | 0o700

    def fake_stat(self, *a, **k):
        if self == existing:
            return FakeStat()
        return real_stat(self, *a, **k)

    with patch.object(Path, "stat", fake_stat):
        with pytest.raises(ValueError, match=r"\[S6.3\].*incompatible"):
            create_hostdirs(config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
                            chown_fn=lambda *a: None)


def test_requires_service_name_for_auto_hostdir(tmp_path):
    config = _base_config()
    config["service"] = {"hostdir": {"data": ""}}

    with pytest.raises(ValueError, match="hostdir section found without service name"):
        create_hostdirs(config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
                        chown_fn=lambda *a: None)


def test_requires_deploy_shared_values(tmp_path):
    with pytest.raises(ValueError, match="CONTAINER_UID/DOCKER_GID"):
        create_hostdirs({}, tmp_path, repo_root=tmp_path, physical_root=tmp_path)


def test_gid_zero_is_valid(tmp_path):
    """B7 / S2.5: GID 0 is a valid default, not falsy-replaced."""
    config = {
        "deploy": {"env": {"shared": {"CONTAINER_UID": "0", "DOCKER_GID": "0"}}}
    }
    config["service"] = {"name": "svc", "hostdir": {"data": ""}}

    seen = {}
    create_hostdirs(config, tmp_path, repo_root=tmp_path, physical_root=tmp_path,
                    chown_fn=lambda p, u, g: seen.update({"uid": u, "gid": g}))

    assert seen == {"uid": 0, "gid": 0}


class TestCIU9HostdirRewriteFeedsRender:
    """CIU-9 open question: does create_hostdirs' in-place S6.2 physical-path
    rewrite actually reach Jinja compose rendering, or does render_compose read
    an earlier-captured (pre-rewrite, still-logical) copy of the config?

    Code-reading trace (engine.py ``main_execution``, steps 8→13): step 8 calls
    ``create_hostdirs(merged, working_dir, repo_root=repo_root)`` — this mutates
    the nested ``hostdir`` dict *in place* inside ``merged`` (no copy is made
    anywhere in ``create_hostdirs``/``_scan_section``); ``merged`` is not
    reassigned again before step 13's ``composefile.guard_config(merged, specs)``,
    which deep-copies ``merged`` (see ``composefile._replace_entries``) — i.e.
    it deep-copies the config only AFTER the S6.2 rewrite already happened, so
    the copy it hands to ``render_compose`` carries the physical paths. This
    test pins that mechanism directly (create_hostdirs -> guard_config ->
    render_compose, DooD-style ``repo_root != physical_root``) without needing
    the full CLI pipeline, and passing it confirms the open question:
    create_hostdirs' rewrite DOES feed template rendering in the current code —
    it is not a second live bug. See KNOWN_ISSUES_TODO_BACKLOG.md CIU-9.
    """

    def test_physical_path_reaches_rendered_compose_in_dood(self, tmp_path):
        repo_root = tmp_path / "workspace"
        physical_root = tmp_path / "physical"
        stack_dir = repo_root / "infra" / "demo-service"
        stack_dir.mkdir(parents=True)
        (physical_root / "infra" / "demo-service").mkdir(parents=True)

        config = _base_config()
        config["service"] = {"name": "demo-service", "hostdir": {"data": ""}}

        create_hostdirs(
            config, stack_dir, repo_root=repo_root, physical_root=physical_root,
            chown_fn=lambda *a: None,
        )

        logical_path = str((stack_dir / "vol-demo-service-data").resolve())
        physical_path = config["service"]["hostdir"]["data"]
        assert physical_path != logical_path
        assert physical_path.startswith(str(physical_root))

        # Downstream pipeline (engine.py steps 12-13): guard_config deep-copies
        # the ALREADY-mutated config, then render_compose spreads it into the
        # Jinja context — mirrors the real call sequence, no CLI needed.
        guarded = guard_config(config, specs=[])
        template = tmp_path / "ciu.compose.yml.j2"
        template.write_text("volumes:\n  - {{ service.hostdir.data }}:/data\n")
        rendered = render_compose(template, guarded)

        assert physical_path in rendered
        assert logical_path not in rendered
