#!/usr/bin/env python3
"""Dual-shipping + ``--shipped`` passthrough tests (SPEC S8.5 / S8.6 / S7.2).

Covers:
- ``phases.service_shipped`` validation (S8.6 / S7.2): default false, bool, abort on non-bool.
- ``engine.run_shipped`` passthrough (S8.6): runs a hand-written compose with no
  CIU stack config, no secret/overlay steps; dry-run skips compose up.
- No-clobber (S8.5): a CIU run renders ``ciu.compose.yml`` and never touches a
  committed ``docker-compose.yml`` in the same dir.
- ``reset_service`` keeps the shipped ``docker-compose.yml`` (S8.5/S6.4).
- ``deploy._run_stack`` routes ``shipped=True`` to ``engine.run_shipped``.

All engine runs use ``--dry-run`` + a monkeypatched network step + the DooD
preflight skip, so no Docker daemon is required (mirrors test_ciu_hooks_execution).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import deploy, engine  # noqa: E402
from ciu.config_constants import CIU_COMPOSE_OUTPUT, SHIPPED_COMPOSE  # noqa: E402
from ciu.deploy_pkg import phases  # noqa: E402


# ---------------------------------------------------------------------------
# phases.service_shipped (S8.6 / S7.2)
# ---------------------------------------------------------------------------

class TestServiceShipped:
    def test_absent_defaults_false(self):
        assert phases.service_shipped({"path": "x", "name": "x"}) is False

    def test_true_passes_through(self):
        assert phases.service_shipped({"shipped": True}) is True

    def test_false_passes_through(self):
        assert phases.service_shipped({"shipped": False}) is False

    @pytest.mark.parametrize("bad", ["true", 1, [], {"a": 1}])
    def test_non_bool_aborts_s7_2(self, bad):
        with pytest.raises(ValueError, match=r"\[S7.2\].*shipped"):
            phases.service_shipped({"shipped": bad})


# ---------------------------------------------------------------------------
# Shared fixture helpers (mirror test_ciu_hooks_execution)
# ---------------------------------------------------------------------------

GLOBAL_DEFAULTS = """\
[ciu]
require_fqdn = false
require_certs = false
auto_connect_network = false

[deploy]
project_name = "shipped-test"
environment_tag = "test"
log_level = "INFO"

[deploy.env.shared]
CONTAINER_UID = "$CONTAINER_UID"
CONTAINER_GID = "$CONTAINER_GID"
DOCKER_GID = "$DOCKER_GID"
"""

SHIPPED_COMPOSE_BODY = """\
services:
  legacy:
    image: alpine:3
    command: ["sh", "-c", "echo legacy; sleep 1"]
"""


def _set_env(tmp_path: Path) -> None:
    os.environ["REPO_ROOT"] = str(tmp_path)
    os.environ["PHYSICAL_REPO_ROOT"] = str(tmp_path)
    os.environ["DOCKER_NETWORK_INTERNAL"] = "shipped-test-net"
    os.environ["CONTAINER_UID"] = "1000"
    os.environ["CONTAINER_GID"] = "1000"
    os.environ["DOCKER_GID"] = "999"
    os.environ["SKIP_DEPENDENCY_CHECK"] = "1"
    os.environ["CIU_SKIP_DOOD_PREFLIGHT"] = "1"


def _write_env_file(tmp_path: Path) -> None:
    (tmp_path / "ciu.env").write_text(
        "\n".join(
            f'export {k}="{os.environ[k]}"'
            for k in (
                "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
                "CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID",
            )
        )
        + "\n"
    )
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(GLOBAL_DEFAULTS)
    (tmp_path / ".gitignore").write_text("**/.ciu/\n")


# ---------------------------------------------------------------------------
# engine.run_shipped (S8.6)
# ---------------------------------------------------------------------------

class TestRunShipped:
    def test_dry_run_passthrough_no_ciu_config(self, tmp_path, monkeypatch):
        """A stack with ONLY a hand-written docker-compose.yml runs via --shipped;
        no ciu.defaults.toml.j2, no overlay/secret artifacts produced (S8.6)."""
        _set_env(tmp_path)
        _write_env_file(tmp_path)
        stack = tmp_path / "vendor" / "legacy"
        stack.mkdir(parents=True)
        shipped = stack / SHIPPED_COMPOSE
        shipped.write_text(SHIPPED_COMPOSE_BODY)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)

        result = engine.run_shipped(stack, dry_run=True, define_root=tmp_path)

        assert result.get("status") == "success"
        assert result.get("shipped") is True
        # No CIU rendering happened: shipped file untouched, no ciu.compose.yml,
        # no machine-owned .ciu/ dir.
        assert shipped.read_text() == SHIPPED_COMPOSE_BODY
        assert not (stack / CIU_COMPOSE_OUTPUT).exists()
        assert not (stack / ".ciu").exists()

    def test_missing_shipped_file_aborts(self, tmp_path, monkeypatch):
        _set_env(tmp_path)
        _write_env_file(tmp_path)
        stack = tmp_path / "vendor" / "empty"
        stack.mkdir(parents=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)

        with pytest.raises(FileNotFoundError, match="pre-shipped compose"):
            engine.run_shipped(stack, dry_run=True, define_root=tmp_path)

    def test_custom_compose_filename(self, tmp_path, monkeypatch):
        _set_env(tmp_path)
        _write_env_file(tmp_path)
        stack = tmp_path / "vendor" / "legacy"
        stack.mkdir(parents=True)
        (stack / "compose.legacy.yml").write_text(SHIPPED_COMPOSE_BODY)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)

        result = engine.run_shipped(
            stack, compose_file="compose.legacy.yml", dry_run=True, define_root=tmp_path
        )
        assert result.get("status") == "success"


# ---------------------------------------------------------------------------
# No-clobber: CIU path never overwrites a shipped docker-compose.yml (S8.5)
# ---------------------------------------------------------------------------

STACK_DEFAULTS = """\
[demo]
name = "demo"
image = "alpine:3"
"""

CIU_COMPOSE_TMPL = """\
services:
  {{ demo.name }}:
    image: {{ demo.image }}
