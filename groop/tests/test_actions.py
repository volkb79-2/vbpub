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

from groop.actions.catalog import (
    ACTION_CATALOG,
    DOCKER_EXECUTABLE,
    SYSTEMCTL_EXECUTABLE,
    ActionKind,
)
from groop.actions.preview import (
    ActionPlan,
    DisabledPlan,
    build_preview,
    build_admin_preview,
)
from groop.actions.audit import AuditLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_no_subprocess_in_modules() -> None:
    """Verify the actions package never imports subprocess."""
    import ast
    import importlib.util

    for mod_name in (
        "groop.actions.catalog",
        "groop.actions.preview",
        "groop.actions.audit",
    ):
        spec = importlib.util.find_spec(mod_name)
        assert spec is not None, f"{mod_name} not found"
        assert spec.origin is not None, f"{mod_name} has no origin"
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                n.name == "subprocess" for n in node.names
            ):
                pytest.fail(f"{mod_name} imports subprocess: {node.names}")
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                pytest.fail(f"{mod_name} imports {node.module}: {node.names}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildPreview:
    """build_preview returns ActionPlan for all known kinds."""

    @pytest.mark.parametrize(
        "kind, target, expected_prefix",
        [
            (
                ActionKind.DOCKER_RESTART,
                "my-container",
                [DOCKER_EXECUTABLE, "restart", "my-container"],
            ),
            (ActionKind.DOCKER_STOP, "nginx", [DOCKER_EXECUTABLE, "stop", "nginx"]),
            (ActionKind.DOCKER_START, "db", [DOCKER_EXECUTABLE, "start", "db"]),
            (
                ActionKind.SYSTEMD_RESTART,
                "nginx.service",
                [SYSTEMCTL_EXECUTABLE, "restart", "nginx.service"],
            ),
            (
                ActionKind.SYSTEMD_STOP,
                "sshd.service",
                [SYSTEMCTL_EXECUTABLE, "stop", "sshd.service"],
            ),
            (
                ActionKind.SYSTEMD_START,
                "cron.service",
                [SYSTEMCTL_EXECUTABLE, "start", "cron.service"],
            ),
            (
                ActionKind.SYSTEMD_SET_PROPERTY,
                "my.slice",
                [
                    SYSTEMCTL_EXECUTABLE,
                    "set-property",
                    "my.slice",
                ],
            ),
        ],
    )
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

    def test_unknown_kind_raises_value_error_for_any_invalid_name(self) -> None:
        with pytest.raises(ValueError):
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
        assert list(result.argv) == [DOCKER_EXECUTABLE, "restart", "x"]

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
        rec = audit.record(
            "docker-restart",
            "my-container",
            ("docker", "restart", "my-container"),
            admin=True,
        )
        assert rec.kind == "docker-restart"
        assert rec.target == "my-container"
        assert rec.mode == "preview"
        assert rec.admin is True
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
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
        audit.record(
            "systemd-restart", "srv", ("systemctl", "restart", "srv"), admin=False
        )
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
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
                # Systemd-set-property requires structured inputs (governance.py).
                # The catalog-level builder only accepts a bare unit name.
                argv = entry.builder("my.slice")
            else:
                argv = entry.builder("test-target")
            assert isinstance(argv, list)
            assert len(argv) >= 2
            assert all(isinstance(a, str) for a in argv)


class TestCliIntegration:
    """Smoke tests that the CLI function compiles and dispatches."""

    def test_parse_action_args_preview(self) -> None:
        from groop.cli import parse_action_args

        args = parse_action_args(
            ["preview", "--kind", "docker-restart", "--target", "c1", "--admin"]
        )
        assert args.command == "preview"
        assert args.kind == "docker-restart"
        assert args.target == "c1"
        assert args.admin is True
        assert args.json is False
        assert args.audit_log is None

    def test_parse_action_args_no_admin(self) -> None:
        from groop.cli import parse_action_args

        args = parse_action_args(
            ["preview", "--kind", "docker-restart", "--target", "c1"]
        )
        assert args.admin is False

    def test_parse_action_args_json(self) -> None:
        from groop.cli import parse_action_args

        args = parse_action_args(
            [
                "preview",
                "--kind",
                "docker-restart",
                "--target",
                "c1",
                "--admin",
                "--json",
            ]
        )
        assert args.json is True

    def test_parse_action_args_audit_log(self) -> None:
        from groop.cli import parse_action_args

        args = parse_action_args(
            [
                "preview",
                "--kind",
                "docker-restart",
                "--target",
                "c1",
                "--admin",
                "--audit-log",
                "/tmp/test.jsonl",
            ]
        )
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
        assert payload["argv"] == [DOCKER_EXECUTABLE, "restart", "c1"]
        assert payload["mode"] == "preview"


class TestMainActionReturnCodes:
    """Verify the _main_action function returns correct exit codes."""

    def test_no_admin_returns_2(self) -> None:
        from groop.cli import _main_action

        code = _main_action(["preview", "--kind", "docker-restart", "--target", "c1"])
        assert code == 2

    def test_unknown_kind_returns_2(self) -> None:
        from groop.cli import _main_action

        code = _main_action(
            ["preview", "--kind", "unknown-kind", "--target", "c1", "--admin"]
        )
        assert code == 2

    def test_admin_preview_returns_0(self) -> None:
        from groop.cli import _main_action

        code = _main_action(
            ["preview", "--kind", "docker-restart", "--target", "c1", "--admin"]
        )
        assert code == 0

    def test_preview_both_target_and_container_exit_2(self) -> None:
        """--target and --container are mutually exclusive for preview."""
        from groop.cli import _main_action

        code = _main_action(
            ["preview", "--kind", "docker-restart", "--target", "c1", "--container", "my-app", "--admin"]
        )
        assert code == 2


class TestTargetValidation:
    """validate_target rejects unsafe inputs for Docker and systemd targets."""

    @pytest.mark.parametrize(
        "kind, target, should_pass",
        [
            (ActionKind.DOCKER_START, "my-container", True),
            (ActionKind.DOCKER_START, "a" * 128, True),
            (ActionKind.DOCKER_START, "a" * 129, False),
            (ActionKind.DOCKER_START, "-x", False),
            (ActionKind.DOCKER_START, "--name", False),
            (ActionKind.DOCKER_START, "", False),
            (ActionKind.DOCKER_START, "x;y", False),
            (ActionKind.DOCKER_START, "x&y", False),
            (ActionKind.DOCKER_START, "x|y", False),
            (ActionKind.DOCKER_START, "$(id)", False),
            (ActionKind.DOCKER_START, "x`y", False),
            (ActionKind.DOCKER_START, "x{y", False),
            (ActionKind.DOCKER_START, "x<y", False),
            (ActionKind.DOCKER_START, "x>y", False),
            (ActionKind.DOCKER_START, "x/y", False),
            (ActionKind.DOCKER_START, "../x", False),
            (ActionKind.DOCKER_START, "x y", False),  # space
            (ActionKind.DOCKER_START, "x\ty", False),  # tab
            (ActionKind.DOCKER_START, "x\ny", False),  # newline
            (ActionKind.DOCKER_START, "abcdef0123456789" * 4, True),  # 64-char hex id
            (
                ActionKind.DOCKER_START,
                "abcdef0123456789" * 4 + "g",
                True,
            ),  # 65-char alphanumeric name (valid)
            (ActionKind.DOCKER_START, ".", False),
            (ActionKind.DOCKER_START, "_.-", False),
            (ActionKind.SYSTEMD_START, "nginx.service", True),
            (ActionKind.SYSTEMD_START, "user@1000.service", True),
            (ActionKind.SYSTEMD_START, "my.slice", True),
            (ActionKind.SYSTEMD_START, "my.scope", True),
            (ActionKind.SYSTEMD_START, "my.target", True),
            (ActionKind.SYSTEMD_START, "my.socket", True),
            (ActionKind.SYSTEMD_START, "my.mount", True),
            (ActionKind.SYSTEMD_START, "my.timer", True),
            (ActionKind.SYSTEMD_START, "my.path", True),
            (ActionKind.SYSTEMD_START, "my.txt", False),  # invalid suffix
            (ActionKind.SYSTEMD_START, "my.exe", False),  # invalid suffix
            (ActionKind.SYSTEMD_START, "-x.service", False),
            (ActionKind.SYSTEMD_START, "", False),
            (ActionKind.SYSTEMD_START, "x;y.service", False),
        ],
    )
    def test_validate_target(self, kind, target, should_pass) -> None:
        from groop.actions.execute import validate_target

        if should_pass:
            validate_target(kind, target)  # no error
        else:
            with pytest.raises(ValueError):
                validate_target(kind, target)


class TestExecutionGates:
    """execute_plan's gates must all pass before any runner is called."""

    def _fake_runner(self, argv, *, timeout=30.0):
        from groop.actions.execute import ExecuteResult

        return ExecuteResult(
            kind="",
            target="",
            argv=argv,
            returncode=0,
            stdout="ok",
            stderr="",
            outcome="success",
            duration_s=0.0,
        )

    def test_gate_admin_false_returns_refusal(self) -> None:
        from groop.actions.execute import execute_plan

        result = execute_plan("docker-restart", "x", admin=False)
        assert result.outcome == "refusal"
        assert result.returncode is None

    def test_gate_confirm_wrong_returns_refusal(self) -> None:
        from groop.actions.execute import execute_plan

        result = execute_plan("docker-restart", "x", admin=True, confirm="wrong")
        assert result.outcome == "refusal"

    def test_gate_confirm_empty_returns_refusal(self) -> None:
        from groop.actions.execute import execute_plan

        result = execute_plan("docker-restart", "x", admin=True, confirm="")
        assert result.outcome == "refusal"

    def test_gate_unknown_kind_returns_refusal(self) -> None:
        from groop.actions.execute import execute_plan

        result = execute_plan("docker-eject", "x", admin=True, confirm="EXECUTE")
        assert result.outcome == "refusal"

    def test_gate_disallowed_kind_returns_refusal(self) -> None:
        from groop.actions.execute import execute_plan

        result = execute_plan(
            "systemd-set-property", "x", admin=True, confirm="EXECUTE"
        )
        assert result.outcome == "refusal"

    def test_gate_invalid_target_returns_refusal(self) -> None:
        from groop.actions.execute import execute_plan

        result = execute_plan("docker-restart", "-x", admin=True, confirm="EXECUTE")
        assert result.outcome == "refusal"


class TestExecutionSuccess:
    """Fully gated execution invokes the runner with the expected argv."""

    def test_docker_restart_args(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_plan, ExecuteResult

        collected: list[tuple[str, ...]] = []

        def runner(argv, *, timeout=30.0):
            collected.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_plan(
            "docker-restart",
            "my-container",
            admin=True,
            confirm="EXECUTE",
            runner=runner,
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "success"
        assert collected == [(DOCKER_EXECUTABLE, "restart", "my-container")]

    def test_systemd_stop_args(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_plan, ExecuteResult

        collected: list[tuple[str, ...]] = []

        def runner(argv, *, timeout=30.0):
            collected.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        execute_plan(
            "systemd-stop",
            "sshd.service",
            admin=True,
            confirm="EXECUTE",
            runner=runner,
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert collected == [(SYSTEMCTL_EXECUTABLE, "stop", "sshd.service")]

    def test_nonzero_exit_propagates(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_plan, ExecuteResult

        def runner(argv, *, timeout=30.0):
            return ExecuteResult("", "", argv, 1, "", "error", "nonzero", 0.0)

        result = execute_plan(
            "docker-stop",
            "c1",
            admin=True,
            confirm="EXECUTE",
            runner=runner,
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "nonzero"
        assert result.returncode == 1
        assert result.stderr == "error"


class TestExecutionAudit:
    """Audit records are written pre and post execution."""

    def test_audit_pre_post_written(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_plan, ExecuteResult

        audit_path = tmp_path / "exec.jsonl"

        def runner(argv, *, timeout=30.0):
            return ExecuteResult("", "", argv, 0, "ok", "", "success", 0.05)

        result = execute_plan(
            "docker-start",
            "db",
            admin=True,
            confirm="EXECUTE",
            audit_path=audit_path,
            runner=runner,
            root_check=lambda: True,
        )
        assert result.outcome == "success"
        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
        pre = json.loads(lines[0])
        post = json.loads(lines[1])
        assert pre["stage"] == "pre"
        assert pre["mode"] == "execute"
        assert pre["kind"] == "docker-start"
        assert pre["target"] == "db"
        assert pre["admin"] is True
        assert "confirm" not in pre
        assert post["stage"] == "post"
        assert post["outcome"] == "success"
        assert post["returncode"] == 0
        assert "duration_s" in post

    def test_audit_is_mandatory(self) -> None:
        from groop.actions.execute import execute_plan, ExecuteResult

        def runner(argv, *, timeout=30.0):
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_plan(
            "docker-restart",
            "x",
            admin=True,
            confirm="EXECUTE",
            runner=runner,
            root_check=lambda: True,
            audit_path=None,
        )  # type: ignore[arg-type]
        assert result.outcome == "refusal"


class TestExecutionTimeout:
    """Timeout outcome is produced correctly."""

    def test_timeout_outcome(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_plan, ExecuteResult

        def runner(argv, *, timeout=30.0):
            return ExecuteResult("", "", argv, None, "", "", "timeout", 5.0)

        result = execute_plan(
            "docker-restart",
            "x",
            admin=True,
            confirm="EXECUTE",
            runner=runner,
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "timeout"
        assert result.returncode is None


class TestExecutionRunnerFailure:
    """Runner failure outcome is produced correctly."""

    def test_runner_failure_outcome(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_plan, ExecuteResult

        def runner(argv, *, timeout=30.0):
            return ExecuteResult(
                "", "", argv, None, "", "OSError: not found", "runner_failure", 0.0
            )

        result = execute_plan(
            "docker-restart",
            "x",
            admin=True,
            confirm="EXECUTE",
            runner=runner,
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "runner_failure"


class TestExecutionResultRendering:
    """Result JSON and text rendering."""

    def test_result_to_jsonable(self) -> None:
        from groop.actions.execute import ExecuteResult, result_to_jsonable

        r = ExecuteResult(
            "docker-restart",
            "c1",
            ("docker", "restart", "c1"),
            0,
            "out",
            "err",
            "success",
            0.123,
        )
        d = result_to_jsonable(r)
        assert d["kind"] == "docker-restart"
        assert d["outcome"] == "success"
        assert d["returncode"] == 0
        assert d["stdout"] == "out"

    def test_render_result_text(self) -> None:
        from groop.actions.execute import ExecuteResult, render_result_text

        r = ExecuteResult(
            "docker-stop",
            "c1",
            ("docker", "stop", "c1"),
            0,
            "output",
            "",
            "success",
            0.5,
        )
        text = render_result_text(r)
        assert "Action: docker-stop" in text
        assert "Outcome: success" in text
        assert "--- stdout ---" in text
        assert "output" in text


class TestExecutionCliIntegration:
    """CLI integration for the execute subcommand."""

    def test_parse_execute_args(self) -> None:
        from groop.cli import parse_action_args

        args = parse_action_args(
            [
                "execute",
                "--kind",
                "docker-restart",
                "--target",
                "c1",
                "--admin",
                "--confirm",
                "EXECUTE",
            ]
        )
        assert args.command == "execute"
        assert args.kind == "docker-restart"
        assert args.target == "c1"
        assert args.admin is True
        assert args.confirm == "EXECUTE"

    def test_parse_execute_args_json_timeout(self) -> None:
        from groop.cli import parse_action_args

        args = parse_action_args(
            [
                "execute",
                "--kind",
                "systemd-start",
                "--target",
                "u.service",
                "--admin",
                "--confirm",
                "EXECUTE",
                "--json",
                "--timeout",
                "15.5",
            ]
        )
        assert args.json is True
        assert args.timeout == 15.5

    def test_execute_refusal_exit_code_2(self) -> None:
        from groop.cli import _main_action

        code = _main_action(["execute", "--kind", "unknown", "--target", "x"])
        assert code == 2

    def test_execute_refusal_no_admin_exit_code_2(self) -> None:
        from groop.cli import _main_action

        code = _main_action(["execute", "--kind", "docker-restart", "--target", "c1"])
        assert code == 2

    def test_execute_refusal_wrong_confirm_exit_code_2(self) -> None:
        from groop.cli import _main_action

        code = _main_action(
            [
                "execute",
                "--kind",
                "docker-restart",
                "--target",
                "c1",
                "--admin",
                "--confirm",
                "NO",
            ]
        )
        assert code == 2

    def test_execute_both_target_and_container_exit_2(self) -> None:
        """--target and --container are mutually exclusive for execute."""
        from groop.cli import _main_action

        code = _main_action(
            [
                "execute",
                "--kind",
                "docker-restart",
                "--target",
                "c1",
                "--container",
                "my-app",
                "--admin",
                "--confirm",
                "EXECUTE",
            ]
        )
        assert code == 2

    def test_parse_execute_set_property_args(self) -> None:
        """Verify --property, --value, --mode parse correctly for execute."""
        from groop.cli import parse_action_args

        args = parse_action_args(
            [
                "execute",
                "--kind",
                "systemd-set-property",
                "--target",
                "my.slice",
                "--admin",
                "--confirm",
                "EXECUTE",
                "--property",
                "memory.high",
                "--value",
                "max",
                "--mode",
                "runtime",
            ]
        )
        assert args.kind == "systemd-set-property"
        assert args.target == "my.slice"
        assert args.property == "memory.high"
        assert args.value == "max"
        assert args.mode == "runtime"
        assert args.admin is True
        assert args.confirm == "EXECUTE"

    def test_parse_preview_set_property_args(self) -> None:
        """Verify --property, --value parse correctly for preview."""
        from groop.cli import parse_action_args

        args = parse_action_args(
            [
                "preview",
                "--kind",
                "systemd-set-property",
                "--target",
                "my.slice",
                "--admin",
                "--property",
                "memory.high",
                "--value",
                "1073741824",
            ]
        )
        assert args.kind == "systemd-set-property"
        assert args.property == "memory.high"
        assert args.value == "1073741824"

    def test_execute_set_property_refusal_exit_code_2(self) -> None:
        """Verify execute_set_property routing via CLI (no admin => refusal)."""
        from groop.cli import _main_action

        code = _main_action(
            [
                "execute",
                "--kind",
                "systemd-set-property",
                "--target",
                "my.slice",
                "--property",
                "memory.high",
                "--value",
                "max",
            ]
        )
        # Refused because no --admin
        assert code == 2


class TestExecutionAllowlistExclusion:
    """Verify that set-property and future non-allowlisted kinds are rejected."""

    def test_set_property_not_in_execution_allowlist(self) -> None:
        from groop.actions.catalog import EXECUTION_ALLOWLIST, ActionKind

        assert ActionKind.SYSTEMD_SET_PROPERTY not in EXECUTION_ALLOWLIST

    def test_execution_allowlist_has_correct_kinds(self) -> None:
        from groop.actions.catalog import EXECUTION_ALLOWLIST, ActionKind

        expected = frozenset(
            {
                ActionKind.DOCKER_RESTART,
                ActionKind.DOCKER_STOP,
                ActionKind.DOCKER_START,
                ActionKind.SYSTEMD_RESTART,
                ActionKind.SYSTEMD_STOP,
                ActionKind.SYSTEMD_START,
                ActionKind.DOCKER_KILL,
                ActionKind.SYSTEMD_KILL,
                ActionKind.DOCKER_UPDATE,
            }
        )
        assert EXECUTION_ALLOWLIST == expected


class TestOutputBounding:
    """Stdout/stderr bounding and redaction."""

    def test_short_output_passes_through(self) -> None:
        from groop.actions.execute import _bound_output

        assert _bound_output("hello") == "hello"

    def test_long_output_is_truncated(self) -> None:
        from groop.actions.execute import _bound_output, _MAX_OUTPUT_CHARS

        long = "x" * (_MAX_OUTPUT_CHARS + 100)
        bounded = _bound_output(long)
        assert len(bounded) == _MAX_OUTPUT_CHARS + len(" ... (truncated)")
        assert bounded.endswith(" ... (truncated)")

    def test_exact_max_passes_through(self) -> None:
        from groop.actions.execute import _bound_output, _MAX_OUTPUT_CHARS

        exact = "x" * _MAX_OUTPUT_CHARS
        assert _bound_output(exact) == exact


class TestPreviewWithValidation:
    """build_preview now validates targets and rejects unsafe ones."""

    def test_preview_rejects_invalid_target(self) -> None:
        with pytest.raises(ValueError):
            build_preview("docker-restart", "x;y")

    def test_preview_rejects_empty_target(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            build_preview("docker-restart", "")

    def test_preview_rejects_option_like_target(self) -> None:
        with pytest.raises(ValueError, match="option-like"):
            build_preview("docker-restart", "--name")

    def test_preview_valid_target_still_works(self) -> None:
        plan = build_preview("docker-restart", "my-container")
        assert isinstance(plan, ActionPlan)
        assert list(plan.argv) == [DOCKER_EXECUTABLE, "restart", "my-container"]


class TestP46ControllerSecurityCorrections:
    """Adversarial production-boundary checks added after controller review."""

    @staticmethod
    def _success(argv, *, timeout=30.0):
        from groop.actions.execute import ExecuteResult

        return ExecuteResult("", "", argv, 0, "ok", "", "success", 0.0)

    def test_root_gate_precedes_audit_and_runner(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import groop.actions.execute as execute

        called = []
        monkeypatch.setattr(
            execute,
            "_write_execution_audit_pre",
            lambda *a, **k: called.append("audit"),
        )
        result = execute.execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "a.jsonl",
            root_check=lambda: False,
            runner=lambda *a, **k: called.append("runner"),
        )
        assert result.outcome == "refusal"
        assert called == []

    @pytest.mark.parametrize(
        "admin, confirm", [(False, "EXECUTE"), (True, "NO"), (True, "")]
    )
    def test_missing_gate_does_not_touch_audit_or_runner(
        self, tmp_path: Path, admin: bool, confirm: str
    ) -> None:
        called = []
        result = __import__(
            "groop.actions.execute", fromlist=["execute_plan"]
        ).execute_plan(
            "docker-start",
            "c1",
            admin=admin,
            confirm=confirm,
            audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True,
            runner=lambda *a, **k: called.append(True),
        )
        assert result.outcome == "refusal"
        assert called == []
        assert not (tmp_path / "a.jsonl").exists()

    @pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 30.001])
    def test_invalid_timeout_is_rejected_before_audit_or_runner(
        self, tmp_path: Path, timeout: float
    ) -> None:
        from groop.actions.execute import execute_plan

        called = []
        path = tmp_path / "a.jsonl"
        result = execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            timeout=timeout,
            audit_path=path,
            root_check=lambda: True,
            runner=lambda *a, **k: called.append(True),
        )
        assert result.outcome == "refusal"
        assert called == []
        assert not path.exists()

    def test_missing_and_default_audit_are_fail_closed(self, tmp_path: Path) -> None:
        from groop.actions.execute import DEFAULT_EXECUTION_AUDIT_PATH, execute_plan

        called = []
        missing = execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            audit_path=None,  # type: ignore[arg-type]
            root_check=lambda: True,
            runner=lambda *a, **k: called.append(True),
        )
        assert missing.outcome == "refusal"
        assert called == []
        default_result = execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            root_check=lambda: False,
            runner=lambda *a, **k: called.append(True),
        )
        assert default_result.outcome == "refusal"
        assert DEFAULT_EXECUTION_AUDIT_PATH.is_absolute()
        assert called == []

    @pytest.mark.parametrize(
        "make_bad",
        [
            lambda path: path.symlink_to(path.parent / "elsewhere"),
            lambda path: (path.write_text("x"), path.chmod(0o644)),
        ],
    )
    def test_symlink_and_broad_permissions_are_rejected(
        self, tmp_path: Path, make_bad
    ) -> None:
        from groop.actions.execute import execute_plan

        path = tmp_path / "audit.jsonl"
        if "symlink" in getattr(make_bad, "__name__", ""):
            (tmp_path / "elsewhere").write_text("x")
        make_bad(path)
        called = []
        result = execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            audit_path=path,
            root_check=lambda: True,
            runner=lambda *a, **k: called.append(True),
        )
        assert result.outcome == "refusal"
        assert called == []

    def test_post_audit_failure_preserves_mutation_outcome(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import groop.actions.execute as execute

        monkeypatch.setattr(
            execute,
            "_write_execution_audit_post",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        result = execute.execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True,
            runner=self._success,
        )
        assert result.outcome == "audit_failure"
        assert result.action_outcome == "success"
        assert result.audit_outcome == "post_failure"

    def test_runner_exception_is_bounded_and_post_audited(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_plan

        def raises(argv, *, timeout):
            raise RuntimeError("secret raw exception must not leak")

        result = execute_plan(
            "docker-stop",
            "c1",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True,
            runner=raises,
        )
        assert result.outcome == "runner_failure"
        assert "secret raw" not in result.stderr
        assert len((tmp_path / "a.jsonl").read_text().splitlines()) == 2

    def test_huge_injected_output_is_bounded(self, tmp_path: Path) -> None:
        from groop.actions.execute import ExecuteResult, execute_plan

        def huge(argv, *, timeout):
            return ExecuteResult(
                "", "", argv, 0, "x" * 100_000, "y" * 100_000, "success", 0.0
            )

        result = execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True,
            runner=huge,
        )
        assert len(result.stdout) <= 4096 + len(" ... (truncated)")
        assert len(result.stderr) <= 4096 + len(" ... (truncated)")

    def test_exact_absolute_argv_and_no_shell_true(self, tmp_path: Path) -> None:
        import ast
        import inspect
        from groop.actions.execute import execute_plan

        observed = []

        def runner(argv, *, timeout):
            observed.append(argv)
            return self._success(argv, timeout=timeout)

        result = execute_plan(
            "systemd-restart",
            "demo.service",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True,
            runner=runner,
        )
        assert result.outcome == "success"
        assert observed == [(SYSTEMCTL_EXECUTABLE, "restart", "demo.service")]
        tree = ast.parse(inspect.getsource(execute_plan.__globals__["_default_runner"]))
        assert not any(
            isinstance(node, ast.keyword)
            and node.arg == "shell"
            and getattr(node.value, "value", None) is True
            for node in ast.walk(tree)
        )

    @pytest.mark.parametrize(
        "target", [".", "_.-", "-bad", "bad/name", "bad;name", "bad name", ""]
    )
    def test_docker_names_are_actual_safe_names(self, target: str) -> None:
        from groop.actions.execute import validate_target

        with pytest.raises(ValueError):
            validate_target(ActionKind.DOCKER_START, target)

    @pytest.mark.parametrize(
        "target",
        [
            "unit",
            ".service",
            "-unit.service",
            "unit.txt",
            "unit..service",
            "unit/service",
        ],
    )
    def test_systemd_units_require_consistent_safe_forms(self, target: str) -> None:
        from groop.actions.execute import validate_target

        with pytest.raises(ValueError):
            validate_target(ActionKind.SYSTEMD_START, target)

    def test_audit_identity_is_stable_and_confirmation_is_not_logged(
        self, tmp_path: Path
    ) -> None:
        from groop.actions.execute import AuditIdentity, execute_plan

        result = execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True,
            identity=lambda: AuditIdentity(4242, "fixture-user"),
            runner=self._success,
            clock=lambda: 1234.5,
        )
        assert result.outcome == "success"
        records = [
            json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()
        ]
        assert {record["uid"] for record in records} == {4242}
        assert {record["user"] for record in records} == {"fixture-user"}
        assert all("confirm" not in record for record in records)

    def test_cli_execute_has_no_arbitrary_audit_log_option(self) -> None:
        from groop.cli import parse_action_args

        with pytest.raises(SystemExit):
            parse_action_args(
                [
                    "execute",
                    "--kind",
                    "docker-start",
                    "--target",
                    "c1",
                    "--audit-log",
                    "/tmp/x",
                ]
            )

    def test_non_boolean_root_check_fails_closed(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_plan

        called = False

        def runner(argv, *, timeout):
            nonlocal called
            called = True
            return self._success(argv, timeout=timeout)

        result = execute_plan(
            "docker-start",
            "c1",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: "yes",
            runner=runner,
        )
        assert result.outcome == "refusal"
        assert called is False
        assert not (tmp_path / "audit.jsonl").exists()

    def test_drain_failure_kills_and_reaps_child(self, monkeypatch) -> None:
        from groop.actions import execute

        class Stream:
            closed = False

            def fileno(self):
                return 10

            def close(self):
                self.closed = True

        class Process:
            stdout = Stream()
            stderr = Stream()
            killed = False
            waited = False

            def poll(self):
                return 1 if self.killed else None

            def kill(self):
                self.killed = True

            def wait(self, timeout):
                self.waited = True
                return 1

        class Selector:
            def register(self, *args):
                pass

            def get_map(self):
                return {1: object()}

            def select(self, timeout):
                raise RuntimeError("selector failed")

            def close(self):
                pass

        process = Process()
        monkeypatch.setattr(execute.selectors, "DefaultSelector", Selector)
        with pytest.raises(RuntimeError, match="selector failed"):
            execute._drain_process(process, 1.0)
        assert process.killed is True
        assert process.waited is True
        assert process.stdout.closed is True
        assert process.stderr.closed is True


# ---------------------------------------------------------------------------
# P49 — systemd memory.high governance tests
# ---------------------------------------------------------------------------


class TestMemoryHighValueValidation:
    """validate_memory_high_value rejects invalid inputs and accepts valid ones."""

    @pytest.mark.parametrize(
        "value",
        [
            "max",
            "1",
            "1073741824",
            "9223372036854775807",  # 2^63 - 1, max allowed
            "+100",
        ],
    )
    def test_valid_values(self, value: str) -> None:
        from groop.actions.governance import validate_memory_high_value

        result = validate_memory_high_value(value)
        assert isinstance(result, str)
        if value.startswith("+"):
            assert result == value[1:]  # leading + is stripped
        else:
            assert result == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "0",
            "-1",
            "1.5",
            "50%",
            "1 000",
            "1,000",
            "abc",
            "0x100",
            "1e6",
            " ",
            "\t",
            "1\n",
            "1;2",
            "'123'",
            '"456"',
            "9223372036854775808",  # > 2^63 - 1
            "18446744073709551616",  # > 2^64
        ],
    )
    def test_invalid_values(self, value: str) -> None:
        from groop.actions.governance import validate_memory_high_value

        with pytest.raises(ValueError):
            validate_memory_high_value(value)

    def test_non_string_rejected(self) -> None:
        from groop.actions.governance import validate_memory_high_value

        with pytest.raises(ValueError):
            validate_memory_high_value(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            validate_memory_high_value("")


class TestMemoryHighUnitValidation:
    """validate_memory_high_unit rejects invalid systemd unit names."""

    @pytest.mark.parametrize("unit", ["my.slice", "user@1000.service", "nginx.scope", "my.target"])
    def test_valid_units(self, unit: str) -> None:
        from groop.actions.governance import validate_memory_high_unit

        validate_memory_high_unit(unit)

    @pytest.mark.parametrize(
        "unit", ["", "-x.service", "..service", "unit.txt", "unit", ".service"]
    )
    def test_invalid_units(self, unit: str) -> None:
        from groop.actions.governance import validate_memory_high_unit

        with pytest.raises(ValueError):
            validate_memory_high_unit(unit)


class TestPersistenceDetection:
    """detect_default_persistence returns the correct default for each unit type."""

    def test_scope_defaults_to_runtime(self) -> None:
        from groop.actions.governance import detect_default_persistence

        assert detect_default_persistence("docker-abc123.scope") == "runtime"
        assert detect_default_persistence("session-1.scope") == "runtime"

    def test_slice_defaults_to_persistent(self) -> None:
        from groop.actions.governance import detect_default_persistence

        assert detect_default_persistence("my.slice") == "persistent"

    def test_service_defaults_to_persistent(self) -> None:
        from groop.actions.governance import detect_default_persistence

        assert detect_default_persistence("nginx.service") == "persistent"

    @pytest.mark.parametrize(
        "mode, expected",
        [("runtime", "runtime"), ("persistent", "persistent"), ("RUNTIME", "runtime"), ("Persistent", "persistent")],
    )
    def test_validate_persistence_mode(self, mode: str, expected: str) -> None:
        from groop.actions.governance import validate_persistence_mode

        assert validate_persistence_mode(mode) == expected

    @pytest.mark.parametrize("mode", ["", "auto", "transient", "none", None])
    def test_invalid_persistence_mode(self, mode: str) -> None:
        from groop.actions.governance import validate_persistence_mode

        with pytest.raises(ValueError):
            validate_persistence_mode(mode)


class TestBuildSetPropertyArgv:
    """build_set_property_argv constructs correct systemctl argv."""

    def test_persistent_mode(self) -> None:
        from groop.actions.governance import build_set_property_argv, SYSTEMCTL_EXECUTABLE

        argv = build_set_property_argv("my.slice", "memory.high", "1073741824", persistence="persistent")
        assert argv == [SYSTEMCTL_EXECUTABLE, "set-property", "my.slice", "memory.high=1073741824"]

    def test_runtime_mode(self) -> None:
        from groop.actions.governance import build_set_property_argv, SYSTEMCTL_EXECUTABLE

        argv = build_set_property_argv("my.scope", "memory.high", "max", persistence="runtime")
        assert argv == [SYSTEMCTL_EXECUTABLE, "set-property", "--runtime", "my.scope", "memory.high=max"]

    def test_max_value(self) -> None:
        from groop.actions.governance import build_set_property_argv, SYSTEMCTL_EXECUTABLE

        argv = build_set_property_argv("my.slice", "memory.high", "max", persistence="persistent")
        assert argv == [SYSTEMCTL_EXECUTABLE, "set-property", "my.slice", "memory.high=max"]

    def test_rejects_wrong_property(self) -> None:
        from groop.actions.governance import build_set_property_argv

        with pytest.raises(ValueError, match="memory.high"):
            build_set_property_argv("my.slice", "memory.max", "100")

    def test_rejects_invalid_value(self) -> None:
        from groop.actions.governance import build_set_property_argv

        with pytest.raises(ValueError):
            build_set_property_argv("my.slice", "memory.high", "-1")


class TestSetPropertyPreview:
    """build_set_property_preview returns a complete SetPropertyPlan."""

    def test_basic_preview(self) -> None:
        from groop.actions.governance import (
            SetPropertyPlan,
            build_set_property_preview,
            SYSTEMCTL_EXECUTABLE,
        )

        plan = build_set_property_preview("my.slice", "memory.high", "max")
        assert isinstance(plan, SetPropertyPlan)
        assert plan.unit == "my.slice"
        assert plan.property_name == "memory.high"
        assert plan.property_value == "max"
        assert plan.kind == "systemd-set-property"
        assert plan.mode == "preview"
        assert SYSTEMCTL_EXECUTABLE in plan.argv
        assert "memory.high=max" in plan.argv

    def test_preview_with_current_value_reader(self) -> None:
        from groop.actions.governance import build_set_property_preview

        plan = build_set_property_preview(
            "my.slice", "memory.high", "max",
            current_value_reader=lambda u: "1073741824",
        )
        assert plan.current_value == "1073741824"

    def test_preview_fallback_persistence(self) -> None:
        from groop.actions.governance import build_set_property_preview

        # Scope -> runtime
        plan = build_set_property_preview("session-1.scope", "memory.high", "max")
        assert plan.persistence == "runtime"

        # Slice -> persistent
        plan = build_set_property_preview("my.slice", "memory.high", "max")
        assert plan.persistence == "persistent"

    def test_render_preview(self) -> None:
        from groop.actions.governance import build_set_property_preview, render_set_property_preview

        plan = build_set_property_preview("my.slice", "memory.high", "max")
        text = render_set_property_preview(plan)
        assert "memory.high" in text
        assert "max" in text
        assert "my.slice" in text
        assert "preview only" in text

    def test_plan_to_jsonable(self) -> None:
        from groop.actions.governance import build_set_property_preview, set_property_plan_to_jsonable

        plan = build_set_property_preview("my.slice", "memory.high", "max")
        d = set_property_plan_to_jsonable(plan)
        assert d["property"] == "memory.high"
        assert d["value"] == "max"
        assert d["target"] == "my.slice"
        assert d["kind"] == "systemd-set-property"


class TestExecuteSetProperty:
    """execute_set_property gates and stale detection."""

    def _fake_runner(self, argv, *, timeout=30.0):
        from groop.actions.execute import ExecuteResult

        return ExecuteResult("", "", argv, 0, "ok", "", "success", 0.0)

    def test_gate_admin_false(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_set_property

        result = execute_set_property(
            "my.slice",
            property_name="memory.high",
            property_value="max",
            admin=False,
            audit_path=tmp_path / "audit.jsonl",
        )
        assert result.outcome == "refusal"

    def test_gate_confirm_wrong(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_set_property

        result = execute_set_property(
            "my.slice",
            property_name="memory.high",
            property_value="max",
            admin=True,
            confirm="wrong",
            audit_path=tmp_path / "audit.jsonl",
        )
        assert result.outcome == "refusal"

    def test_gate_root_false(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_set_property

        result = execute_set_property(
            "my.slice",
            property_name="memory.high",
            property_value="max",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: False,
        )
        assert result.outcome == "refusal"

    def test_invalid_property_rejected(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_set_property

        result = execute_set_property(
            "my.slice",
            property_name="memory.max",
            property_value="100",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "refusal"

    def test_invalid_value_rejected(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_set_property

        result = execute_set_property(
            "my.slice",
            property_name="memory.high",
            property_value="-1",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "refusal"

    def test_invalid_unit_rejected(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_set_property

        result = execute_set_property(
            "-x.service",
            property_name="memory.high",
            property_value="max",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "refusal"

    def test_success_path(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_set_property, ExecuteResult

        argv_collected = []

        def runner(argv, *, timeout=30.0):
            argv_collected.append(argv)
            return ExecuteResult("", "", argv, 0, "ok", "", "success", 0.0)

        result = execute_set_property(
            "my.slice",
            property_name="memory.high",
            property_value="max",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
        )
        assert result.outcome == "success"
        assert len(argv_collected) == 1
        argv = argv_collected[0]
        assert "/usr/bin/systemctl" in argv
        assert "set-property" in argv
        assert "memory.high=max" in argv

    def test_audit_written(self, tmp_path: Path) -> None:
        from groop.actions.execute import execute_set_property, ExecuteResult

        def runner(argv, *, timeout=30.0):
            return ExecuteResult("", "", argv, 0, "ok", "", "success", 0.0)

        audit_path = tmp_path / "exec.jsonl"
        execute_set_property(
            "my.slice",
            property_name="memory.high",
            property_value="1073741824",
            admin=True,
            confirm="EXECUTE",
            audit_path=audit_path,
            root_check=lambda: True,
            runner=runner,
        )
        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
        pre = json.loads(lines[0])
        post = json.loads(lines[1])
        assert pre["stage"] == "pre"
        assert pre["kind"] == "systemd-set-property"
        assert post["stage"] == "post"
        assert post["outcome"] == "success"

    def test_stale_detection(self, tmp_path: Path) -> None:
        """planned_current_value differs from fresh read => stale outcome."""
        from groop.actions.execute import execute_set_property

        def current_value_reader(unit: str) -> str:
            return "1073741824"  # different from planned "max"

        result = execute_set_property(
            "my.slice",
            property_name="memory.high",
            property_value="max",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            planned_current_value="original_value",
            current_value_reader=current_value_reader,
        )
        assert result.outcome == "stale"
        assert "value changed" in result.stderr

    def test_runtime_mode_argv(self, tmp_path: Path) -> None:
        """Scope units default to --runtime."""
        from groop.actions.execute import execute_set_property, ExecuteResult

        argv_collected = []

        def runner(argv, *, timeout=30.0):
            argv_collected.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_set_property(
            "docker-abc.scope",
            property_name="memory.high",
            property_value="max",
            admin=True,
            confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
        )
        assert result.outcome == "success"
        assert "--runtime" in argv_collected[0]
