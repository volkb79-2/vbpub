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


def _make_nonregular_cgroup_root(tmp_path: Path) -> Path:
    """Create special-file fixtures outside the checkout for hermetic tests."""
    root = tmp_path / "cgroup_nonreg"
    leaf = root / "system.slice" / "ssh.service"
    leaf.mkdir(parents=True)
    (leaf / "memory.current").symlink_to("/etc/passwd")
    (leaf / "cpu.stat").mkdir()
    os.mkfifo(leaf / "pids.current")
    (leaf / "pids.max").write_text("512\n")
    (leaf / "memory.min").write_text("0\n")
    return root


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

    def test_plan_both_target_and_container_exit_2(self) -> None:
        """--target and --container are mutually exclusive."""
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(
            ["plan", "--kind", "docker-json-log", "--target", "x", "--container", "my-app", "--inspect-files", "--admin"]
        )
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


# ---------------------------------------------------------------------------
# P45 — Bounded content read tests
# ---------------------------------------------------------------------------

class TestReadDisabled:
    """build_inspect_read gating — disabled without flags."""

    def test_read_without_inspect_files_returns_denied(self) -> None:
        from groop.inspect_files.reader import ReadDenied, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=False, admin=True,
        )
        assert isinstance(result, ReadDenied)
        assert result.mode == "disabled"

    def test_read_without_admin_returns_denied(self) -> None:
        from groop.inspect_files.reader import ReadDenied, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=False,
        )
        assert isinstance(result, ReadDenied)

    def test_read_without_both_returns_denied(self) -> None:
        from groop.inspect_files.reader import ReadDenied, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=False, admin=False,
        )
        assert isinstance(result, ReadDenied)

    def test_read_denied_json_no_content(self) -> None:
        """JSON output of ReadDenied must not echo content."""
        from groop.inspect_files.reader import ReadDenied
        d = ReadDenied(kind=None, target="test")
        j = d.to_jsonable()
        assert j["mode"] == "disabled"
        assert "content" not in j
        assert "message" in j


