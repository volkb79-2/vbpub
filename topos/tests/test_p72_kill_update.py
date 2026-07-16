from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# P72 - Kill and Update verb acceptance oracles
# ---------------------------------------------------------------------------


def _usage_1mib(target: str) -> int:
    """A container using 1 MiB: any test limit here is comfortably above it.

    The below-current guard is fail-closed -- a current usage that cannot be read
    refuses the update -- so every --memory test states the usage it is testing
    against instead of relying on the guard staying quiet.
    """
    return 1024 * 1024


class TestP72SignalValidation:
    """Closed signal allowlist - Oracle 2."""

    def test_valid_signals_pass(self) -> None:
        from topos.actions.kill_ops import validate_signal
        for sig in ("TERM", "INT", "HUP", "KILL", "QUIT", "USR1", "USR2"):
            assert validate_signal(sig) == sig

    def test_numeric_signal_rejected(self) -> None:
        from topos.actions.kill_ops import validate_signal
        with pytest.raises(ValueError, match="symbolic name"):
            validate_signal("9")

    def test_sig_prefix_rejected(self) -> None:
        from topos.actions.kill_ops import validate_signal
        with pytest.raises(ValueError, match="SIG"):
            validate_signal("SIGKILL")
        with pytest.raises(ValueError, match="SIG"):
            validate_signal("SIGTERM")

    def test_bogus_signal_rejected(self) -> None:
        from topos.actions.kill_ops import validate_signal
        with pytest.raises(ValueError, match="unknown signal"):
            validate_signal("bogus")
        with pytest.raises(ValueError, match="unknown signal"):
            validate_signal("STOP")
        with pytest.raises(ValueError, match="unknown signal"):
            validate_signal("CONT")


class TestP72KillPlan:
    """Kill plan preview - Oracle 1 (previewed argv == executed argv)."""

    def test_docker_kill_argv(self) -> None:
        from topos.actions.kill_ops import build_kill_preview, DOCKER_EXECUTABLE
        plan = build_kill_preview("docker-kill", "my-container", signal="TERM")
        assert list(plan.argv) == [DOCKER_EXECUTABLE, "kill", "--signal", "TERM", "my-container"]
        assert plan.signal == "TERM"
        assert plan.kind == "docker-kill"

    def test_systemd_kill_argv(self) -> None:
        from topos.actions.kill_ops import build_kill_preview, SYSTEMCTL_EXECUTABLE
        plan = build_kill_preview("systemd-kill", "nginx.service", signal="HUP")
        assert list(plan.argv) == [SYSTEMCTL_EXECUTABLE, "kill", "--signal", "HUP", "nginx.service"]
        assert plan.signal == "HUP"
        assert plan.kind == "systemd-kill"

    def test_kill_preview_renders_signal(self) -> None:
        from topos.actions.kill_ops import build_kill_preview, render_kill_preview
        plan = build_kill_preview("docker-kill", "c1", signal="INT")
        text = render_kill_preview(plan)
        assert "INT" in text
        assert "docker-kill" in text
        assert "c1" in text
        assert "preview only" in text

    def test_kill_plan_to_jsonable(self) -> None:
        from topos.actions.kill_ops import build_kill_preview, kill_plan_to_jsonable
        plan = build_kill_preview("docker-kill", "c1", signal="TERM")
        d = kill_plan_to_jsonable(plan)
        assert d["signal"] == "TERM"
        assert d["kind"] == "docker-kill"
        assert d["target"] == "c1"


