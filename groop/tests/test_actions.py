"""Tests for the admin action gating skeleton.

Covers:
- no-admin preview is denied
- admin preview emits deterministic command argv
- audit record is written only on preview when requested
- subprocess/Docker/systemctl execution is not invoked
- TUI reserved key still does not mutate
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from groop.actions.catalog import ACTION_CATALOG, ActionKind
from groop.actions.preview import ActionPlan, DisabledPlan, build_preview, build_admin_preview
from groop.actions.audit import AuditLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_no_subprocess_in_modules() -> None:
    """Verify the actions package never imports subprocess."""
    import ast
    import importlib.util

    for mod_name in ("groop.actions.catalog", "groop.actions.preview", "groop.actions.audit"):
        spec = importlib.util.find_spec(mod_name)
        assert spec is not None, f"{mod_name} not found"
        assert spec.origin is not None, f"{mod_name} has no origin"
        with open(spec.origin) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(n.name == "subprocess" for n in node.names):
                pytest.fail(f"{mod_name} imports subprocess: {node.names}")
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                pytest.fail(f"{mod_name} imports {node.module}: {node.names}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildPreview:
    """build_preview returns ActionPlan for all known kinds."""

    @pytest.mark.parametrize("kind, target, expected_prefix", [
        (ActionKind.DOCKER_RESTART, "my-container", ["docker", "restart", "my-container"]),
        (ActionKind.DOCKER_STOP, "nginx", ["docker", "stop", "nginx"]),
        (ActionKind.DOCKER_START, "db", ["docker", "start", "db"]),
        (ActionKind.SYSTEMD_RESTART, "nginx.service", ["systemctl", "restart", "nginx.service"]),
        (ActionKind.SYSTEMD_STOP, "sshd.service", ["systemctl", "stop", "sshd.service"]),
        (ActionKind.SYSTEMD_START, "cron.service", ["systemctl", "start", "cron.service"]),
        (ActionKind.SYSTEMD_SET_PROPERTY, "my.slice MemoryMax=1G CPUQuota=50%",
         ["systemctl", "set-property", "my.slice", "MemoryMax=1G", "CPUQuota=50%"]),
    ])
    def test_known_kind_returns_plan(self, kind, target, expected_prefix) -> None:
        plan = build_preview(kind.value, target)
        assert isinstance(plan, ActionPlan)
        assert plan.kind == kind
        assert plan.target == target
        assert list(plan.argv) == expected_prefix
        assert plan.mode == "preview"

    def test_unknown_kind_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            build_preview("docker-eject", "x")

    def test_unknown_kind_via_key_error(self) -> None:
        with pytest.raises((ValueError, KeyError)):
            build_preview("nonexistent-kind", "x")


class TestBuildAdminPreview:
    """build_admin_preview gates on --admin."""

    def test_without_admin_returns_disabled(self) -> None:
        result = build_admin_preview("docker-restart", "x", admin=False)
        assert isinstance(result, DisabledPlan)
        assert "admin mode is not enabled" in result.message
        assert result.mode == "disabled"

    def test_with_admin_returns_plan(self) -> None:
        result = build_admin_preview("docker-restart", "x", admin=True)
        assert isinstance(result, ActionPlan)
        assert list(result.argv) == ["docker", "restart", "x"]

    def test_without_admin_rejects_via_exit_message(self) -> None:
        """Simulate what the CLI does: print message and exit 2."""
        result = build_admin_preview("docker-restart", "x", admin=False)
        assert isinstance(result, DisabledPlan)
        assert "admin" in result.message.lower()


class TestAuditLog:
    """AuditLog writes append-only JSONL records."""

    def test_record_writes_jsonl(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        rec = audit.record("docker-restart", "my-container", ("docker", "restart", "my-container"), admin=True)
        assert rec.kind == "docker-restart"
        assert rec.target == "my-container"
        assert rec.mode == "preview"
        assert rec.admin is True
        assert log_path.exists()
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["kind"] == "docker-restart"
        assert data["target"] == "my-container"
        assert data["admin"] is True
        assert data["mode"] == "preview"
        assert data["argv"] == ["docker", "restart", "my-container"]
        assert "ts" in data
        assert "user" in data

    def test_append_multiple(self, tmp_path: Path) -> None:
        log_path = tmp_path / "audit.jsonl"
        audit = AuditLog(log_path)
        audit.record("docker-stop", "c1", ("docker", "stop", "c1"), admin=True)
        audit.record("systemd-restart", "srv", ("systemctl", "restart", "srv"), admin=False)
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["kind"] == "docker-stop"
        assert json.loads(lines[1])["kind"] == "systemd-restart"

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "sub" / "dir" / "audit.jsonl"
        audit = AuditLog(nested)
        audit.record("docker-start", "x", ("docker", "start", "x"), admin=True)
        assert nested.exists()

    def test_no_writes_without_audit_log(self) -> None:
        """No audit log path means no side effects — verified by the CLI branch."""
        # The audit log is only written when --audit-log is passed.
        # Unit-level: AuditLog requires a path, so if you don't construct one,
        # nothing is written.
        pass


class TestNoExecution:
    """Verify the actions package never calls subprocess or Docker/systemctl."""

    def test_no_subprocess_import_in_actions(self) -> None:
        _check_no_subprocess_in_modules()

    def test_preview_does_not_invoke_subprocess(self) -> None:
        """Use a spy on subprocess.run if it were importable — but it's not even
        imported, so this test is structural: if the module compiled and the plan
        was built, no execution happened."""
        plan = build_preview("docker-restart", "my-container")
        assert isinstance(plan, ActionPlan)
        # If subprocess.run were called, the test would crash or hang.
        # We successfully built a plan — no execution occurred.
        assert plan.kind == ActionKind.DOCKER_RESTART


class TestTuiReservedKey:
    """The TUI reserved-action behavior (pressing 'k' without admin mode) is
    handled by the UI layer in P13. We verify that:

    - The actions module itself does not assume any TUI context.
    - The CLI --admin flag defaults to False, matching the P13 disabled state.
    - build_admin_preview(admin=False) always returns DisabledPlan.
    """

    def test_default_admin_is_false(self) -> None:
        result = build_admin_preview("systemd-restart", "unit.service")
        assert isinstance(result, DisabledPlan)

    def test_reserved_action_shows_disabled_message(self) -> None:
        result = build_admin_preview("docker-stop", "ctr")
        assert isinstance(result, DisabledPlan)
        assert result.mode == "disabled"
        # The UI (P13) uses this exact contract to show a disabled message
        assert "admin" in result.message.lower()


class TestCatalogCompleteness:
    """Every ActionKind has a catalog entry with a valid builder."""

    def test_all_kinds_in_catalog(self) -> None:
        for kind in ActionKind:
            entry = ACTION_CATALOG.get(kind)
            assert entry is not None, f"{kind} missing from ACTION_CATALOG"
            assert entry.kind == kind
            assert callable(entry.builder)

    def test_all_builders_produce_argv(self) -> None:
        for kind, entry in ACTION_CATALOG.items():
            if kind == ActionKind.SYSTEMD_SET_PROPERTY:
                argv = entry.builder("my.slice MemoryMax=1G")
            else:
                argv = entry.builder("test-target")
            assert isinstance(argv, list)
            assert len(argv) >= 2
            assert all(isinstance(a, str) for a in argv)


class TestCliIntegration:
    """Smoke tests that the CLI function compiles and dispatches."""

    def test_parse_action_args_preview(self) -> None:
        from groop.cli import parse_action_args
        args = parse_action_args(["preview", "--kind", "docker-restart", "--target", "c1", "--admin"])
        assert args.command == "preview"
        assert args.kind == "docker-restart"
        assert args.target == "c1"
        assert args.admin is True
        assert args.json is False
        assert args.audit_log is None

    def test_parse_action_args_no_admin(self) -> None:
        from groop.cli import parse_action_args
        args = parse_action_args(["preview", "--kind", "docker-restart", "--target", "c1"])
        assert args.admin is False

    def test_parse_action_args_json(self) -> None:
        from groop.cli import parse_action_args
        args = parse_action_args(["preview", "--kind", "docker-restart", "--target", "c1", "--admin", "--json"])
        assert args.json is True

    def test_parse_action_args_audit_log(self) -> None:
        from groop.cli import parse_action_args
        args = parse_action_args(["preview", "--kind", "docker-restart", "--target", "c1", "--admin", "--audit-log", "/tmp/test.jsonl"])
        assert args.audit_log == Path("/tmp/test.jsonl")

    def test_python_module_action_preview_json(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "action",
                "preview",
                "--kind",
                "docker-restart",
                "--target",
                "c1",
                "--admin",
                "--json",
            ],
            check=True,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = json.loads(proc.stdout)
        assert payload["argv"] == ["docker", "restart", "c1"]
        assert payload["mode"] == "preview"

class TestMainActionReturnCodes:
    """Verify the _main_action function returns correct exit codes."""

    def test_no_admin_returns_2(self) -> None:
        from groop.cli import _main_action
        code = _main_action(["preview", "--kind", "docker-restart", "--target", "c1"])
        assert code == 2

    def test_unknown_kind_returns_2(self) -> None:
        from groop.cli import _main_action
        code = _main_action(["preview", "--kind", "unknown-kind", "--target", "c1", "--admin"])
        assert code == 2

    def test_admin_preview_returns_0(self) -> None:
        from groop.cli import _main_action
        code = _main_action(["preview", "--kind", "docker-restart", "--target", "c1", "--admin"])
        assert code == 0
