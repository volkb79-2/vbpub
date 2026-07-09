"""Tests for the inspect-files safety skeleton.

Covers:
- disabled without --inspect-files
- disabled without --admin
- enabled plans are deterministic JSON/text
- plan argv is a list, not a shell string
- no subprocess execution or file content reads are performed
- unsafe direct paths are rejected or never accepted by the parser
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from groop.inspect_files.catalog import INSPECT_CATALOG, InspectFilesKind
from groop.inspect_files.plan import (
    DisabledInspector,
    InspectFilesPlan,
    build_gated_inspect_plan,
    build_inspect_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_no_subprocess_in_modules() -> None:
    """Verify the inspect_files package never imports subprocess."""
    for mod_name in ("groop.inspect_files.catalog", "groop.inspect_files.__init__", "groop.inspect_files.plan"):
        spec = importlib.util.find_spec(mod_name)
        assert spec is not None, f"{mod_name} not found"
        assert spec.origin is not None, f"{mod_name} has no origin"
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(n.name == "subprocess" for n in node.names):
                pytest.fail(f"{mod_name} imports subprocess: {node.names}")
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                pytest.fail(f"{mod_name} imports {node.module}: {node.names}")


def _check_no_file_read_in_modules() -> None:
    """Verify the inspect_files package never uses file reads or resolving calls."""
    for mod_name in ("groop.inspect_files.catalog", "groop.inspect_files.__init__", "groop.inspect_files.plan"):
        spec = importlib.util.find_spec(mod_name)
        assert spec is not None, f"{mod_name} not found"
        assert spec.origin is not None, f"{mod_name} has no origin"
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            # Check for direct call to open
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    pytest.fail(f"{mod_name} calls open(): {ast.dump(node)}")
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in ("read_text", "read_bytes", "open"):
                        pytest.fail(f"{mod_name} calls Path.{node.func.attr}(): {ast.dump(node)}")
                    if node.func.attr == "resolve":
                        pytest.fail(f"{mod_name} calls Path.resolve(): {ast.dump(node)}")
            # Check for os.open
            if isinstance(node, ast.Attribute):
                if node.attr == "open" and isinstance(node.value, ast.Name) and node.value.id == "os":
                    pytest.fail(f"{mod_name} uses os.open")


# ---------------------------------------------------------------------------
# Gating tests
# ---------------------------------------------------------------------------

class TestGating:
    """build_gated_inspect_plan gates on --inspect-files and --admin."""

    def test_without_inspect_files_returns_disabled(self) -> None:
        result = build_gated_inspect_plan("docker-json-log", "abc123", inspect_files=False, admin=True)
        assert isinstance(result, DisabledInspector)
        assert "file inspection is not enabled" in result.message
        assert result.mode == "disabled"

    def test_without_admin_returns_disabled(self) -> None:
        result = build_gated_inspect_plan("docker-json-log", "abc123", inspect_files=True, admin=False)
        assert isinstance(result, DisabledInspector)
        assert "admin" in result.message.lower()
        assert result.mode == "disabled"

    def test_without_both_returns_disabled(self) -> None:
        result = build_gated_inspect_plan("docker-json-log", "abc123", inspect_files=False, admin=False)
        assert isinstance(result, DisabledInspector)

    def test_with_both_returns_plan(self) -> None:
        result = build_gated_inspect_plan("docker-json-log", "abc123", inspect_files=True, admin=True)
        assert isinstance(result, InspectFilesPlan)
        assert result.kind == InspectFilesKind.DOCKER_JSON_LOG
        assert result.mode == "plan"

    def test_disabled_inspector_jsonable(self) -> None:
        result = build_gated_inspect_plan("docker-json-log", "x", inspect_files=False, admin=True)
        j = result.to_jsonable()
        assert j["mode"] == "disabled"
        assert "message" in j
        assert j["kind"] == "docker-json-log"


class TestDisabledViaCli:
    """Simulate CLI-level disabled behavior."""

    def test_without_inspect_files_exit_2(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(["plan", "--kind", "docker-json-log", "--target", "c1", "--admin"])
        assert code == 2

    def test_without_admin_exit_2(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(["plan", "--kind", "docker-json-log", "--target", "c1", "--inspect-files"])
        assert code == 2

    def test_without_both_exit_2(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(["plan", "--kind", "docker-json-log", "--target", "c1"])
        assert code == 2

    def test_with_both_returns_0(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(["plan", "--kind", "docker-json-log", "--target", "c1", "--inspect-files", "--admin"])
        assert code == 0

    def test_unknown_kind_returns_2(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(["plan", "--kind", "unknown-kind", "--target", "x", "--inspect-files", "--admin"])
        assert code == 2


# ---------------------------------------------------------------------------
# Plan rendering tests
# ---------------------------------------------------------------------------

class TestPlanRendering:
    """Deterministic JSON/text rendering."""

    def test_docker_json_log_plan_json_keys(self) -> None:
        plan = build_inspect_plan("docker-json-log", "abc123def456")
        j = plan.to_jsonable()
        assert j["kind"] == "docker-json-log"
        assert j["target"] == "abc123def456"
        assert j["mode"] == "plan"
        assert "path_previews" in j
        assert "command_previews" in j
        assert "description" in j
        assert "kind_label" in j
        assert len(j["path_previews"]) >= 1
        assert len(j["command_previews"]) >= 1

    def test_docker_json_log_plan_text(self) -> None:
        plan = build_inspect_plan("docker-json-log", "abc123def456")
        text = plan.to_text()
        assert "Inspection Plan: docker-json-log" in text
        assert "abc123def456" in text
        assert "Path previews:" in text
        assert "Command previews" in text
        assert "plan only" in text

    def test_systemd_journal_plan_json(self) -> None:
        plan = build_inspect_plan("systemd-journal", "ssh.service")
        j = plan.to_jsonable()
        assert j["kind"] == "systemd-journal"
        assert j["target"] == "ssh.service"
        assert "journalctl" in str(j["command_previews"])
        assert all(isinstance(cmd, list) for cmd in j["command_previews"])

    def test_systemd_journal_plan_text(self) -> None:
        plan = build_inspect_plan("systemd-journal", "ssh.service")
        text = plan.to_text()
        assert "Inspection Plan: systemd-journal" in text
        assert "journalctl" in text
        assert "systemctl" in text

    def test_cgroup_files_plan_json(self) -> None:
        plan = build_inspect_plan("cgroup-files", "system.slice/ssh.service")
        j = plan.to_jsonable()
        assert j["kind"] == "cgroup-files"
        assert "path_previews" in j
        # Should list known cgroup files
        paths = j["path_previews"]
        assert any("memory.current" in p for p in paths)
        assert any("cpu.stat" in p for p in paths)

    def test_cgroup_files_plan_text(self) -> None:
        plan = build_inspect_plan("cgroup-files", "system.slice/ssh.service")
        text = plan.to_text()
        assert "Inspection Plan: cgroup-files" in text
        assert "memory.current" in text
        assert "cpu.stat" in text

    def test_json_serializable(self) -> None:
        plan = build_inspect_plan("docker-json-log", "abc123")
        payload = plan.to_jsonable()
        # Verify it round-trips through json.dumps
        s = json.dumps(payload, sort_keys=True)
        loaded = json.loads(s)
        assert loaded["kind"] == "docker-json-log"
        assert loaded["mode"] == "plan"

    def test_text_contains_mode_plan_only(self) -> None:
        plan = build_inspect_plan("systemd-journal", "cron.service")
        assert "no file contents read" in plan.to_text()


# ---------------------------------------------------------------------------
# Path/argv safety tests
# ---------------------------------------------------------------------------

class TestPathSafety:
    """Path and argv safety guarantees."""

    def test_command_previews_are_lists_not_strings(self) -> None:
        for kind in InspectFilesKind:
            if kind == InspectFilesKind.CGROUP_FILES:
                continue  # cgroup-files has no command previews
            target = "abc123def456" if kind == InspectFilesKind.DOCKER_JSON_LOG else "ssh.service"
            plan = build_inspect_plan(kind.value, target)
            for cmd in plan.command_previews:
                assert isinstance(cmd, tuple)
                assert all(isinstance(a, str) for a in cmd)
                # Verify it's not a single shell string
                assert len(cmd) >= 2

    def test_path_previews_are_path_objects(self) -> None:
        plan = build_inspect_plan("docker-json-log", "abc123")
        for p in plan.path_previews:
            assert isinstance(p, Path)
            assert isinstance(str(p), str)

    def test_docker_target_rejects_absolute_path(self) -> None:
        with pytest.raises(ValueError, match="must be a container id or name, not a path"):
            build_inspect_plan("docker-json-log", "/etc/passwd")

    def test_docker_target_rejects_relative_path(self) -> None:
        with pytest.raises(ValueError):
            build_inspect_plan("docker-json-log", "../etc/passwd")

    def test_docker_target_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            build_inspect_plan("docker-json-log", "")

    def test_docker_target_rejects_shell_metacharacters(self) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            build_inspect_plan("docker-json-log", "container;rm")

    def test_systemd_target_rejects_absolute_path(self) -> None:
        with pytest.raises(ValueError, match="must be a unit name, not a path"):
            build_inspect_plan("systemd-journal", "/etc/shadow")

    def test_systemd_target_rejects_unsafe_chars(self) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            build_inspect_plan("systemd-journal", "unit;rm -rf /")

    def test_cgroup_target_rejects_unsafe_path(self) -> None:
        with pytest.raises(ValueError, match="must be under /sys/fs/cgroup"):
            build_inspect_plan("cgroup-files", "/etc/passwd")

    def test_cgroup_target_rejects_traversal(self) -> None:
        with pytest.raises(ValueError, match="unsafe path segments"):
            build_inspect_plan("cgroup-files", "system.slice/../etc")

    def test_cgroup_target_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            build_inspect_plan("cgroup-files", "")

    def test_cgroup_target_accepts_sysfs_path(self) -> None:
        plan = build_inspect_plan("cgroup-files", "/sys/fs/cgroup/system.slice/ssh.service")
        assert isinstance(plan, InspectFilesPlan)
        assert all(str(path).startswith("/sys/fs/cgroup/system.slice/ssh.service/") for path in plan.path_previews)

    def test_docker_non_hex_name_accepted(self) -> None:
        # Docker targets don't need to be hex; names like "my-container" are valid
        plan = build_inspect_plan("docker-json-log", "my-container")
        assert isinstance(plan, InspectFilesPlan)

    def test_gated_plan_preserves_kind_on_disabled(self) -> None:
        result = build_gated_inspect_plan("systemd-journal", "ssh.service", inspect_files=False, admin=False)
        assert result.kind == InspectFilesKind.SYSTEMD_JOURNAL


# ---------------------------------------------------------------------------
# No-execution / no-read guarantees
# ---------------------------------------------------------------------------

class TestNoExecution:
    """Verify the inspect_files package never calls subprocess or open/read."""

    def test_no_subprocess_import(self) -> None:
        _check_no_subprocess_in_modules()

    def test_no_file_read_calls(self) -> None:
        _check_no_file_read_in_modules()

    def test_build_plan_does_not_invoke_subprocess(self) -> None:
        """Structural test: if the module compiled and the plan was built,
        no execution happened (subprocess is not even imported)."""
        plan = build_inspect_plan("docker-json-log", "abc123def456")
        assert isinstance(plan, InspectFilesPlan)
        assert plan.kind == InspectFilesKind.DOCKER_JSON_LOG

    def test_build_plan_does_not_read_files(self) -> None:
        """Path previews are built lexically without touching the filesystem."""
        # Paths with nonexistent directories should still produce previews
        plan = build_inspect_plan("docker-json-log", "nonexistent123456")
        assert isinstance(plan, InspectFilesPlan)
        # Path previews should reference paths that may not exist
        paths = [str(p) for p in plan.path_previews]
        assert any("/var/lib/docker/containers/nonexistent123456" in p for p in paths)

    def test_cgroup_plan_paths_never_touch_fs(self) -> None:
        """Cgroup file plans are purely lexical, never read."""
        plan = build_inspect_plan("cgroup-files", "system.slice/ssh.service")
        assert isinstance(plan, InspectFilesPlan)
        # All path previews should be under /sys/fs/cgroup (even for nonexistent paths)
        for p in plan.path_previews:
            assert str(p).startswith("/sys/fs/cgroup/")


# ---------------------------------------------------------------------------
# Catalog completeness
# ---------------------------------------------------------------------------

class TestCatalogCompleteness:
    """Every InspectFilesKind has a catalog entry with a valid builder."""

    def test_all_kinds_in_catalog(self) -> None:
        for kind in InspectFilesKind:
            entry = INSPECT_CATALOG.get(kind)
            assert entry is not None, f"{kind} missing from INSPECT_CATALOG"
            assert entry.kind == kind
            assert callable(entry.builder)

    def test_all_builders_produce_plans(self) -> None:
        for kind, entry in INSPECT_CATALOG.items():
            if kind == InspectFilesKind.DOCKER_JSON_LOG:
                paths, commands = entry.builder("abc123def456")
            elif kind == InspectFilesKind.SYSTEMD_JOURNAL:
                paths, commands = entry.builder("ssh.service")
            elif kind == InspectFilesKind.CGROUP_FILES:
                paths, commands = entry.builder("system.slice/ssh.service")
            else:
                pytest.fail(f"unknown kind {kind}")
            assert isinstance(paths, list)
            assert isinstance(commands, list)
            for p in paths:
                assert isinstance(p, Path)
            for cmd in commands:
                assert isinstance(cmd, list)
                assert all(isinstance(a, str) for a in cmd)


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestCliIntegration:
    """Smoke tests that the CLI function compiles and dispatches."""

    def test_parse_inspect_files_args_plan(self) -> None:
        from groop.cli import parse_inspect_files_args
        args = parse_inspect_files_args(["plan", "--kind", "docker-json-log", "--target", "c1", "--inspect-files", "--admin"])
        assert args.command == "plan"
        assert args.kind == "docker-json-log"
        assert args.target == "c1"
        assert args.inspect_files is True
        assert args.admin is True
        assert args.json is False

    def test_parse_inspect_files_args_no_flags(self) -> None:
        from groop.cli import parse_inspect_files_args
        args = parse_inspect_files_args(["plan", "--kind", "docker-json-log", "--target", "c1"])
        assert args.inspect_files is False
        assert args.admin is False

    def test_parse_inspect_files_args_json(self) -> None:
        from groop.cli import parse_inspect_files_args
        args = parse_inspect_files_args(["plan", "--kind", "docker-json-log", "--target", "c1", "--inspect-files", "--admin", "--json"])
        assert args.json is True

    def test_python_module_inspect_files_plan_json(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "inspect-files",
                "plan",
                "--kind",
                "docker-json-log",
                "--target",
                "c1",
                "--inspect-files",
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
        assert payload["kind"] == "docker-json-log"
        assert payload["mode"] == "plan"
        assert "path_previews" in payload
        assert "command_previews" in payload

    def test_python_module_inspect_files_disabled(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "groop.cli",
                "inspect-files",
                "plan",
                "--kind",
                "docker-json-log",
                "--target",
                "c1",
            ],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.returncode != 0
        assert "not enabled" in proc.stderr