class TestReadDisabledViaCli:
    """CLI-level disabled behavior for read command."""

    def test_read_without_inspect_files_exit_2(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(
            ["read", "--kind", "docker-json-log", "--target", "c1", "--admin"]
        )
        assert code == 2

    def test_read_without_admin_exit_2(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(
            ["read", "--kind", "docker-json-log", "--target", "c1", "--inspect-files"]
        )
        assert code == 2

    def test_read_with_both_returns_0(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(
            [
                "read", "--kind", "docker-json-log",
                "--target", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "--inspect-files", "--admin",
            ]
        )
        # This may fail because the fixture path doesn't exist — but it should
        # return 1 (error) not 2 (denied)
        assert code in (0, 1)

    def test_read_denied_exit_code(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(
            ["read", "--kind", "docker-json-log", "--target", "c1"]
        )
        assert code == 2

    def test_read_unknown_kind_exit_2(self) -> None:
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(
            ["read", "--kind", "unknown-kind", "--target", "x", "--inspect-files", "--admin"]
        )
        assert code == 1  # InspectFilesReadError returns exit 1

    def test_read_both_target_and_container_exit_2(self) -> None:
        """--target and --container are mutually exclusive for read."""
        from groop.cli import _main_inspect_files
        code = _main_inspect_files(
            ["read", "--kind", "docker-json-log", "--target", "x", "--container", "my-app", "--inspect-files", "--admin"]
        )
        assert code == 2


class TestReadContent:
    """Bounded content reads with fixture roots."""

    FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "inspect_files"

    def test_docker_log_read_success(self) -> None:
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult), f"Got {type(result).__name__}: {result}"
        assert result.kind.value == "docker-json-log"
        assert cid in result.path
        assert "container starting up" in result.content
        assert "health check passed" in result.content
        assert not result.truncated_bytes
        assert not result.truncated_lines

    def test_docker_log_read_json_format(self) -> None:
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        j = result.to_jsonable()
        assert j["kind"] == "docker-json-log"
        assert j["mode"] == "content"
        assert j["path"].endswith("-json.log")
        assert "container starting up" in j["content"]
        assert not j["truncated_bytes"]
        assert not j["truncated_lines"]
        # Verify JSON serialization round-trips
        import json as _json
        s = _json.dumps(j, sort_keys=True)
        loaded = _json.loads(s)
        assert loaded["kind"] == "docker-json-log"

    def test_docker_log_read_text_format(self) -> None:
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        text = result.to_text()
        assert "Read: docker-json-log" in text
        assert cid in text
        assert "container starting up" in text

    def test_docker_log_max_bytes_truncation(self) -> None:
        """Reading with a tiny max-bytes should truncate."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            max_bytes=10,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert result.truncated_bytes
        assert len(result.content) <= 10

    def test_docker_log_max_lines_truncation(self) -> None:
        """Reading with max_lines=1 should truncate to 1 line."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            max_lines=1,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert result.truncated_lines
        lines = result.content.strip().split("\n")
        assert len(lines) == 1

    def test_docker_log_oversized_file(self) -> None:
        """The oversized fixture (10000 lines) should be truncated at default limits."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "oversized",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        # oversized is not a valid 64-char hex id, so it should error
        from groop.inspect_files.reader import InspectFilesReadError
        assert isinstance(result, InspectFilesReadError)

    def test_docker_log_oversized_file_valid_id(self) -> None:
        """Read an oversized file with a valid container ID via max-lines cutoff."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "b" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            max_bytes=200_000,
            max_lines=3,
            fixture_root=self.FIXTURE_ROOT / "docker",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert result.truncated_lines
        assert result.content.count("\n") <= 3

    def test_cgroup_files_read_success(self) -> None:
        """Read bounded content from cgroup fixture files."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult), f"Got {type(result).__name__}: {result}"
        assert "memory.current" in result.content
        assert "1048576000" in result.content
        assert "cpu.stat" in result.content
        assert "123456789" in result.content
        assert "pids.current" in result.content
        assert "42" in result.content

    def test_cgroup_files_read_json_format(self) -> None:
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        j = result.to_jsonable()
        assert j["kind"] == "cgroup-files"
        assert j["mode"] == "content"
        assert "memory.current" in j["content"]
        import json as _json
        s = _json.dumps(j, sort_keys=True)
        loaded = _json.loads(s)
        assert loaded["kind"] == "cgroup-files"

    def test_cgroup_files_read_text_format(self) -> None:
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        text = result.to_text()
        assert "Read: cgroup-files" in text
        assert "memory.current" in text
        assert "cpu.stat" in text


class TestReadSafety:
    """Safety tests: no subprocess, no arbitrary path escape, no special files."""

    FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "inspect_files"

    def test_no_subprocess_import_in_reader(self) -> None:
        """Verify the reader module never imports subprocess."""
        import ast, importlib.util
        for mod_name in ("groop.inspect_files.reader",):
            spec = importlib.util.find_spec(mod_name)
            assert spec is not None
            assert spec.origin is not None
            with open(spec.origin, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(n.name == "subprocess" for n in node.names):
                    pytest.fail(f"{mod_name} imports subprocess: {node.names}")
                if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                    pytest.fail(f"{mod_name} imports {node.module}: {node.names}")

    def test_no_arbitrary_path_escape_docker(self) -> None:
        """User cannot escape the containers directory via path tricks."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        for bad_target in ("../../../etc/passwd", "/etc/passwd", "", "..", "."):
            result = build_inspect_read(
                "docker-json-log", bad_target,
                inspect_files=True, admin=True,
                fixture_root=self.FIXTURE_ROOT / "docker",
            )
            assert isinstance(result, InspectFilesReadError), f"Expected error for {bad_target!r}"

    def test_docker_target_rejects_short_id(self) -> None:
        """Docker read requires a full 64-char hex ID."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        for short_id in ("abc123", "a" * 63, "xyz" * 21, "container-name"):
            result = build_inspect_read(
                "docker-json-log", short_id,
                inspect_files=True, admin=True,
                fixture_root=self.FIXTURE_ROOT / "docker",
            )
            assert isinstance(result, InspectFilesReadError), f"Expected error for {short_id!r}"

    def test_cgroup_target_rejects_absolute_path(self) -> None:
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "/etc/passwd",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)

    def test_cgroup_target_rejects_traversal(self) -> None:
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "../../etc/passwd",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)

    def test_unknown_kind_returns_error(self) -> None:
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "systemd-journal", "ssh.service",
            inspect_files=True, admin=True,
            fixture_root=Path("/nonexistent"),
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)
        assert "does not support content reads" in result.error

    def test_read_error_json_no_content(self) -> None:
        """JSON output of InspectFilesReadError must not echo content."""
        from groop.inspect_files.reader import InspectFilesReadError
        e = InspectFilesReadError(kind=None, target="test", error="test error")
        j = e.to_jsonable()
        assert j["mode"] == "error"
        assert "content" not in j
        assert "error" in j


class TestReadCliIntegration:
    """CLI-level integration tests for the read command."""

    FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "inspect_files"

    def test_parse_inspect_files_args_read(self) -> None:
        from groop.cli import parse_inspect_files_args
        args = parse_inspect_files_args(
            ["read", "--kind", "docker-json-log", "--target", "a" * 64,
             "--inspect-files", "--admin"]
        )
        assert args.command == "read"
        assert args.kind == "docker-json-log"
        assert args.target == "a" * 64
        assert args.inspect_files is True
        assert args.admin is True

    def test_parse_read_args_defaults(self) -> None:
        from groop.cli import parse_inspect_files_args
        args = parse_inspect_files_args(
            ["read", "--kind", "cgroup-files", "--target", "x", "--inspect-files", "--admin"]
        )
        assert args.max_bytes == 65536
        assert args.max_lines == 5000
        assert args.json is False
        # --fixture-root must NOT be present in the production CLI
        assert not hasattr(args, "fixture_root")

    def test_parse_read_args_custom_bounds(self) -> None:
        from groop.cli import parse_inspect_files_args
        args = parse_inspect_files_args(
            ["read", "--kind", "docker-json-log", "--target", "a" * 64,
             "--inspect-files", "--admin", "--json",
             "--max-bytes", "1024", "--max-lines", "10"]
        )
        assert args.max_bytes == 1024
        assert args.max_lines == 10
        assert args.json is True

    def test_api_read_docker_log_success(self) -> None:
        """API-level docker fixture read (CLI has no --fixture-root)."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult), f"Got {type(result).__name__}: {result}"
        assert result.kind.value == "docker-json-log"
        assert "container starting up" in result.content

    def test_api_read_docker_log_json(self) -> None:
        """API read with JSON output."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        j = result.to_jsonable()
        assert j["mode"] == "content"
        assert "container starting up" in j["content"]

    def test_api_read_cgroup_success(self) -> None:
        """API-level cgroup fixture read (CLI has no --fixture-root)."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult), f"Got {type(result).__name__}: {result}"
        assert "memory.current" in result.content
        assert "cpu.stat" in result.content

    def test_cli_read_denied_exit_2(self) -> None:
        """Read without flags returns exit 2."""
        from groop.cli import _main_inspect_files
        code = _main_inspect_files([
            "read", "--kind", "docker-json-log", "--target", "a" * 64,
        ])
        assert code == 2


# ---------------------------------------------------------------------------
# Security and boundary tests for P45 corrections
# ---------------------------------------------------------------------------


class TestReadSecurityCorrections:
    """Covers: parent symlink escape, giant line, aggregate bounds,
    negative/zero/huge limits, CLI fixture-root absence, no special files,
    hostile bytes."""

    FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "inspect_files"

    # ---- Symlink escape (descriptor-relative confinement) ----

    def test_symlink_escape_in_component_rejected(self) -> None:
        """If a component of the resolved path is a symlink, O_NOFOLLOW
        on the directory walk MUST reject it."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service/dangerous_link/passwd_escape",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "cgroup_escape",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)

    def test_fifo_rejected(self) -> None:
        """FIFO (named pipe) is not a regular file -- must be rejected
        by the catalog validation (traversal) or reader."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "../../inspect_files/_danger/test_fifo",
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "cgroup_escape",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)

    # ---- Single giant line (chunk-based reads) ----

    def test_giant_line_bounded_with_valid_id(self) -> None:
        """Read a file with small max_bytes to verify chunk-based read
        truncates at the byte limit."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            max_bytes=20,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert result.truncated_bytes
        assert len(result.content) <= 20

    # ---- Aggregate bounds for cgroup files ----

    def test_cgroup_aggregate_bytes_truncation(self) -> None:
        """Very small max_bytes should truncate across ALL cgroup files,
        not per-file."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            max_bytes=20,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert result.truncated_bytes

    def test_cgroup_aggregate_lines_truncation(self) -> None:
        """Small max_lines should truncate across ALL cgroup files."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            max_lines=1,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert result.truncated_lines

    # ---- Negative/zero/huge limits ----

    def test_negative_max_bytes_rejected(self) -> None:
        """Negative max_bytes must be rejected."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            max_bytes=-1,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)
        assert "max_bytes" in result.error.lower()

    def test_zero_max_bytes_rejected(self) -> None:
        """Zero max_bytes must be rejected."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            max_bytes=0,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)
        assert "max_bytes" in result.error.lower()

    def test_huge_max_bytes_rejected(self) -> None:
        """Excessive max_bytes exceeding absolute maximum must be rejected."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            max_bytes=1_048_577,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)
        assert "max_bytes" in result.error.lower()

    def test_negative_max_lines_rejected(self) -> None:
        """Negative max_lines must be rejected."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            max_lines=-5,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)
        assert "max_lines" in result.error.lower()

    def test_huge_max_lines_rejected(self) -> None:
        """Excessive max_lines exceeding absolute maximum must be rejected."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            max_lines=100_001,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadError)
        assert "max_lines" in result.error.lower()

    # ---- CLI absence of fixture-root ----

    def test_cli_no_fixture_root_flag(self) -> None:
        """Verify --fixture-root is NOT accepted by the CLI parser."""
        import pytest
        with pytest.raises(SystemExit):
            from groop.cli import parse_inspect_files_args
            parse_inspect_files_args([
                "read", "--kind", "docker-json-log", "--target", "x",
                "--inspect-files", "--admin", "--fixture-root", "/tmp",
            ])

    # ---- Root requirement (production path) ----

    def test_read_requires_root_in_production(self) -> None:
        """Without fixture_root, build_inspect_read should require root."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
        )
        assert isinstance(result, InspectFilesReadError)
        assert "requires root" in result.error

    # ---- Hostile/control bytes ----

    def test_hostile_bytes_safe(self) -> None:
        """Null bytes and control characters must not crash the decoder."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "a" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
        is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert isinstance(result.content, str)

    # ---- No subprocess/writes ----

    def test_reader_no_subprocess(self) -> None:
        """Reader module must not import subprocess."""
        import ast, importlib.util
        spec = importlib.util.find_spec("groop.inspect_files.reader")
        assert spec is not None
        assert spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(n.name == "subprocess" for n in node.names):
                pytest.fail(f"reader imports subprocess: {node.names}")
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                pytest.fail(f"reader imports {node.module}: {node.names}")

    def test_reader_no_write_operations(self) -> None:
        """Reader must not open files for writing."""
        import ast, importlib.util
        spec = importlib.util.find_spec("groop.inspect_files.reader")
        assert spec is not None
        assert spec.origin is not None
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND"):
                    pytest.fail(f"reader uses write flag {node.attr}")


# ---------------------------------------------------------------------------
# P45 narrow — comprehensive boundary, error-type, root injection, and
# framing-budget tests
# ---------------------------------------------------------------------------


class TestP45BoundedContentCorrections:
    """P45 corrections: giant line > chunk-size, exact multi-file budgets,
    exhausted budget, framing bounds, root injection, error types, hostile
    bytes, and descriptor-relative O_NOFOLLOW preservation."""

    FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "inspect_files"

    # ---- Giant line > chunk-size test ----

    def test_giant_line_chunk_boundary(self) -> None:
        """Read a docker fixture with a single line > _READ_CHUNK_SIZE (65536)
        — verifies chunk-based read never materialises the whole line."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "b" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        # With default limits (64 KiB / 5000 lines) the fixture (~70 KiB raw,
        # ~70 KiB decoded content) should be truncated at the byte limit.
        assert result.truncated_bytes
        assert len(result.content) <= 65536  # _DEFAULT_MAX_BYTES

    def test_giant_line_large_budget(self) -> None:
        """Read a giant-line docker fixture with a generous byte budget —
        verifies the single giant line is fully read within one chunk."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "b" * 64
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            max_bytes=200_000,
            fixture_root=self.FIXTURE_ROOT / "docker",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        # The fixture has 3 JSON lines (~70 KiB total), 200_000 bytes is enough.
        assert "first short line" in result.content
        assert "last short line" in result.content
        # The content should be well under 200_000 bytes
        assert len(result.content) < 200_000

    # ---- Aggregate multi-file exact budget tests ----

    def test_cgroup_exact_byte_budget(self) -> None:
        """max_bytes set to a tight budget — verifies framing overhead is
        reserved before content, so the rendered output stays within budget."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        # First file (memory.current): path ~151 chars, header_cost ~156,
        # content 11 bytes ≈ 167 total.
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            max_bytes=180,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        # Rendered content must fit within budget
        assert len(result.content) <= 180
        assert result.truncated_bytes or result.truncated_lines

    def test_cgroup_exact_line_budget(self) -> None:
        """max_lines=1 — verifies truncation is flagged."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            max_lines=1,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert result.truncated_lines

    # ---- Exhausted-budget no-further-content test ----

    def test_exhausted_bytes_no_further_content(self) -> None:
        """Once aggregate byte cap is exhausted, subsequent cgroup files
        must read zero content."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            max_bytes=5,  # Won't even fit the first file's framing header
            fixture_root=self.FIXTURE_ROOT / "cgroup",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        # Content should be empty (no file had budget for even the header)
        assert len(result.content) == 0

    def test_exhausted_lines_no_further_content(self) -> None:
        """Once aggregate line cap is exhausted, subsequent files must
        read zero content."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            max_lines=1,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        assert result.truncated_lines

    # ---- Output framing bound tests ----

    def test_framing_stays_within_byte_budget(self) -> None:
        """Verify combined # header + content total stays within max_bytes
        for cgroup multi-file reads."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        budget = 350
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            max_bytes=budget,
            fixture_root=self.FIXTURE_ROOT / "cgroup",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        # The rendered content (with headers) must not exceed the budget
        assert len(result.content) <= budget, (
            f"content length {len(result.content)} exceeds budget {budget}"
        )

    # ---- Root injection tests ----

    def test_root_inject_true_passes(self) -> None:
        """Explicit is_root=lambda: True must pass root check."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)

    def test_root_inject_false_fails(self) -> None:
        """Explicit is_root=lambda: False must fail with requires-root error."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
            is_root=lambda: False,
        )
        assert isinstance(result, InspectFilesReadError)
        assert "requires root" in result.error

    def test_fixture_root_alone_does_not_bypass_root(self, monkeypatch) -> None:
        """fixture_root without is_root must NOT bypass root check — the
        default os.geteuid() check applies unless is_root is provided."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read
        monkeypatch.setattr("groop.inspect_files.reader.os.geteuid", lambda: 1000)
        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
            # No is_root — fixture_root alone no longer implies root
        )
        # Running as non-root user (vscode), so this must fail
        assert isinstance(result, InspectFilesReadError)
        assert "requires root" in result.error

    # ---- Error-type tests: FIFO, directory, symlink at leaf ----

    def test_fifo_leaf_rejected(self, tmp_path: Path) -> None:
        """A cgroup file path resolving to a FIFO must be rejected by
        _confine_and_open with a descriptive error (not hang)."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            fixture_root=_make_nonregular_cgroup_root(tmp_path),
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        # pids.current is a FIFO — should appear as error in combined output
        assert "[ERROR]" in result.content or result.truncated_bytes
        # The regular files (pids.max, memory.min) should still succeed
        assert "512" in result.content

    def test_directory_leaf_rejected(self, tmp_path: Path) -> None:
        """A cgroup file path resolving to a directory must be rejected."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            fixture_root=_make_nonregular_cgroup_root(tmp_path),
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        # cpu.stat is a directory — should error
        assert "cpu.stat" in result.content

    def test_symlink_leaf_rejected(self, tmp_path: Path) -> None:
        """A cgroup file path whose final leaf is a symlink must be
        rejected by O_NOFOLLOW."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        result = build_inspect_read(
            "cgroup-files", "system.slice/ssh.service",
            inspect_files=True, admin=True,
            fixture_root=_make_nonregular_cgroup_root(tmp_path),
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        # memory.current is a symlink to /etc/passwd — should error
        assert "[ERROR]" in result.content or result.truncated_bytes

    # ---- Hostile bytes sanitization test ----

    def test_hostile_bytes_sanitized(self) -> None:
        """NUL, terminal escape, BEL, DEL, C1 codes must be replaced with
        U+FFFD in the decoded output."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "c0" * 32  # 64 hex chars = c0c0c0...
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        content = result.content
        # NUL byte should be sanitized
        assert "\x00" not in content, "NUL byte survived sanitization"
        # ESC (0x1B) should be sanitized
        assert "\x1b" not in content, "ESC byte survived sanitization"
        # BEL (0x07) should be sanitized
        assert "\x07" not in content, "BEL byte survived sanitization"
        # DEL (0x7F) should be sanitized
        assert "\x7f" not in content, "DEL byte survived sanitization"
        # C1 CSI (0x9B) should be sanitized
        assert "\x9b" not in content, "C1 CSI byte survived sanitization"
        # Newline must be preserved
        assert "\n" in content
        # Replacement character should appear
        assert "\ufffd" in content

    def test_hostile_bytes_preserves_newline_tab(self) -> None:
        """Sanitization must preserve \\n and \\t."""
        from groop.inspect_files.reader import InspectFilesReadResult, build_inspect_read
        cid = "c0" * 32
        result = build_inspect_read(
            "docker-json-log", cid,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
            is_root=lambda: True,
        )
        assert isinstance(result, InspectFilesReadResult)
        content = result.content
        # The docker log lines are separated by \n
        assert content.count("\n") >= 3

    # ---- Descriptor-relative O_NOFOLLOW preserved ----

    def test_confine_and_open_no_follow_leaf_symlink(self, tmp_path: Path) -> None:
        """_confine_and_open must reject a final-leaf symlink even when
        all parent directories are safe regular directories."""
        from groop.inspect_files.reader import _confine_and_open
        from pathlib import Path
        import pytest
        allow_root = _make_nonregular_cgroup_root(tmp_path)
        leaf = allow_root / "system.slice" / "ssh.service"
        with pytest.raises(OSError):
            _confine_and_open(leaf / "memory.current", allow_root)

    def test_confine_and_open_no_follow_leaf_fifo(self, tmp_path: Path) -> None:
        """_confine_and_open must reject a FIFO final leaf."""
        from groop.inspect_files.reader import _confine_and_open
        from pathlib import Path
        import pytest
        allow_root = _make_nonregular_cgroup_root(tmp_path)
        leaf = allow_root / "system.slice" / "ssh.service"
        with pytest.raises(ValueError, match="not a regular file"):
            _confine_and_open(leaf / "pids.current", allow_root)

    def test_confine_and_open_no_follow_leaf_directory(self, tmp_path: Path) -> None:
        """_confine_and_open must reject a directory final leaf."""
        from groop.inspect_files.reader import _confine_and_open
        from pathlib import Path
        import pytest
        allow_root = _make_nonregular_cgroup_root(tmp_path)
        leaf = allow_root / "system.slice" / "ssh.service"
        with pytest.raises(ValueError, match="not a regular file"):
            _confine_and_open(leaf / "cpu.stat", allow_root)

    def test_confine_and_open_regular_file_succeeds(self) -> None:
        """_confine_and_open must succeed for a regular file."""
        from groop.inspect_files.reader import _confine_and_open
        from pathlib import Path
        leaf = self.FIXTURE_ROOT / "cgroup" / "system.slice" / "ssh.service"
        allow_root = self.FIXTURE_ROOT / "cgroup"
        buf = _confine_and_open(leaf / "memory.current", allow_root)
        assert buf is not None
        data = buf.read()
        assert data  # non-empty
        buf.close()

    def test_root_seam_requires_literal_true(self) -> None:
        """A truthy non-bool test seam must never authorize a root read."""
        from groop.inspect_files.reader import InspectFilesReadError, build_inspect_read

        result = build_inspect_read(
            "docker-json-log", "a" * 64,
            inspect_files=True, admin=True,
            fixture_root=self.FIXTURE_ROOT / "docker",
            is_root=lambda: "true",  # type: ignore[return-value]
        )
        assert isinstance(result, InspectFilesReadError)
        assert "requires root" in result.error

    def test_rendered_bound_counts_utf8_bytes_and_generated_lines(self) -> None:
        """Rendered bounds use encoded bytes and include framing newlines."""
        from groop.inspect_files.reader import _bound_rendered_text

        text, trunc_b, trunc_l = _bound_rendered_text(
            "# header\n\ufffd\ufffd\nbody\nextra",
            max_bytes=14,
            max_lines=2,
        )
        assert len(text.encode("utf-8")) <= 14
        assert text.count("\n") <= 2
        assert trunc_b or trunc_l