class TestP72KillForceGate:
    """KILL signal requires --force - Oracle 3."""

    def test_kill_without_force_refused(self) -> None:
        from topos.actions.kill_ops import build_kill_preview
        with pytest.raises(ValueError, match="--force"):
            build_kill_preview("docker-kill", "c1", signal="KILL", force=False)

    def test_kill_with_force_proceeds(self) -> None:
        from topos.actions.kill_ops import build_kill_preview
        plan = build_kill_preview("docker-kill", "c1", signal="KILL", force=True)
        assert plan.signal == "KILL"
        assert plan.force is True

    def test_execute_kill_without_force_refused(self, tmp_path: Path) -> None:
        from topos.actions.execute import execute_kill
        result = execute_kill(
            "docker-kill", "c1",
            signal="KILL", force=False,
            admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "refusal"
        assert "--force" in result.stderr

    def test_execute_kill_with_force_proceeds(self, tmp_path: Path) -> None:
        from topos.actions.execute import execute_kill, ExecuteResult

        collected = []

        def runner(argv, *, timeout=30.0):
            collected.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_kill(
            "docker-kill", "c1",
            signal="KILL", force=True,
            admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
        )
        assert result.outcome == "success"
        assert len(collected) == 1


class TestP72KillProtectedEntity:
    """Protected entities are refused - Oracle 4."""

    def test_protected_entity_refused(self, tmp_path: Path) -> None:
        from topos.actions.execute import execute_kill

        def protected_check(kind: str, target: str) -> bool:
            return target == "protected-svc"

        result = execute_kill(
            "docker-kill", "protected-svc",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            protected_check=protected_check,
        )
        assert result.outcome == "refusal"
        assert "protected" in result.stderr

    def test_protected_entity_runner_not_invoked(self, tmp_path: Path) -> None:
        """Assert the runner was never invoked - not just that an error was returned."""
        from topos.actions.execute import execute_kill

        called = []

        def protected_check(kind: str, target: str) -> bool:
            return True

        def runner(argv, *, timeout=30.0):
            called.append(True)
            from topos.actions.execute import ExecuteResult
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_kill(
            "docker-kill", "protected-svc",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            protected_check=protected_check,
        )
        assert result.outcome == "refusal"
        assert called == []  # runner never invoked

    def test_protected_entity_admin_confirmed_still_refused(self, tmp_path: Path) -> None:
        """Protected target cannot be killed even with --admin and correct token."""
        from topos.actions.execute import execute_kill

        called = []

        def protected_check(kind: str, target: str) -> bool:
            return True

        def runner(argv, *, timeout=30.0):
            called.append(True)
            from topos.actions.execute import ExecuteResult
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_kill(
            "docker-kill", "critical-svc",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            protected_check=protected_check,
        )
        assert result.outcome == "refusal"
        assert called == []


class TestP72KillExecution:
    """Execute kill - audit, success, fail-closed."""

    def test_kill_audit_record_contains_signal(self, tmp_path: Path) -> None:
        """Oracle 1: audit record contains the signal."""
        from topos.actions.execute import execute_kill, ExecuteResult

        audit_path = tmp_path / "audit.jsonl"

        def runner(argv, *, timeout=30.0):
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_kill(
            "docker-kill", "my-container",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=audit_path,
            root_check=lambda: True,
            runner=runner,
        )
        assert result.outcome == "success"
        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
        post = json.loads(lines[1])
        assert post["kind"] == "docker-kill"
        assert "TERM" in str(post["argv"])

    def test_kill_non_root_refused(self, tmp_path: Path) -> None:
        """Oracle 9: non-root invocation refused."""
        from topos.actions.execute import execute_kill

        result = execute_kill(
            "docker-kill", "c1",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: False,
        )
        assert result.outcome == "refusal"
        assert "root" in result.stderr

    def test_kill_non_admin_refused(self, tmp_path: Path) -> None:
        """Oracle 9: non-admin invocation refused."""
        from topos.actions.execute import execute_kill

        result = execute_kill(
            "docker-kill", "c1",
            signal="TERM", admin=False, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
        )
        assert result.outcome == "refusal"

    def test_kill_wrong_confirm_refused(self, tmp_path: Path) -> None:
        """Wrong confirmation token is refused."""
        from topos.actions.execute import execute_kill

        result = execute_kill(
            "docker-kill", "c1",
            signal="TERM", admin=True, confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "refusal"


class TestP72UpdatePlan:
    """Update plan preview - Oracle 1 (previewed argv == executed argv)."""

    def test_docker_update_memory_argv(self) -> None:
        from topos.actions.update_ops import build_update_preview, DOCKER_EXECUTABLE
        plan = build_update_preview("my-container", memory="512M", current_memory_reader=_usage_1mib)
        assert plan.kind == "docker-update"
        expected_memory = 512 * 1024 * 1024
        assert list(plan.argv) == [DOCKER_EXECUTABLE, "update", "--memory", str(expected_memory), "my-container"]
        assert plan.memory == expected_memory

    def test_docker_update_cpus_argv(self) -> None:
        from topos.actions.update_ops import build_update_preview, DOCKER_EXECUTABLE
        plan = build_update_preview("my-container", cpus="2.5")
        assert list(plan.argv) == [DOCKER_EXECUTABLE, "update", "--cpus", "2.5", "my-container"]
        assert plan.cpus == 2.5

    def test_docker_update_both_argv(self) -> None:
        from topos.actions.update_ops import build_update_preview, DOCKER_EXECUTABLE
        plan = build_update_preview("my-container", memory="1G", cpus="4", current_memory_reader=_usage_1mib)
        argv = list(plan.argv)
        assert DOCKER_EXECUTABLE in argv
        assert "--memory" in argv
        assert "--cpus" in argv
        assert plan.memory == 1073741824
        assert plan.cpus == 4.0

    def test_update_at_least_one_required(self) -> None:
        from topos.actions.update_ops import build_update_preview
        with pytest.raises(ValueError, match="at least one"):
            build_update_preview("c1")

    def test_update_preview_renders(self) -> None:
        from topos.actions.update_ops import build_update_preview, render_update_preview
        plan = build_update_preview("c1", memory="512M", current_memory_reader=_usage_1mib)
        text = render_update_preview(plan)
        assert "docker-update" in text
        assert "c1" in text
        assert "Memory" in text

    def test_update_plan_to_jsonable(self) -> None:
        from topos.actions.update_ops import build_update_preview, update_plan_to_jsonable
        plan = build_update_preview("c1", memory="512M", current_memory_reader=_usage_1mib)
        d = update_plan_to_jsonable(plan)
        assert d["kind"] == "docker-update"
        assert d["memory"] == 512 * 1024 * 1024
        assert d["target"] == "c1"


class TestP72UpdateMemoryValidation:
    """Memory validation - Oracle 7 (reuses P49's parse_size)."""

    def test_valid_memory_values(self) -> None:
        from topos.actions.update_ops import validate_memory
        assert validate_memory("512") == 512
        assert validate_memory("1K") == 1024
        assert validate_memory("1M") == 1024 * 1024
        assert validate_memory("1G") == 1024 * 1024 * 1024
        assert validate_memory("512M") == 512 * 1024 * 1024

    def test_overflow_memory_rejected(self) -> None:
        from topos.actions.update_ops import validate_memory
        with pytest.raises(ValueError):
            validate_memory("-1")
        with pytest.raises(ValueError):
            validate_memory("0")
        with pytest.raises(ValueError):
            validate_memory("garbage")

    def test_update_systemd_target_refused(self, tmp_path: Path) -> None:
        """Oracle 6: update against systemd unit exits with set-property message."""
        from topos.actions.execute import execute_update
        # The target validator rejects .service targets for DOCKER_UPDATE
        result = execute_update(
            "nginx.service",
            memory="512M",
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "refusal"
        assert "set-property" in result.stderr


class TestP72UpdateBelowCurrentGuard:
    """Oracle 5: Refuse memory limit below current usage."""

    def test_below_current_refused(self, tmp_path: Path) -> None:
        from topos.actions.execute import execute_update

        result = execute_update(
            "c1",
            memory="100",
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            current_memory_reader=lambda t: 500,
        )
        assert result.outcome == "refusal"
        assert "below current" in result.stderr

    def test_below_current_with_override_proceeds(self, tmp_path: Path) -> None:
        from topos.actions.execute import execute_update, ExecuteResult

        collected = []

        def runner(argv, *, timeout=30.0):
            collected.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_update(
            "c1",
            memory="100",
            below_current=True,
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            current_memory_reader=lambda t: 500,
        )
        assert result.outcome == "success"
        assert len(collected) == 1

    def test_preview_below_current_refused(self) -> None:
        from topos.actions.update_ops import build_update_preview

        with pytest.raises(ValueError, match="below current"):
            build_update_preview(
                "c1", memory="100",
                current_memory_reader=lambda t: 500,
            )

    def test_preview_below_current_with_override_proceeds(self) -> None:
        from topos.actions.update_ops import build_update_preview
        plan = build_update_preview(
            "c1", memory="100",
            below_current=True,
            current_memory_reader=lambda t: 500,
        )
        assert plan.memory == 100
        assert plan.below_current is True


class TestP72UpdateCPUSValidation:
    """CPU validation - bounded positive float."""

    def test_valid_cpus(self) -> None:
        from topos.actions.update_ops import validate_cpus
        assert validate_cpus("1") == 1.0
        assert validate_cpus("2.5") == 2.5
        assert validate_cpus("0.5") == 0.5

    def test_invalid_cpus_rejected(self) -> None:
        from topos.actions.update_ops import validate_cpus
        with pytest.raises(ValueError):
            validate_cpus("-1")
        with pytest.raises(ValueError):
            validate_cpus("0")
        with pytest.raises(ValueError):
            validate_cpus("abc")
        with pytest.raises(ValueError):
            validate_cpus("")


class TestP72UpdateExecution:
    """Execute update - audit, success, fail-closed."""

    def test_update_success(self, tmp_path: Path) -> None:
        from topos.actions.execute import execute_update, ExecuteResult

        collected = []

        def runner(argv, *, timeout=30.0):
            collected.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_update(
            "c1",
            memory="512M",
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            current_memory_reader=_usage_1mib,
        )
        assert result.outcome == "success"
        assert len(collected) == 1

    def test_update_non_root_refused(self, tmp_path: Path) -> None:
        """Oracle 9."""
        from topos.actions.execute import execute_update
        result = execute_update(
            "c1", memory="512M",
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: False,
        )
        assert result.outcome == "refusal"

    def test_update_non_admin_refused(self, tmp_path: Path) -> None:
        """Oracle 9."""
        from topos.actions.execute import execute_update
        result = execute_update(
            "c1", memory="512M",
            admin=False, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
        )
        assert result.outcome == "refusal"

    def test_update_wrong_confirm_refused(self, tmp_path: Path) -> None:
        from topos.actions.execute import execute_update
        result = execute_update(
            "c1", memory="512M",
            admin=True, confirm="EXECUTE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
        )
        assert result.outcome == "refusal"

    def test_update_audit_fail_closed(self, tmp_path: Path) -> None:
        """Oracle 8: audit fail-closed inherited from P46."""
        from topos.actions.execute import execute_update, ExecuteResult

        audit_path = tmp_path / "audit.jsonl"

        def runner(argv, *, timeout=30.0):
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_update(
            "c1", memory="512M",
            admin=True, confirm="UPDATE",
            audit_path=audit_path,
            root_check=lambda: True,
            runner=runner,
            current_memory_reader=_usage_1mib,
        )
        assert result.outcome == "success"
        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
        post = json.loads(lines[1])
        assert post["kind"] == "docker-update"


class TestP72KillPlanPreviewIntegration:
    """Oracle 1: previewed argv == executed argv via build_admin_preview."""

    def test_kill_preview_via_build_admin_preview(self) -> None:
        from topos.actions.preview import build_admin_preview
        from topos.actions.kill_ops import KillPlan
        from topos.actions.catalog import DOCKER_EXECUTABLE

        result = build_admin_preview(
            "docker-kill", "my-container",
            admin=True, signal="TERM",
        )
        assert isinstance(result, KillPlan)
        assert list(result.argv) == [DOCKER_EXECUTABLE, "kill", "--signal", "TERM", "my-container"]

    def test_update_preview_via_build_admin_preview(self) -> None:
        from topos.actions.preview import build_admin_preview
        from topos.actions.update_ops import UpdatePlan
        from topos.actions.catalog import DOCKER_EXECUTABLE

        result = build_admin_preview(
            "docker-update", "my-container",
            admin=True, memory="512M",
            current_memory_reader=_usage_1mib,
        )
        assert isinstance(result, UpdatePlan)
        assert DOCKER_EXECUTABLE in result.argv
        assert "--memory" in result.argv

    def test_kill_preview_no_admin_returns_disabled(self) -> None:
        from topos.actions.preview import build_admin_preview, DisabledPlan
        result = build_admin_preview("docker-kill", "c1", admin=False)
        assert isinstance(result, DisabledPlan)

    def test_update_preview_no_admin_returns_disabled(self) -> None:
        from topos.actions.preview import build_admin_preview, DisabledPlan
        result = build_admin_preview("docker-update", "c1", admin=False)
        assert isinstance(result, DisabledPlan)


class TestP72AuditFailClosed:
    """Oracle 8: inherited P46 contract - audit fail-closed for new verbs."""

    def test_kill_audit_failure_blocks_execution(self, tmp_path: Path, monkeypatch) -> None:
        from topos.actions.execute import execute_kill, ExecuteResult
        import topos.actions.execute as execute_mod

        def failing_pre_audit(*a, **k):
            raise OSError("audit unavailable")

        monkeypatch.setattr(
            execute_mod,
            "_write_execution_audit_pre",
            failing_pre_audit,
        )

        called = []

        def runner(argv, *, timeout=30.0):
            called.append(True)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_kill(
            "docker-kill", "c1",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
        )
        assert result.outcome == "refusal"
        assert called == []

    def test_update_audit_failure_blocks_execution(self, tmp_path: Path, monkeypatch) -> None:
        from topos.actions.execute import execute_update, ExecuteResult
        import topos.actions.execute as execute_mod

        def failing_pre_audit(*a, **k):
            raise OSError("audit unavailable")

        monkeypatch.setattr(
            execute_mod,
            "_write_execution_audit_pre",
            failing_pre_audit,
        )

        called = []

        def runner(argv, *, timeout=30.0):
            called.append(True)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_update(
            "c1", memory="512M",
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            current_memory_reader=_usage_1mib,
        )
        assert result.outcome == "refusal"
        assert "audit" in result.stderr
        assert called == []


class TestP72ReviewRegressions:
    """Pass-#2 findings (P72-REVIEW.md). Each test fails against the merged-as-submitted code."""

    def test_execute_plan_refuses_kill_and_update_kinds(self, tmp_path: Path) -> None:
        """R1: the generic execute_plan() path must not be able to run kill/update.

        As submitted, the new kinds were in EXECUTION_ALLOWLIST, so
        execute_plan("docker-kill", ...) with the generic EXECUTE token ran the
        catalog's argument-free builder -- `docker kill <target>`, whose docker
        default is SIGKILL -- with no signal allowlist, no --force gate and no
        protected-entity check.
        """
        from topos.actions.execute import execute_plan, ExecuteResult

        for kind in ("docker-kill", "systemd-kill", "docker-update"):
            called = []

            def runner(argv, *, timeout=30.0):
                called.append(argv)
                return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

            target = "nginx.service" if kind == "systemd-kill" else "c1"
            result = execute_plan(
                kind, target,
                admin=True, confirm="EXECUTE",
                audit_path=tmp_path / "audit.jsonl",
                root_check=lambda: True,
                runner=runner,
            )
            assert result.outcome == "refusal", f"{kind} must not be executable via execute_plan"
            assert "allowlist" in result.stderr
            assert called == [], f"{kind} reached the runner through the generic path"

    def test_default_protected_check_refuses_config_protected_target(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """R2: contract 7 must hold with NO injected check -- the production default.

        As submitted, _default_protected_check() returned False unconditionally and
        the CLI passed no check, so no protected service was ever refused in
        production; only the injected test seam ever refused anything.
        """
        import topos.config as config_mod
        from topos.actions.execute import execute_kill, ExecuteResult
        from topos.config import ToposConfig

        monkeypatch.setattr(
            config_mod, "load",
            lambda path=None: ToposConfig(protected_services=("wings.service", "critical-svc")),
        )

        called = []

        def runner(argv, *, timeout=30.0):
            called.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_kill(
            "docker-kill", "critical-svc",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
        )
        assert result.outcome == "refusal"
        assert "protected" in result.stderr
        assert called == []

        # An unprotected target on the same config still proceeds.
        result_ok = execute_kill(
            "docker-kill", "ordinary-svc",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
        )
        assert result_ok.outcome == "success"
        assert len(called) == 1

    def test_protected_check_that_raises_refuses(self, tmp_path: Path) -> None:
        """R2b: a protected-check that cannot answer is a refusal, not a pass."""
        from topos.actions.execute import execute_kill, ExecuteResult

        called = []

        def broken_check(kind: str, target: str) -> bool:
            raise OSError("config unreadable")

        def runner(argv, *, timeout=30.0):
            called.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_kill(
            "docker-kill", "c1",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            protected_check=broken_check,
        )
        assert result.outcome == "refusal"
        assert "protected-service check failed" in result.stderr
        assert called == []

    def test_unverifiable_current_usage_refuses_memory_update(self, tmp_path: Path) -> None:
        """R3: contract 10 fail-closed -- an unreadable current usage refuses.

        As submitted the production reader could never resolve a Docker container
        name (validate_target forbids the '/' its only code path required), so it
        always returned None and the OOM guard never fired outside tests.
        """
        from topos.actions.execute import execute_update, ExecuteResult

        called = []

        def runner(argv, *, timeout=30.0):
            called.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_update(
            "c1", memory="512M",
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            current_memory_reader=lambda t: None,  # usage cannot be established
        )
        assert result.outcome == "refusal"
        assert "could not be established" in result.stderr
        assert called == []

        # The same override that covers a known breach covers an unverifiable usage.
        forced = execute_update(
            "c1", memory="512M",
            below_current=True,
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            current_memory_reader=lambda t: None,
        )
        assert forced.outcome == "success"
        assert len(called) == 1

    def test_cpus_only_update_needs_no_usage_reading(self, tmp_path: Path) -> None:
        """R3b: a --cpus-only update cannot OOM anything, so it is not gated on usage."""
        from topos.actions.execute import execute_update, ExecuteResult

        called = []

        def runner(argv, *, timeout=30.0):
            called.append(argv)
            return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

        result = execute_update(
            "c1", cpus="2",
            admin=True, confirm="UPDATE",
            audit_path=tmp_path / "audit.jsonl",
            root_check=lambda: True,
            runner=runner,
            current_memory_reader=lambda t: None,
        )
        assert result.outcome == "success"
        assert len(called) == 1

    def test_preview_systemd_target_names_set_property(self) -> None:
        """R4: the PREVIEW path must redirect a systemd target, not report it unverifiable.

        Ordering regression: the fail-closed current-usage check (F2) must not run
        before the systemd-target rejection, or the operator is told "usage could not
        be established" instead of "use topos action set-property" (contract 8).
        """
        from topos.actions.update_ops import build_update_preview

        with pytest.raises(ValueError, match="set-property"):
            build_update_preview("nginx.service", memory="512M")