"""


class TestNoClobber:
    def test_ciu_run_leaves_shipped_compose_untouched(self, tmp_path, monkeypatch):
        _set_env(tmp_path)
        _write_env_file(tmp_path)
        stack = tmp_path / "applications" / "demo"
        stack.mkdir(parents=True)
        (stack / "ciu.defaults.toml.j2").write_text(STACK_DEFAULTS)
        (stack / "ciu.compose.yml.j2").write_text(CIU_COMPOSE_TMPL)
        shipped = stack / SHIPPED_COMPOSE
        shipped.write_text(SHIPPED_COMPOSE_BODY)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)

        result = engine.main_execution(
            working_dir=stack, dry_run=True, define_root=tmp_path, skip_hostdir_check=True,
        )

        assert result.get("status") == "success"
        # CIU rendered its OWN compose; the hand-written one is byte-identical.
        rendered = (stack / CIU_COMPOSE_OUTPUT).read_text()
        assert "image: alpine:3" in rendered
        assert shipped.read_text() == SHIPPED_COMPOSE_BODY


# ---------------------------------------------------------------------------
# reset_service keeps the shipped compose (S8.5 / S6.4)
# ---------------------------------------------------------------------------

class TestResetKeepsShipped:
    def test_reset_removes_ciu_compose_keeps_docker_compose(self, tmp_path):
        config = {"deploy": {"project_name": "p", "labels": {"prefix": "dstdns"}}}
        (tmp_path / CIU_COMPOSE_OUTPUT).write_text("services: {}\n")
        (tmp_path / SHIPPED_COMPOSE).write_text(SHIPPED_COMPOSE_BODY)

        ok = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(engine.procutil, "run_cmd", return_value=ok):
            engine.reset_service(config, tmp_path, assume_yes=True)

        assert not (tmp_path / CIU_COMPOSE_OUTPUT).exists()  # CIU output removed
        assert (tmp_path / SHIPPED_COMPOSE).exists()         # shipped file kept (S8.5)
        assert (tmp_path / SHIPPED_COMPOSE).read_text() == SHIPPED_COMPOSE_BODY


# ---------------------------------------------------------------------------
# deploy._run_stack routes shipped=True to engine.run_shipped (S8.6)
# ---------------------------------------------------------------------------

class TestDeployRouting:
    def test_run_stack_shipped_calls_run_shipped(self, tmp_path, monkeypatch):
        stack = tmp_path / "vendor" / "legacy"
        stack.mkdir(parents=True)
        called = {"shipped": False, "native": False}

        def fake_shipped(**kw):
            called["shipped"] = True
            return {"status": "success"}

        def fake_native(**kw):
            called["native"] = True
            return {"status": "success"}

        monkeypatch.setattr(deploy.engine, "run_shipped", fake_shipped)
        monkeypatch.setattr(deploy.engine, "main_execution", fake_native)

        ok = deploy._run_stack(
            stack, env={}, compose_profiles=[], dry_run=True,
            update_cert_permission=False, shipped=True,
        )
        assert ok is True
        assert called["shipped"] is True
        assert called["native"] is False

    def test_run_stack_native_calls_main_execution(self, tmp_path, monkeypatch):
        stack = tmp_path / "applications" / "demo"
        stack.mkdir(parents=True)
        called = {"shipped": False, "native": False}

        monkeypatch.setattr(deploy.engine, "run_shipped",
                            lambda **kw: called.__setitem__("shipped", True) or {"status": "success"})
        monkeypatch.setattr(deploy.engine, "main_execution",
                            lambda **kw: called.__setitem__("native", True) or {"status": "success"})

        ok = deploy._run_stack(
            stack, env={}, compose_profiles=[], dry_run=True,
            update_cert_permission=False, shipped=False,
        )
        assert ok is True
        assert called["native"] is True
        assert called["shipped"] is False
